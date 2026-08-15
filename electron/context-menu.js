'use strict';

const { Menu, clipboard, shell } = require('electron');

const attached = new WeakSet();

function buildContextMenuTemplate(webContents, params) {
  const template = [];
  const editFlags = params.editFlags || {};

  if (params.isEditable) {
    template.push(
      { role: 'undo', enabled: editFlags.canUndo },
      { role: 'redo', enabled: editFlags.canRedo },
      { type: 'separator' },
      { role: 'cut', enabled: editFlags.canCut },
      { role: 'copy', enabled: editFlags.canCopy },
      { role: 'paste', enabled: editFlags.canPaste },
      { role: 'pasteAndMatchStyle', enabled: editFlags.canPaste },
      { type: 'separator' },
      { role: 'selectAll', enabled: editFlags.canSelectAll },
    );
  } else if (String(params.selectionText || '').trim()) {
    template.push(
      { role: 'copy' },
      { type: 'separator' },
      { role: 'selectAll' },
    );
  }

  if (params.linkURL) {
    if (template.length) template.push({ type: 'separator' });
    template.push(
      {
        label: 'Open Link',
        click: () => {
          void shell.openExternal(params.linkURL);
        },
      },
      {
        label: 'Copy Link',
        click: () => {
          clipboard.writeText(params.linkURL);
        },
      },
    );
  }

  if (params.mediaType === 'image' && params.srcURL) {
    if (template.length) template.push({ type: 'separator' });
    template.push(
      {
        label: 'Copy Image',
        click: () => {
          webContents.copyImageAt(params.x, params.y);
        },
      },
      {
        label: 'Copy Image Address',
        click: () => {
          clipboard.writeText(params.srcURL);
        },
      },
    );
  }

  return template;
}

function attachContextMenu(webContents) {
  if (!webContents || attached.has(webContents)) return;
  attached.add(webContents);

  webContents.on('context-menu', (event, params) => {
    const template = buildContextMenuTemplate(webContents, params);
    if (!template.length) return;

    const window = webContents.getOwnerBrowserWindow?.() || null;
    Menu.buildFromTemplate(template).popup({
      window: window || undefined,
      x: params.x,
      y: params.y,
    });
  });
}

function registerContextMenus(app) {
  app.on('browser-window-created', (_event, window) => {
    attachContextMenu(window.webContents);
  });
}

module.exports = {
  attachContextMenu,
  registerContextMenus,
};
