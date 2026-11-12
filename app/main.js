'use strict';

const { app, BrowserWindow, ipcMain, shell, protocol, net, screen } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');

const { isAllowedExternalUrl } = require('./lib/url-guard');
const { resolveVendorPath } = require('./lib/vendor-paths');
const { WINDOW_SIZES } = require('./lib/layout');

const APP_DIR = __dirname;
const PROJECT_ROOT = path.dirname(APP_DIR);
const VENV_PYTHON = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');
const BRAIN_ENTRY = path.join(APP_DIR, 'brain', 'brain.py');
const PROBE_MODE = process.argv.includes('--probe');
const LIVE_PROBE_MODE = process.argv.includes('--probe-live');

let orbWindow = null;
let petWindow = null;
let brainProcess = null;
let brainRestarts = 0;
let shuttingDown = false;
let expanded = false;
let pendingId = 0;
const pending = new Map();
const served = { orbScript: false, petFrame: false };

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
    if (file.includes(`${path.sep}pet-frames${path.sep}`)) served.petFrame = true;
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
      const id = message.id;
      const resolver = id != null ? pending.get(id) : null;
      if (resolver) {
        pending.delete(id);
        resolver.resolve(message);
      }
      // Every message from the brain also reaches the renderer, which is the
      // UI channel for plans, transcripts and levels.
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

function createPetWindow() {
  petWindow = new BrowserWindow({
    width: 200,
    height: 240,
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
  petWindow.loadFile(path.join(APP_DIR, 'renderer', 'pet.html'));
  petWindow.once('ready-to-show', () => {
    petWindow.show();
    const display = screen.getPrimaryDisplay().workAreaSize;
    petWindow.setPosition(display.width - 240, display.height - 280);
  });
  petWindow.on('closed', () => {
    petWindow = null;
  });
}

function stopBrain() {
  shuttingDown = true;
  if (brainProcess) {
    try { brainProcess.kill(); } catch (_) { /* already gone */ }
    brainProcess = null;
  }
}

function setExpanded(next) {
  expanded = Boolean(next);
  const size = expanded ? WINDOW_SIZES.expanded : WINDOW_SIZES.compact;
  if (orbWindow) {
    // The content size is what the spec describes.  Windows adds a one pixel
    // invisible border around a frameless window, so the outer size is always
    // a touch larger.
    orbWindow.setContentSize(size.width, size.height);
    orbWindow.webContents.send('window:layout', { expanded });
  }
  return { expanded };
}

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

app.whenReady().then(async () => {
  registerVendorProtocol();
  spawnBrain();
  createOrbWindow();
  createPetWindow();
  if (LIVE_PROBE_MODE) {
    await runLiveProbe();
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

ipcMain.handle('brain:spawn', () => {
  spawnBrain();
  return { ok: true };
});

ipcMain.handle('shell:open', async (_event, url) => {
  if (!isAllowedExternalUrl(url)) {
    return { ok: false };
  }
  await shell.openExternal(url);
  return { ok: true };
});

ipcMain.handle('window:toggle-pin', (_event) => {
  if (!orbWindow) return { pinned: false };
  const next = !orbWindow.isAlwaysOnTop();
  orbWindow.setAlwaysOnTop(next);
  return { pinned: next };
});

ipcMain.handle('window:toggle-expand', (_event) => {
  return setExpanded(!expanded);
});

ipcMain.handle('window:show-pet', (_event) => {
  if (petWindow) petWindow.show();
  return { ok: true };
});

ipcMain.handle('window:hide-pet', (_event) => {
  if (petWindow) petWindow.hide();
  return { ok: true };
});

ipcMain.handle('pet:move', (_event, { dx, dy } = {}) => {
  if (!petWindow) return { ok: false };
  const [x, y] = petWindow.getPosition();
  const stepX = Number(dx) || 0;
  const stepY = Number(dy) || 0;
  petWindow.setPosition(x + stepX, y + stepY);
  return { ok: true };
});

ipcMain.handle('orb:focus', (_event) => {
  if (orbWindow) {
    orbWindow.show();
    orbWindow.focus();
  }
  return { ok: true };
});

ipcMain.on('orb:state', (_event, state) => {
  if (petWindow && petWindow.webContents) {
    petWindow.webContents.send('pet:state', { state: String(state || '') });
  }
});

// -- probe mode ---------------------------------------------------------------
//
// `electron . --probe` runs the real app, looks inside both renderers and
// prints one JSON line.  The smoke test asserts on that line, so a broken
// window or a dead renderer can no longer pass as a healthy app.

const ORB_PROBE = `(() => {
  const orb = document.getElementById("orb");
  const panel = document.getElementById("panel");
  return {
    elementDefined: Boolean(customElements.get("speaking-orb")),
    rendererRan: window.__rasgullaRendererReady === true,
    panelHiddenAtStart: Boolean(panel && panel.classList.contains("hidden")),
    panelDisplay: panel ? getComputedStyle(panel).display : null,
    shadowRoot: Boolean(orb && orb.shadowRoot),
    transcriptChildren: document.getElementById("transcript")
      ? document.getElementById("transcript").children.length
      : -1
  };
})()`;

const PET_PROBE = `(() => {
  const canvas = document.getElementById("sprite");
  const state = window.__rasgullaPet || {};
  let painted = 0;
  try {
    const context = canvas.getContext("2d");
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 0) painted += 1;
    }
  } catch (error) {
    painted = -1;
  }
  return {
    framesLoaded: state.framesLoaded || 0,
    frameErrors: state.frameErrors || 0,
    setName: state.setName || null,
    canvasNonTransparentPixels: painted
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
  const transcript = document.getElementById("transcript");
  return {
    captionText: cap ? cap.textContent : null,
    orbState: orb ? orb.getAttribute("state") : null,
    audioIsBlob: audio ? audio.src.startsWith("blob:") : false,
    audioPaused: audio ? audio.paused : null,
    audioDuration: audio ? audio.duration : null,
    transcriptChildren: transcript ? transcript.children.length : -1,
    lastLine: transcript && transcript.lastElementChild
      ? transcript.lastElementChild.textContent : null,
    panelHidden: document.getElementById("panel")
      ? document.getElementById("panel").classList.contains("hidden")
      : null
  };
})()`;

async function runLiveProbe() {
  console.log('RASGULLA_LIVE_STEP asking');
  await waitForLoad(orbWindow);
  await sleep(2500);
  await orbWindow.webContents.executeJavaScript(`(() => {
    const entry = document.getElementById("entry");
    entry.value = "In one short sentence, what is a desk companion?";
    document.getElementById("btnSend").click();
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
      state.transcriptChildren >= 2 &&
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

async function runProbe() {
  console.log('RASGULLA_PROBE_STEP windows');
  await waitForLoad(orbWindow);
  await waitForLoad(petWindow);
  await sleep(3000);
  console.log('RASGULLA_PROBE_STEP loaded');

  const probe = {
    windows: { orb: Boolean(orbWindow), pet: Boolean(petWindow) },
    orb: {},
    pet: {},
    brain: {},
    assets: { orbScript: served.orbScript, petFrame: served.petFrame },
    layout: {},
  };

  if (orbWindow) {
    probe.orb = await orbWindow.webContents.executeJavaScript(ORB_PROBE);
    const before = orbWindow.getSize();
    const contentBefore = orbWindow.getContentSize();
    setExpanded(true);
    await sleep(700);
    const after = orbWindow.getSize();
    const contentAfter = orbWindow.getContentSize();
    const panelVisible = await orbWindow.webContents.executeJavaScript(
      '(() => { const p = document.getElementById("panel"); return p ? getComputedStyle(p).display : null; })()'
    );
    setExpanded(false);
    await sleep(400);
    probe.layout = {
      before: { width: before[0], height: before[1] },
      after: { width: after[0], height: after[1] },
      contentBefore: { width: contentBefore[0], height: contentBefore[1] },
      contentAfter: { width: contentAfter[0], height: contentAfter[1] },
      panelDisplayWhenExpanded: panelVisible,
    };
  }

  if (petWindow) {
    probe.pet = await petWindow.webContents.executeJavaScript(PET_PROBE);
  }

  // The orb state drives the pet: a busy orb has to switch the overlay to
  // the work frames, not keep playing the idle ones.
  if (orbWindow && petWindow) {
    await orbWindow.webContents.executeJavaScript(
      'window.__rasgullaSetOrbState("thinking")'
    );
    await sleep(3000);
    const busyPet = await petWindow.webContents.executeJavaScript(PET_PROBE);
    probe.petStateSwitch = {
      setName: busyPet.setName,
      framesLoaded: busyPet.framesLoaded,
      frameErrors: busyPet.frameErrors,
    };
    await orbWindow.webContents.executeJavaScript(
      'window.__rasgullaSetOrbState("listening")'
    );
  }

  console.log('RASGULLA_PROBE_STEP brain');
  try {
    const reply = await sendToBrain({ command: 'hello' });
    probe.brain = {
      alive: true,
      type: reply.type || null,
      configError: reply.config_error || '',
      keyFound: Boolean(reply.key_found),
    };
  } catch (error) {
    probe.brain = {
      alive: false,
      type: null,
      configError: String((error && error.message) || error),
      keyFound: false,
    };
  }
  console.log('RASGULLA_PROBE_STEP done');

  probe.ok = Boolean(
    probe.windows.orb &&
    probe.windows.pet &&
    probe.orb.elementDefined &&
    probe.orb.rendererRan &&
    probe.orb.panelHiddenAtStart &&
    probe.orb.shadowRoot &&
    probe.pet.framesLoaded >= 12 &&
    probe.pet.frameErrors === 0 &&
    probe.pet.canvasNonTransparentPixels > 0 &&
    probe.brain.alive &&
    probe.brain.type === 'ready' &&
    probe.brain.configError === '' &&
    probe.assets.orbScript &&
    probe.assets.petFrame &&
    probe.petStateSwitch &&
    probe.petStateSwitch.setName === 'work' &&
    probe.petStateSwitch.framesLoaded >= 12 &&
    probe.layout.after &&
    probe.layout.contentAfter &&
    probe.layout.contentAfter.width === WINDOW_SIZES.expanded.width &&
    probe.layout.contentAfter.height === WINDOW_SIZES.expanded.height
  );

  console.log(`RASGULLA_PROBE ${JSON.stringify(probe)}`);
  stopBrain();
  app.exit(probe.ok ? 0 : 1);
}
