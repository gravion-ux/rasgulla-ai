'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('rasgulla', {
  sendToBrain: (payload) => ipcRenderer.invoke('brain:send', payload),
  spawnBrain: () => ipcRenderer.invoke('brain:spawn'),
  togglePin: () => ipcRenderer.invoke('window:toggle-pin'),
  toggleExpand: () => ipcRenderer.invoke('window:toggle-expand'),
  openExternal: (url) => ipcRenderer.invoke('shell:open', url),
  showPet: () => ipcRenderer.invoke('window:show-pet'),
  hidePet: () => ipcRenderer.invoke('window:hide-pet'),
  movePet: (dx, dy) => ipcRenderer.invoke('pet:move', { dx, dy }),
  focusOrb: () => ipcRenderer.invoke('orb:focus'),
  setOrbState: (state) => ipcRenderer.send('orb:state', state),
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
  onLayout: (handler) => {
    const subscription = (_event, state) => handler(state);
    ipcRenderer.on('window:layout', subscription);
    return () => ipcRenderer.off('window:layout', subscription);
  },
  onPetState: (handler) => {
    const subscription = (_event, state) => handler(state);
    ipcRenderer.on('pet:state', subscription);
    return () => ipcRenderer.off('pet:state', subscription);
  },
});
