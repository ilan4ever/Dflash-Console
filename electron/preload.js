'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('DFlashDesktop', {
  shell: 'electron',
  isElectron: true,
  getShellVersion: () => ipcRenderer.invoke('app:get-version'),
  getAppSettings: () => ipcRenderer.invoke('app-settings:get'),
  setAppSettings: (patch) => ipcRenderer.invoke('app-settings:set', patch),
  chooseDataRoot: () => ipcRenderer.invoke('app-settings:choose-data-root'),
  openUserDataFolder: () => ipcRenderer.invoke('app-settings:open-user-data'),
  getUpdateStatus: () => ipcRenderer.invoke('update:get-status'),
  checkForUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  installUpdateNow: () => ipcRenderer.invoke('update-popup:install'),
  deferUpdate: () => ipcRenderer.invoke('update-popup:later'),
  onUpdateStatus: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('update:status', listener);
    return () => ipcRenderer.removeListener('update:status', listener);
  },
  onPostInstallWelcomeCleared: (callback) => {
    if (typeof callback !== 'function') return () => {};
    const listener = () => callback();
    ipcRenderer.on('post-install-welcome:cleared', listener);
    return () => ipcRenderer.removeListener('post-install-welcome:cleared', listener);
  },
});
