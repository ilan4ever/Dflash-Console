from pathlib import Path

# --- chat-live.js ---
chat = Path(r'C:\dev\Dflash-Console\static\js\chat-live.js')
text = chat.read_text(encoding='utf-8')

if 'function selectCheckpoint(' not in text:
    old_export = "  window.DFlashChatLive = { onViewEnter, refreshEngines: () => refreshCatalog({ force: true }) };"
    new_fns = r'''
  function readCheckpointKeyFromStorage() {
    try {
      return String(localStorage.getItem(CHECKPOINT_KEY) || '').trim();
    } catch (_err) {
      return '';
    }
  }

  function selectCheckpoint(key, { persistSelection = true } = {}) {
    const next = String(key || '').trim();
    if (!next) return false;
    selectedCheckpointKey = next;
    try { localStorage.setItem(CHECKPOINT_KEY, next); } catch (_err) { /* ignore */ }
    if (persistSelection) persist();
    renderPickers();
    syncEngineToModel();
    syncSessionEngine();
    updateComposerState();
    const pick = document.getElementById('chatCheckpointPick');
    if (pick && [...pick.options].some((opt) => opt.value === next)) {
      pick.value = next;
      return true;
    }
    return false;
  }

  window.DFlashChatLive = {
    onViewEnter,
    refreshEngines: () => refreshCatalog({ force: true }),
    selectCheckpoint,
  };
'''
    if old_export not in text:
        raise SystemExit('export line not found in chat-live.js')
    text = text.replace(old_export, new_fns, 1)

# Re-read checkpoint from storage at start of renderCheckpointPicker
needle = '''  function renderCheckpointPicker() {
    const pick = document.getElementById('chatCheckpointPick');
    if (!pick) return;

    const prev = pick.value || selectedCheckpointKey || '';
'''
repl = '''  function renderCheckpointPicker() {
    const pick = document.getElementById('chatCheckpointPick');
    if (!pick) return;

    const storedKey = readCheckpointKeyFromStorage();
    if (storedKey && storedKey !== selectedCheckpointKey) {
      selectedCheckpointKey = storedKey;
    }
    const prev = pick.value || selectedCheckpointKey || '';
'''
if needle in text and 'readCheckpointKeyFromStorage()' not in text.split('function renderCheckpointPicker')[1][:400]:
    text = text.replace(needle, repl, 1)
elif 'storedKey = readCheckpointKeyFromStorage()' not in text:
    if needle not in text:
        # maybe already partially patched
        if 'readCheckpointKeyFromStorage()' not in text:
            raise SystemExit('renderCheckpointPicker needle not found')
    else:
        text = text.replace(needle, repl, 1)

# onViewEnter: sync storage first
old_enter = '''  async function onViewEnter() {
    await refreshCatalog({ force: !catalogLoaded });
'''
new_enter = '''  async function onViewEnter() {
    const storedKey = readCheckpointKeyFromStorage();
    if (storedKey) selectedCheckpointKey = storedKey;
    await refreshCatalog({ force: !catalogLoaded });
'''
if old_enter in text and 'const storedKey = readCheckpointKeyFromStorage()' not in text.split('async function onViewEnter')[1][:200]:
    text = text.replace(old_enter, new_enter, 1)

chat.write_text(text, encoding='utf-8')
print('chat-live patched')

# --- models-live.js ---
models = Path(r'C:\dev\Dflash-Console\static\js\models-live.js')
mtext = models.read_text(encoding='utf-8')
old_open = '''  function openCloudModelInPlayground(model) {
    if (!isCloudApiModel(model)) return;
    const key = String(model?.chat_model_key || modelKey(model) || '').trim();
    if (key) {
      try { localStorage.setItem('dflashConsole.chatCheckpointKey', key); } catch (_) { /* ignore */ }
    }
    window.DFlashShell?.setView?.('chat');
  }
'''
new_open = '''  function openCloudModelInPlayground(model) {
    if (!isCloudApiModel(model)) return;
    const key = String(model?.chat_model_key || modelKey(model) || '').trim();
    if (key) {
      try { localStorage.setItem('dflashConsole.chatCheckpointKey', key); } catch (_) { /* ignore */ }
    }
    window.DFlashShell?.setView?.('chat');
    const apply = () => {
      if (!key) return;
      if (typeof window.DFlashChatLive?.selectCheckpoint === 'function') {
        window.DFlashChatLive.selectCheckpoint(key);
      }
    };
    // Catalog refresh on Playground enter is async — retry briefly until the option exists.
    apply();
    setTimeout(apply, 50);
    setTimeout(apply, 200);
    setTimeout(apply, 500);
    setTimeout(apply, 1200);
  }
'''
if old_open not in mtext:
    raise SystemExit('openCloudModelInPlayground block not found')
mtext = mtext.replace(old_open, new_open, 1)
models.write_text(mtext, encoding='utf-8')
print('models-live patched')
