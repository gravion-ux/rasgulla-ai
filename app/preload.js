'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('rasgulla', {
  sendToBrain: (payload) => ipcRenderer.invoke('brain:send', payload),
  spawnBrain: () => ipcRenderer.invoke('brain:spawn'),
  togglePin: () => ipcRenderer.invoke('window:toggle-pin'),
  openExternal: (url) => ipcRenderer.invoke('shell:open', url),
  showPet: () => ipcRenderer.invoke('window:show-pet'),
  hidePet: () => ipcRenderer.invoke('window:hide-pet'),
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
