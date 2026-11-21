'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// The minimal bridge: the renderer can talk to the brain and listen to it.
contextBridge.exposeInMainWorld('rasgulla', {
  sendToBrain: (payload) => ipcRenderer.invoke('brain:send', payload),
  onBrainEvent: (handler) => {
    const subscription = (_event, message) => handler(message);
    ipcRenderer.on('brain:event', subscription);
    return () => ipcRenderer.off('brain:event', subscription);
  },
  onBrainLog: (handler) => {
    const subscription = (_event, message) => handler(message);
    ipcRenderer.on('brain:log', subscription);
    return () => ipcRenderer.off('brain:log', subscription);
  },
  // The window itself: the close button puts her in the tray instead of
  // killing her, and the pin decides whether she stays on top of the other
  // windows.
  hideApp: () => ipcRenderer.invoke('app:hide'),
  togglePin: () => ipcRenderer.invoke('app:toggle-pin'),
  isPinned: () => ipcRenderer.invoke('app:is-pinned'),
});
