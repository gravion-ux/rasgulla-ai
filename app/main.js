'use strict';

const { app, BrowserWindow, ipcMain, shell, protocol, screen } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const APP_DIR = __dirname;
const VENV_PYTHON = path.join(
  'C:',
  'Users',
  'priya',
  'Documents',
  'Rasgulla',
  '.venv',
  'Scripts',
  'python.exe'
);
const BRAIN_ENTRY = path.join(APP_DIR, 'brain', 'brain.py');

let orbWindow = null;
let petWindow = null;
let brainProcess = null;
let pendingId = 0;
const pending = new Map();

function spawnBrain() {
  if (brainProcess) {
    try { brainProcess.kill(); } catch (_) {}
    brainProcess = null;
  }
  brainProcess = spawn(VENV_PYTHON, [BRAIN_ENTRY], {
    cwd: APP_DIR,
    stdio: ['pipe', 'pipe', 'inherit'],
    env: { ...process.env, RASGULLA_API_KEY: process.env.RASGULLA_API_KEY || '' },
  });
  let buffer = '';
  brainProcess.stdout.on('data', (chunk) => {
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
      } else if (orbWindow && orbWindow.webContents) {
        orbWindow.webContents.send('brain:event', message);
      }
    }
  });
  brainProcess.stderr.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (text) {
      if (orbWindow && orbWindow.webContents) {
        orbWindow.webContents.send('brain:log', { message: text });
      }
    }
  });
  brainProcess.on('exit', () => {
    brainProcess = null;
    if (orbWindow && orbWindow.webContents) {
      orbWindow.webContents.send('brain:event', { type: 'brain_down' });
    }
  });
}

function sendToBrain(payload) {
  return new Promise((resolve, reject) => {
    if (!brainProcess || brainProcess.stdin === null || brainProcess.stdin.destroyed) {
      reject(new Error('brain not running'));
      return;
    }
    const id = ++pendingId;
    pending.set(id, { resolve, reject });
    payload.id = id;
    brainProcess.stdin.write(JSON.stringify(payload) + '\n');
  });
}

function createOrbWindow() {
  orbWindow = new BrowserWindow({
    width: 440,
    height: 660,
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
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
    if (brainProcess) { brainProcess.kill(); brainProcess = null; }
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

protocol.registerFileProtocol('orb-vendor', (request, callback) => {
  const file = path.join(APP_DIR, 'vendor', decodeURIComponent(request.url.replace('orb-vendor://', '')));
  callback({ path: file });
});

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');

app.whenReady().then(() => {
  spawnBrain();
  createOrbWindow();
  createPetWindow();
});

app.on('window-all-closed', () => {
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
  await shell.openExternal(url);
});

ipcMain.handle('window:toggle-pin', (_event) => {
  if (!orbWindow) return { pinned: false };
  const next = !orbWindow.isAlwaysOnTop();
  orbWindow.setAlwaysOnTop(next);
  return { pinned: next };
});

ipcMain.handle('window:show-pet', (_event) => {
  if (petWindow) {
    petWindow.show();
  }
  return { ok: true };
});

ipcMain.handle('window:hide-pet', (_event) => {
  if (petWindow) {
    petWindow.hide();
  }
  return { ok: true };
});

ipcMain.handle('pet_move', (_event, { dx, dy }) => {
  if (!petWindow) return { ok: false };
  const [x, y] = petWindow.getPosition();
  petWindow.setPosition(x + dx, y + dy);
  return { ok: true };
});

ipcMain.handle('focus_orb', (_event) => {
  if (orbWindow) {
    orbWindow.focus();
  }
  return { ok: true };
});
