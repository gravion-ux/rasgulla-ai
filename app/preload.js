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
});
