'use strict';

const { app, BrowserWindow, ipcMain, protocol, net } = require('electron');
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

let orbWindow = null;
let brainProcess = null;
let brainRestarts = 0;
let shuttingDown = false;
let pendingId = 0;
const pending = new Map();
const served = { orbScript: false };
// How many messages of each kind the brain has sent, so a probe can tell
// what really arrived instead of what was asked for.
const brainCounts = { total: 0, level: 0, mic_state: 0, transcript: 0, plan: 0 };

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
    alwaysOnTop: true,
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
  orbWindow.on('closed', () => {
    orbWindow = null;
    stopBrain();
  });
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
  } else if (PROBE_MODE) {
    await runProbe();
  }
});

app.on('window-all-closed', () => {
  stopBrain();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createOrbWindow();
  }
});

ipcMain.handle('brain:send', async (_event, payload) => {
  return sendToBrain(payload);
});

// -- probe mode ---------------------------------------------------------------
//
// `electron . --probe` runs the real app, looks inside the renderer and
// prints one JSON line.  The smoke test asserts on that line, so a broken
// window or a dead renderer can no longer pass as a healthy app.

const ORB_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const brand = document.getElementById("brand");
  const phases = document.querySelectorAll(".pill button");
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

const TALK_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const cap = orb && orb.shadowRoot ? orb.shadowRoot.querySelector(".cap") : null;
  const active = document.querySelector('.pill button[aria-pressed="true"]');
  return {
    phase: active ? active.textContent.trim().toLowerCase() : null,
    captionText: cap ? cap.textContent : "",
    orbState: orb ? orb.getAttribute("state") : null
  };
})()`;

async function runTalkProbe() {
  // The whole point of the app: click the orb, it hears you, it answers.
  await waitForLoad(orbWindow);
  await sleep(2500);

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
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const state = await orbWindow.webContents.executeJavaScript(TALK_PROBE);
    phase = state.phase;
    captionText = state.captionText;
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
    ok: Boolean(
      orbLevel === 0.77 &&
      micLevels > 0 &&
      micClosedEvents > 0 &&
      planEvents > 0 &&
      phase === 'speaking' &&
      captionText.trim().length > 0
    ),
  };
  console.log(`RASGULLA_TALK ${JSON.stringify(talk)}`);
  stopBrain();
  app.exit(talk.ok ? 0 : 1);
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
    probe.orb.orbCursor === 'pointer' &&
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
