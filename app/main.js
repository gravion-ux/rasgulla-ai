'use strict';

const { app, BrowserWindow, ipcMain, protocol, net, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');

const { resolveVendorPath } = require('./lib/vendor-paths');
const { WINDOW_SIZES } = require('./lib/layout');

const APP_DIR = __dirname;
const PROJECT_ROOT = path.dirname(APP_DIR);
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
const BRAIN_ENTRY = path.join(APP_DIR, 'brain', 'brain.py');
const PROBE_MODE = process.argv.includes('--probe');
const LIVE_PROBE_MODE = process.argv.includes('--probe-live');
const SHOT_MODE = process.argv.includes('--probe-shot');
const TALK_MODE = process.argv.includes('--probe-talk');
const WAKE_MODE = process.argv.includes('--probe-wake');
const CAPTION_MODE = process.argv.includes('--probe-caption');

let orbWindow = null;
let brainProcess = null;
let brainRestarts = 0;
let shuttingDown = false;
let pendingId = 0;
const pending = new Map();
const served = { orbScript: false };
// The tray she lives in when the window is closed, and whether she is pinned
// on top of the other windows.  She starts pinned, the way she always was.
let tray = null;
let pinned = true;
// The last greeting the brain sent, so a probe can look at what she said.
let lastGreeting = null;
// The last complaint the brain made, and the tail of its log, so a probe
// can say what went wrong instead of only what did not happen.
let lastBrainError = '';
const brainLogLines = [];
// How many messages of each kind the brain has sent, so a probe can tell
// what really arrived instead of what was asked for.
const brainCounts = {
  total: 0,
  level: 0,
  mic_state: 0,
  mic_state_wake: 0,
  transcript: 0,
  plan: 0,
  wake: 0,
  greeting: 0,
};

// The scheme has to be declared before the app is ready.  It is deliberately
// not a "standard" scheme, so Chromium hands the request over as the exact
// string that was typed instead of a normalised host and path.
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'orb-vendor',
    privileges: {
      standard: false,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
    },
  },
]);

function contentTypeFor(file) {
  const extension = path.extname(file).toLowerCase();
  if (extension === '.js') return 'text/javascript';
  if (extension === '.css') return 'text/css';
  if (extension === '.png') return 'image/png';
  if (extension === '.svg') return 'image/svg+xml';
  if (extension === '.json') return 'application/json';
  if (extension === '.html') return 'text/html';
  return 'application/octet-stream';
}

function registerVendorProtocol() {
  // protocol.handle only exists once the session is ready, so this has to
  // happen inside whenReady.  Returning a { path } response does not deliver
  // bytes to the page, so the file is fetched through net instead.
  protocol.handle('orb-vendor', async (request) => {
    const resolved = resolveVendorPath(request.url, {
      appDir: APP_DIR,
      projectRoot: PROJECT_ROOT,
    });
    if (!resolved.ok) {
      return new Response(resolved.reason, {
        status: 404,
        headers: { 'content-type': 'text/plain' },
      });
    }
    const file = resolved.file;
    if (file.endsWith('speaking-orb.js')) served.orbScript = true;
    try {
      const response = await net.fetch(pathToFileURL(file).toString());
      const body = await response.arrayBuffer();
      return new Response(body, {
        status: response.status,
        headers: {
          'content-type': response.headers.get('content-type') || contentTypeFor(file),
          'access-control-allow-origin': '*',
          'cache-control': 'no-cache',
        },
      });
    } catch (error) {
      return new Response('unreadable', {
        status: 500,
        headers: { 'content-type': 'text/plain' },
      });
    }
  });
}

function spawnBrain() {
  if (brainProcess) {
    try { brainProcess.kill(); } catch (_) { /* already gone */ }
    brainProcess = null;
  }
  const child = spawn(VENV_PYTHON, [BRAIN_ENTRY], {
    cwd: APP_DIR,
    // stderr has to be piped, not inherited: with 'inherit' the stream is
    // null and the log handler below would throw on its first chunk.
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, RASGULLA_API_KEY: process.env.RASGULLA_API_KEY || '' },
  });
  brainProcess = child;

  let buffer = '';
  child.stdout.on('data', (chunk) => {
    buffer += chunk.toString();
    let index;
    while ((index = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch (_) { continue; }
      const kind = message && message.type;
      brainCounts.total += 1;
      if (typeof brainCounts[kind] === 'number') brainCounts[kind] += 1;
      if (kind === 'mic_state' && message.state === 'wake') brainCounts.mic_state_wake += 1;
      if (kind === 'greeting') lastGreeting = message;
      if (kind === 'error') lastBrainError = String(message.message || '');
      const id = message.id;
      const resolver = id != null ? pending.get(id) : null;
      if (resolver) {
        pending.delete(id);
        resolver.resolve(message);
      }
      // Every message from the brain also reaches the renderer, which is the
      // UI channel for transcripts, levels and plans.
      if (orbWindow && orbWindow.webContents) {
        orbWindow.webContents.send('brain:event', message);
      }
    }
  });
  child.stderr.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (!text) return;
    brainLogLines.push(text);
    if (brainLogLines.length > 40) brainLogLines.shift();
    if (orbWindow && orbWindow.webContents) {
      orbWindow.webContents.send('brain:log', { message: text });
    }
  });
  child.on('error', (error) => {
    if (orbWindow && orbWindow.webContents) {
      orbWindow.webContents.send('brain:log', {
        message: `the brain could not be started: ${error.message}`,
      });
    }
  });
  child.on('spawn', () => {
    // The renderer says hello itself when the app starts, so this is only
    // for a brain that came back after a crash.  The renderer then opens the
    // microphone again and the wake word works after a restart too.
    if (brainRestarts === 0) return;
    sendToBrain({ command: 'hello' })
      .then((reply) => {
        if (orbWindow && orbWindow.webContents) {
          orbWindow.webContents.send('brain:event', reply);
        }
      })
      .catch(() => { /* the brain is not ready yet; it will say hello later */ });
  });
  child.on('exit', () => {
    brainProcess = null;
    for (const [, resolver] of pending) {
      resolver.reject(new Error('the brain stopped'));
    }
    pending.clear();
    if (orbWindow && orbWindow.webContents) {
      orbWindow.webContents.send('brain:event', { type: 'brain_down' });
    }
    if (!shuttingDown && brainRestarts < 2) {
      brainRestarts += 1;
      setTimeout(spawnBrain, 1000);
    }
  });
}

function sendToBrain(payload) {
  return new Promise((resolve, reject) => {
    if (!brainProcess || brainProcess.stdin === null || brainProcess.stdin.destroyed) {
      reject(new Error('the brain is not running'));
      return;
    }
    const id = ++pendingId;
    // A reply that never arrives must not leave the interface waiting.
    const timer = setTimeout(() => {
      if (pending.delete(id)) {
        reject(new Error('the brain did not answer in time'));
      }
    }, 30000);
    pending.set(id, {
      resolve: (message) => { clearTimeout(timer); resolve(message); },
      reject: (error) => { clearTimeout(timer); reject(error); },
    });
    payload.id = id;
    brainProcess.stdin.write(JSON.stringify(payload) + '\n');
  });
}

function createOrbWindow() {
  orbWindow = new BrowserWindow({
    width: WINDOW_SIZES.compact.width,
    height: WINDOW_SIZES.compact.height,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: pinned,
    skipTaskbar: true,
    resizable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(APP_DIR, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  orbWindow.loadFile(path.join(APP_DIR, 'renderer', 'index.html'));
  orbWindow.once('ready-to-show', () => {
    orbWindow.show();
  });
  orbWindow.on('close', (event) => {
    // Closing her window does not kill her: she goes into the tray and keeps
    // listening.  Only a real quit closes the window for good.
    if (shuttingDown) return;
    event.preventDefault();
    orbWindow.hide();
    ensureTray();
  });
  orbWindow.on('closed', () => {
    orbWindow = null;
    stopBrain();
  });
}

// The tray icon is drawn pixel by pixel, so the app carries no image file.
function orbIcon() {
  const size = 32;
  const buffer = Buffer.alloc(size * size * 4);
  const middle = (size - 1) / 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - middle;
      const dy = y - middle;
      const distance = Math.sqrt(dx * dx + dy * dy);
      const index = (y * size + x) * 4;
      let alpha = 0;
      if (distance > size * 0.3 && distance < size * 0.47) alpha = 255; // the ring
      else if (distance < size * 0.18) alpha = 210; // the core
      buffer[index] = 36; // blue
      buffer[index + 1] = 165; // green
      buffer[index + 2] = 245; // red
      buffer[index + 3] = alpha;
    }
  }
  return nativeImage.createFromBitmap(buffer, { width: size, height: size });
}

function ensureTray() {
  if (tray) return tray;
  tray = new Tray(orbIcon());
  tray.setToolTip('Rasgulla');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Show Rasgulla', click: () => showOrb() },
      { type: 'separator' },
      { label: 'Quit', click: () => quitApp() },
    ])
  );
  tray.on('click', () => showOrb());
  return tray;
}

function showOrb() {
  if (!orbWindow || orbWindow.isDestroyed()) {
    createOrbWindow();
    return;
  }
  orbWindow.show();
  orbWindow.focus();
}

function quitApp() {
  shuttingDown = true;
  stopBrain();
  if (tray) {
    tray.destroy();
    tray = null;
  }
  app.quit();
}

function stopBrain() {
  shuttingDown = true;
  if (brainProcess) {
    try { brainProcess.kill(); } catch (_) { /* already gone */ }
    brainProcess = null;
  }
}

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

app.whenReady().then(async () => {
  registerVendorProtocol();
  spawnBrain();
  createOrbWindow();
  if (LIVE_PROBE_MODE) {
    await runLiveProbe();
  } else if (SHOT_MODE) {
    await runShotProbe();
  } else if (TALK_MODE) {
    await runTalkProbe();
  } else if (WAKE_MODE) {
    await runWakeProbe();
  } else if (CAPTION_MODE) {
    await runCaptionProbe();
  } else if (PROBE_MODE) {
    await runProbe();
  }
});

app.on('window-all-closed', () => {
  // With the tray she stays alive even when her window is gone.
  if (!tray && process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  shuttingDown = true;
  stopBrain();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createOrbWindow();
  }
});

ipcMain.handle('brain:send', async (_event, payload) => {
  return sendToBrain(payload);
});

// The window controls.  The close button puts her in the tray, and the pin
// decides whether she floats above the other windows.
ipcMain.handle('app:hide', async () => {
  if (orbWindow && !orbWindow.isDestroyed()) {
    orbWindow.hide();
    ensureTray();
  }
  return true;
});

ipcMain.handle('app:toggle-pin', async () => {
  pinned = !pinned;
  if (orbWindow && !orbWindow.isDestroyed()) orbWindow.setAlwaysOnTop(pinned);
  return pinned;
});

ipcMain.handle('app:is-pinned', async () => pinned);

// -- probe mode ---------------------------------------------------------------
//
// `electron . --probe` runs the real app, looks inside the renderer and
// prints one JSON line.  The smoke test asserts on that line, so a broken
// window or a dead renderer can no longer pass as a healthy app.

const ORB_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const brand = document.getElementById("brand");
  const phases = document.querySelectorAll(".pill button");
  const pin = document.getElementById("btnPin");
  return {
    elementDefined: Boolean(customElements.get("speaking-orb")),
    rendererRan: window.__rasgullaRendererReady === true,
    shadowRoot: Boolean(orb && orb.shadowRoot),
    brandText: brand ? brand.textContent.trim() : null,
    phaseCount: phases.length,
    phaseLabels: Array.from(phases).map((button) => button.textContent.trim()),
    hasInput: Boolean(document.getElementById("entry")),
    hasPanel: Boolean(document.getElementById("panel")),
    hasStrip: Boolean(document.querySelector(".strip")),
    hasPlanCard: Boolean(document.getElementById("plan-card")),
    hasConfirmCard: Boolean(document.getElementById("confirm-card")),
    hasReplyArea: Boolean(document.getElementById("reply")),
    hasPinButton: Boolean(pin),
    orbCursor: orb ? getComputedStyle(orb).cursor : null
  };
})()`;

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForLoad(win) {
  if (!win) return;
  if (win.webContents.isLoading()) {
    await new Promise((resolve) => {
      win.webContents.once('did-finish-load', resolve);
      win.webContents.once('did-fail-load', resolve);
    });
  }
}

const LIVE_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const cap = orb && orb.shadowRoot ? orb.shadowRoot.querySelector(".cap") : null;
  const audio = document.getElementById("voice");
  const brand = document.getElementById("brand");
  const phases = document.querySelectorAll(".pill button");
  return {
    captionText: cap ? cap.textContent : null,
    orbState: orb ? orb.getAttribute("state") : null,
    audioIsBlob: audio ? audio.src.startsWith("blob:") : false,
    audioPaused: audio ? audio.paused : null,
    audioDuration: audio ? audio.duration : null,
    brandText: brand ? brand.textContent.trim() : null,
    phaseCount: phases.length,
    hasInput: Boolean(document.getElementById("entry")),
    hasPanel: Boolean(document.getElementById("panel"))
  };
})()`;

async function runLiveProbe() {
  console.log('RASGULLA_LIVE_STEP asking');
  await waitForLoad(orbWindow);
  await sleep(2500);
  await orbWindow.webContents.executeJavaScript(`(() => {
    window.__rasgullaAsk("In one short sentence, what is a desk companion?");
    return true;
  })()`);

  let state = null;
  let audioEverPlayed = false;
  let captionSeen = '';
  for (let i = 0; i < 150; i++) {
    await sleep(1000);
    state = await orbWindow.webContents.executeJavaScript(LIVE_PROBE);
    if (state && state.audioPaused === false) audioEverPlayed = true;
    if (state && state.captionText && state.captionText.trim().length > 0) {
      captionSeen = state.captionText.trim();
      if (captionSeen.length > 3) break;
    }
  }
  console.log('RASGULLA_LIVE_STEP done');

  const live = {
    ok: Boolean(
      state &&
      audioEverPlayed &&
      state.audioIsBlob &&
      captionSeen.length > 0
    ),
    captionText: captionSeen,
    audioEverPlayed: audioEverPlayed,
    state: state,
    errors: brainCounts.error,
    lastError: lastBrainError,
    brainLog: brainLogLines.slice(-8),
  };
  console.log(`RASGULLA_LIVE ${JSON.stringify(live)}`);
  stopBrain();
  app.exit(live.ok ? 0 : 1);
}

async function runShotProbe() {
  // A picture of the real face, for the record.
  await waitForLoad(orbWindow);
  await sleep(3000);
  const shotIndex = process.argv.indexOf('--shot');
  const target = shotIndex > 0 && process.argv[shotIndex + 1]
    ? process.argv[shotIndex + 1]
    : path.join(process.cwd(), 'rasgulla-orb.png');
  const image = await orbWindow.webContents.capturePage();
  require('fs').writeFileSync(target, image.toPNG());
  console.log(`RASGULLA_SHOT ${target}`);
  const layout = await orbWindow.webContents.executeJavaScript(`(() => {
    const pick = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const orb = document.getElementById("orb");
    let painted = 0;
    try {
      const canvas = orb.shadowRoot.querySelector(".orb canvas");
      const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 3; i < data.length; i += 4) { if (data[i] > 0) painted += 1; }
    } catch (error) { painted = -1; }
    return {
      viewport: { w: innerWidth, h: innerHeight },
      brand: pick("#brand"),
      pill: pick(".pill"),
      orb: pick("#orb"),
      close: pick("#btnClose"),
      orbPaintedPixels: painted
    };
  })()`);
  console.log(`RASGULLA_SHOT_LAYOUT ${JSON.stringify(layout)}`);
  stopBrain();
  app.exit(0);
}

const CAPTION_PROBE = `(() => {
  const host = document.getElementById("orb");
  const root = host ? host.shadowRoot : null;
  const cap = root ? root.querySelector(".cap") : null;
  const orbDiv = root ? root.querySelector(".orb") : null;
  const reply = document.getElementById("reply");
  if (!host || !cap) return { error: "no caption" };
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: +r.x.toFixed(1), y: +r.y.toFixed(1),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      top: +r.top.toFixed(1), bottom: +r.bottom.toFixed(1),
      left: +r.left.toFixed(1), right: +r.right.toFixed(1)
    };
  };
  const hostRect = rect(host);
  const capRect = rect(cap);
  const orbRect = rect(orbDiv);
  const replyRect = reply ? rect(reply) : null;
  return {
    host: hostRect,
    cap: capRect,
    orb: orbRect,
    reply: replyRect,
    capText: cap.textContent.trim(),
    capFontSize: getComputedStyle(cap).fontSize,
    hostContain: getComputedStyle(host).contain,
    capScrollHeight: cap.scrollHeight,
    capClientHeight: cap.clientHeight,
    capOverflowY: getComputedStyle(cap).overflowY,
    insideHost: capRect.top >= hostRect.top - 0.5 && capRect.bottom <= hostRect.bottom + 0.5,
    overflowBy: +(capRect.bottom - hostRect.bottom).toFixed(1),
    replyText: reply ? reply.textContent.trim() : "",
    replyScrollHeight: reply ? reply.scrollHeight : 0,
    replyClientHeight: reply ? reply.clientHeight : 0,
    replyOverflowY: reply ? getComputedStyle(reply).overflowY : "",
    replyInsideWindow: replyRect
      ? replyRect.top >= -0.5 && replyRect.bottom <= innerHeight + 0.5
      : false,
    viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio }
  };
})()`;

// A real answer, taken from an actual reply, so the probe measures what the
// user really reads rather than a toy line.
const CAPTION_ANSWER =
  "Here is the Python function to reverse a string:\n\n" +
  "def reverse_string(s):\n" +
  "    return s[::-1]\n\n" +
  "s[::-1] walks the string backwards, so the original one is left alone.";

// A dump is longer than the space under the face, so it has to scroll rather
// than be painted into the clip and lost.
const CAPTION_DUMP = Array.from(
  { length: 24 },
  (_, index) => `line ${String(index + 1).padStart(2, "0")}: the quick brown fox jumps over the lazy dog`
).join("\n");

async function measureCaption(caseName, line) {
  // The reply is handed to the renderer the way the brain hands it over, so
  // the probe measures the real path and not a hand-made DOM.
  const driven = await orbWindow.webContents.executeJavaScript(`(() => {
    if (typeof window.__rasgullaMessage !== "function") {
      return { ok: false, why: "the renderer has no message hook" };
    }
    window.__rasgullaMessage({
      type: "plan",
      reply: ${JSON.stringify(line)},
      caption: ${JSON.stringify(line)},
      speech: "",
      audio: "",
      words: [],
      mime: ""
    });
    return { ok: true };
  })()`);
  if (!driven || !driven.ok) {
    return { error: (driven && driven.why) || "the renderer did not take the reply" };
  }
  await sleep(900);

  const layout = await orbWindow.webContents.executeJavaScript(CAPTION_PROBE);
  if (layout.error) return { error: layout.error };

  const image = await orbWindow.webContents.capturePage();
  const size = image.getSize();
  const bitmap = image.toBitmap();
  // A picture of the real reply, for the record.
  const shotIndex = process.argv.indexOf('--shot');
  if (caseName === 'answer' && shotIndex > 0 && process.argv[shotIndex + 1]) {
    require('fs').writeFileSync(process.argv[shotIndex + 1], image.toPNG());
    console.log(`RASGULLA_SHOT ${process.argv[shotIndex + 1]}`);
  }
  const scale = size.width / layout.viewport.w;
  const brightIn = (rect) => {
    if (!rect) return 0;
    const x0 = Math.max(0, Math.round(rect.left * scale));
    const x1 = Math.min(size.width, Math.round(rect.right * scale));
    const y0 = Math.max(0, Math.round(rect.top * scale));
    const y1 = Math.min(size.height, Math.round(rect.bottom * scale));
    let bright = 0;
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const i = (y * size.width + x) * 4;
        // the bitmap is BGRA: the text is a pale near-white on a dark page
        if (bitmap[i + 2] > 110 && bitmap[i + 1] > 110 && bitmap[i] > 110) bright += 1;
      }
    }
    return bright;
  };
  return {
    ...layout,
    case: caseName,
    drivenText: line,
    scale: +scale.toFixed(3),
    imageSize: size,
    brightPixels: brightIn(layout.cap),
    replyBrightPixels: brightIn(layout.reply)
  };
}

// The caption has to be visible, not merely present in the DOM.  The old
// probe only read cap.textContent, so a caption painted outside the host's
// clip still passed.  This one measures the box and counts the pixels that
// were actually drawn.
async function runCaptionProbe() {
  await waitForLoad(orbWindow);
  await sleep(3000);

  const answer = await measureCaption("answer", CAPTION_ANSWER);
  const dump = answer.error ? { error: answer.error } : await measureCaption("dump", CAPTION_DUMP);

  const caption = { answer, dump };
  console.log(`RASGULLA_CAPTION ${JSON.stringify(caption)}`);
  stopBrain();
  app.exit(answer.error || dump.error ? 1 : 0);
}

const TALK_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const cap = orb && orb.shadowRoot ? orb.shadowRoot.querySelector(".cap") : null;
  const active = document.querySelector('.pill button[aria-pressed="true"]');
  const audio = document.getElementById("voice");
  return {
    phase: active ? active.textContent.trim().toLowerCase() : null,
    captionText: cap ? cap.textContent : "",
    orbState: orb ? orb.getAttribute("state") : null,
    audioIsBlob: audio ? audio.src.startsWith("blob:") : false,
    audioPaused: audio ? audio.paused : null
  };
})()`;

async function runTalkProbe() {
  // The whole point of the app: click the orb, it hears you, it answers.
  await waitForLoad(orbWindow);
  await sleep(2500);

  // She sleeps with the microphone open, so the listener is closed first.
  // That way the levels that follow can only come from the click.
  await sendToBrain({ command: 'listen', on: false });
  await sleep(1500);

  // One link: a level event from the brain has to move the orb.
  orbWindow.webContents.send('brain:event', { type: 'level', value: 0.77 });
  await sleep(600);
  const orbLevel = await orbWindow.webContents.executeJavaScript(
    '(() => { const o = document.getElementById("orb"); return o ? Number(o.getAttribute("level")) : null; })()'
  );

  // Two links: the click has to open the real microphone, which then starts
  // sending levels of its own.
  const levelsBefore = brainCounts.level;
  await orbWindow.webContents.executeJavaScript(
    '(() => { document.getElementById("orb").click(); return true; })()'
  );
  await sleep(4000);
  const micLevels = brainCounts.level - levelsBefore;

  // Close the microphone again and wait for the brain to say it is idle.
  await sendToBrain({ command: 'mic', on: false });
  await sleep(1500);
  const micClosedEvents = brainCounts.mic_state;

  // Three links: a final transcript has to start a real answer.
  const plansBefore = brainCounts.plan;
  orbWindow.webContents.send('brain:event', {
    type: 'transcript',
    text: 'what time is it',
    final: true,
  });
  let phase = null;
  let captionText = '';
  let audioIsBlob = false;
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const state = await orbWindow.webContents.executeJavaScript(TALK_PROBE);
    phase = state.phase;
    captionText = state.captionText;
    audioIsBlob = state.audioIsBlob;
    if (phase === 'speaking' && captionText.trim().length > 0) break;
  }
  const planEvents = brainCounts.plan - plansBefore;

  const talk = {
    orbLevel: orbLevel,
    micLevels: micLevels,
    micClosedEvents: micClosedEvents,
    planEvents: planEvents,
    phase: phase,
    captionText: captionText,
    audioIsBlob: audioIsBlob,
    ok: Boolean(
      orbLevel === 0.77 &&
      micLevels > 0 &&
      micClosedEvents > 0 &&
      planEvents > 0 &&
      phase === 'speaking' &&
      captionText.trim().length > 0 &&
      audioIsBlob
    ),
  };
  console.log(`RASGULLA_TALK ${JSON.stringify(talk)}`);
  stopBrain();
  app.exit(talk.ok ? 0 : 1);
}

const WAKE_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const cap = orb && orb.shadowRoot ? orb.shadowRoot.querySelector(".cap") : null;
  const active = document.querySelector('.pill button[aria-pressed="true"]');
  const reply = document.getElementById("reply");
  const audio = document.getElementById("voice");
  const log = Array.isArray(window.__rasgullaCaptionLog) ? window.__rasgullaCaptionLog : [];
  return {
    phase: active ? active.textContent.trim().toLowerCase() : null,
    captionText: cap ? cap.textContent : "",
    captionSeen: log.join(" | "),
    replyText: reply ? reply.textContent.trim() : "",
    audioTime: audio ? Number(audio.currentTime) || 0 : 0,
    micMode: typeof window.__rasgullaGetMicMode === "function" ? window.__rasgullaGetMicMode() : null
  };
})()`;

async function runWakeProbe() {
  // She has to sleep with the microphone open, wake up when she is called,
  // and greet you out loud.
  await waitForLoad(orbWindow);
  await sleep(3000);

  // She opens the microphone by herself and falls asleep in it.
  let micLevels = 0;
  let firstLevelAfterMs = -1;
  const listeningAt = Date.now();
  for (let i = 0; i < 60; i++) {
    await sleep(500);
    micLevels = brainCounts.level;
    if (micLevels > 0) {
      firstLevelAfterMs = Date.now() - listeningAt;
      break;
    }
  }
  const micStateWake = brainCounts.mic_state_wake;
  // The microphone has to keep up with the room, not only send one level.
  const levelsAtStart = brainCounts.level;
  await sleep(3000);
  const levelRate = brainCounts.level - levelsAtStart;

  // The wake word, or two claps, has to wake her up and make her greet you.
  const wakesBefore = brainCounts.wake;
  const greetingsBefore = brainCounts.greeting;
  await sendToBrain({ command: 'wake', source: 'voice' });

  let phase = null;
  let spoke = false;
  let captionText = '';
  let captionSeen = '';
  let replyText = '';
  let audioTime = 0;
  let micMode = null;
  // The greeting is over in a second or two, so the probe looks often and
  // remembers what it saw, instead of hoping to catch one lucky second.
  for (let i = 0; i < 160; i++) {
    await sleep(250);
    const state = await orbWindow.webContents.executeJavaScript(WAKE_PROBE);
    phase = state.phase;
    if (state.phase === 'speaking') spoke = true;
    captionText = state.captionText;
    captionSeen = state.captionSeen;
    replyText = state.replyText;
    audioTime = Math.max(audioTime, state.audioTime);
    micMode = state.micMode;
    if (captionSeen.trim() && replyText.trim()) break;
  }

  const wake = {
    micLevels: micLevels,
    levelRate: levelRate,
    firstLevelAfterMs: firstLevelAfterMs,
    micStateWake: micStateWake,
    wakeEvents: brainCounts.wake - wakesBefore,
    greetingEvents: brainCounts.greeting - greetingsBefore,
    greetingAudio: Boolean(lastGreeting && lastGreeting.audio),
    greetingSpeech: lastGreeting ? String(lastGreeting.speech || '') : '',
    phase: phase,
    spoke: spoke,
    captionText: captionText,
    captionSeen: captionSeen,
    replyText: replyText,
    audioTime: audioTime,
    micMode: micMode,
    errors: brainCounts.error,
    lastError: lastBrainError,
    brainLog: brainLogLines.slice(-8),
    ok: Boolean(
      micLevels > 0 &&
      micStateWake >= 1 &&
      brainCounts.wake - wakesBefore >= 1 &&
      brainCounts.greeting - greetingsBefore >= 1 &&
      lastGreeting &&
      lastGreeting.audio &&
      spoke &&
      captionSeen.trim().length > 0 &&
      replyText.trim().length > 0
    ),
  };
  console.log(`RASGULLA_WAKE ${JSON.stringify(wake)}`);
  stopBrain();
  app.exit(wake.ok ? 0 : 1);
}

async function runProbe() {
  console.log('RASGULLA_PROBE_STEP windows');
  await waitForLoad(orbWindow);
  await sleep(3000);
  console.log('RASGULLA_PROBE_STEP loaded');

  const probe = {
    windows: { orb: Boolean(orbWindow), count: BrowserWindow.getAllWindows().length },
    orb: {},
    brain: {},
    assets: { orbScript: served.orbScript },
    layout: {},
  };

  if (orbWindow) {
    probe.orb = await orbWindow.webContents.executeJavaScript(ORB_PROBE);
    const contentBefore = orbWindow.getContentSize();
    probe.layout = {
      contentBefore: { width: contentBefore[0], height: contentBefore[1] },
    };
  }

  console.log('RASGULLA_PROBE_STEP brain');
  try {
    const reply = await sendToBrain({ command: 'hello' });
    probe.brain = {
      alive: true,
      type: reply.type || null,
      configError: reply.config_error || '',
      keyFound: Boolean(reply.key_found),
      voiceId: reply.voice_id || null,
    };
  } catch (error) {
    probe.brain = {
      alive: false,
      type: null,
      configError: String((error && error.message) || error),
      keyFound: false,
      voiceId: null,
    };
  }

  // The orb talks by opening the microphone, so that command has to work.
  try {
    const opened = await sendToBrain({ command: 'mic', on: true });
    const closed = await sendToBrain({ command: 'mic', on: false });
    probe.brain.micAccepted = opened.type === 'ok' && closed.type === 'ok';
  } catch (error) {
    probe.brain.micAccepted = false;
  }
  console.log('RASGULLA_PROBE_STEP done');

  // The window controls: she starts pinned on top, the pin button takes that
  // away and gives it back, and closing her window puts her in the tray
  // instead of killing her.
  const pinBefore = orbWindow.isAlwaysOnTop();
  await orbWindow.webContents.executeJavaScript(
    'document.getElementById("btnPin").click()'
  );
  await sleep(500);
  const pinAfter = orbWindow.isAlwaysOnTop();
  const pinPressed = await orbWindow.webContents.executeJavaScript(
    'document.getElementById("btnPin").getAttribute("aria-pressed")'
  );
  await orbWindow.webContents.executeJavaScript(
    'document.getElementById("btnPin").click()'
  );
  await sleep(500);
  const pinBack = orbWindow.isAlwaysOnTop();

  await orbWindow.webContents.executeJavaScript(
    'document.getElementById("btnClose").click()'
  );
  await sleep(800);
  const hidden = orbWindow.isVisible() === false;
  const trayAfterClose = Boolean(tray);
  orbWindow.show();
  await sleep(300);

  probe.window = {
    pinBefore: pinBefore,
    pinAfter: pinAfter,
    pinBack: pinBack,
    pinPressed: pinPressed,
    hiddenOnClose: hidden,
    trayReady: trayAfterClose,
    trayAtStart: Boolean(tray),
  };

  probe.ok = Boolean(
    probe.windows.orb &&
    probe.windows.count === 1 &&
    probe.orb.elementDefined &&
    probe.orb.rendererRan &&
    probe.orb.shadowRoot &&
    probe.orb.brandText === 'Rasgulla' &&
    probe.orb.phaseCount === 3 &&
    probe.orb.phaseLabels.join(',') === 'Listening,Thinking,Speaking' &&
    probe.orb.hasInput === false &&
    probe.orb.hasPanel === false &&
    probe.orb.hasStrip === false &&
    probe.orb.hasPlanCard === false &&
    probe.orb.hasConfirmCard === false &&
    probe.orb.hasReplyArea === true &&
    probe.orb.hasPinButton === true &&
    probe.orb.orbCursor === 'pointer' &&
    probe.window.pinBefore === true &&
    probe.window.pinAfter === false &&
    probe.window.pinBack === true &&
    probe.window.pinPressed === 'false' &&
    probe.window.hiddenOnClose === true &&
    probe.window.trayReady === true &&
    probe.brain.alive &&
    probe.brain.type === 'ready' &&
    probe.brain.configError === '' &&
    probe.brain.voiceId === 'en-IN-NeerjaExpressiveNeural' &&
    probe.brain.micAccepted === true &&
    probe.assets.orbScript &&
    probe.layout.contentBefore &&
    probe.layout.contentBefore.width === WINDOW_SIZES.compact.width &&
    probe.layout.contentBefore.height === WINDOW_SIZES.compact.height
  );

  console.log(`RASGULLA_PROBE ${JSON.stringify(probe)}`);
  stopBrain();
  app.exit(probe.ok ? 0 : 1);
}
