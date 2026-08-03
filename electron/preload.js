'use strict';

// Reserved for future desktop bridges. The Console UI talks to the local
// FastAPI backend over HTTP the same way the browser does.
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('DFlashDesktop', {
  shell: 'electron',
});
