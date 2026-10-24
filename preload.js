'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nurowDesktop', {
  version: process.versions.electron,
  quit: () => ipcRenderer.send('quit'),
  openLogs: () => ipcRenderer.send('open-logs'),
  openModels: () => ipcRenderer.send('open-models'),
  openSettings: () => ipcRenderer.send('open-settings'),
  toggleClickThrough: () => ipcRenderer.send('toggle-clickthrough'),
  resetPosition: () => ipcRenderer.send('reset-position'),
  startDrag: () => ipcRenderer.send('drag-start'),
  endDrag: () => ipcRenderer.send('drag-end'),
  onCursor: (cb) => {
    ipcRenderer.on('cursor', (_event, payload) => {
      try { cb(payload); } catch (_) { /* ignore */ }
    });
  },
});
