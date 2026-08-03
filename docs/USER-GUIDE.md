# DFlash Console — User Guide

This guide walks you through everyday use of DFlash Console: starting engines, loading checkpoints, reading live stats, and managing settings.

---

## 1. First launch

1. Copy `config.example.json` to `config.json` and set your **DFlash root** path and engine profiles.
2. Run `.\run.ps1` from PowerShell, or `.\scripts\run-electron.ps1` for the desktop window.
3. Open **http://127.0.0.1:8900/** in your browser if you started the API without Electron.

The sidebar shows the main areas: **Engines**, **Checkpoints**, **Playground**, **Nodes**, **Model catalog**, **Settings**, and **Documentation**.

---

## 2. Engines tab

The **Engines** view is your control center for llama-server profiles defined in `config.json`.

### Start an engine

- Use the **power toggle** in the top bar to start or stop the selected engine process.
- Or click **Load** after picking a checkpoint from the dropdown — the router starts automatically if needed.

### Load a checkpoint

1. Choose a checkpoint from **Select checkpoint…**
2. Click **Load**
3. A loading card appears with a progress bar while weights load.
4. When ready, the card shows **Loaded** and live token stats on top.

You can load **multiple engines in parallel** — each profile tracks its own progress independently.

### Loaded engine card

Each loaded card shows:

| Row | Meaning |
|-----|---------|
| **Generating Xs** | Inference is running; seconds update every second |
| **Generated · Speed** | Tokens from the last completion and tokens per second |

**Click** a loaded card to open the **Runtime panel** on the right (context size, GPU layers, sampling defaults).

**Right-click** a card for:

- Show details / Show runtime
- Copy API URL or checkpoint path
- View metadata
- Unload checkpoint
- Cancel load (while loading)

### Eject vs stop

| Action | Result |
|--------|--------|
| **Eject / Unload** | Removes the checkpoint; router keeps listening on the port |
| **Stop engine** | Shuts down the llama-server process entirely |

Embedding engines are different: their listener requires the embedding
checkpoint to remain loaded, so use **Stop** when you need to release their
GPU memory.

### Developer logs

Scroll to **Developer logs** at the bottom. Logs stream from the active engine. Use **Clear** in the header to wipe the log file.

---

## 3. Checkpoints tab

Browse all GGUF and other model files discovered in your configured libraries.

- Rows tagged **loadable** match an engine profile in `config.json`.
- Click **Load** on a row to load that checkpoint on its engine.
- Use the inspector on the right for file details and runtime settings before loading.

---

## 4. Model catalog

Open **Model catalog** in the sidebar to search Hugging Face and download GGUF files into a library folder you choose.

---

## 5. Settings

Open **Settings** (gear icon) for workspace, hardware, and engine configuration.

### Locations (recommended starting point)

**Settings → Locations** shows every important path on your machine:

- Console config file
- DFlash install directory
- Checkpoint library roots
- Engine log folder
- Launch preset folder
- Console UI URL

From here you can **Export / Import config** and **Export / Import presets** for backup or moving to another PC.

### Hardware

- Enable or disable specific GPUs
- Choose a **split strategy** (single largest GPU, even split, split by VRAM)
- View live CPU and GPU readings in the system bar

### Engine settings

Configure idle unload behavior and per-engine defaults under **Engine settings**
in the Engines bar or **Settings → Engine network**. The Console UI and managed
engine listeners are loopback-only by design.

---

## 6. System bar

The strip at the top shows CPU usage, memory, and GPU activity. CPU readings are calibrated to match Windows Task Manager.

The status feed on the right reports recent actions (loads, errors, exports).

---

## 7. Calling your engines

Each loaded card displays an **OpenAI-compatible URL**, for example:

```
http://127.0.0.1:8090/v1/chat/completions
```

You can also route requests through the console proxy (updates live stats automatically):

```
POST http://127.0.0.1:8900/api/servers/{server_id}/v1/chat/completions
```

Use the same JSON body as the OpenAI Chat Completions API. Response `usage` and `timings` fields feed the token stats on the card.

---

## 8. Documentation tab

Open **Documentation** in the sidebar for:

- **Overview** — product summary and quick links
- **User guide** — this document
- **Engine control** — REST endpoints for load/unload/stop
- **Runtime JSON shapes** — load and inference settings fields
- **Engine OpenAI API** — direct llama-server routes
- **Console — models, hardware, libraries** — catalog and hardware APIs

Swagger UI is also available at `/docs` for interactive testing.

---

## 9. Troubleshooting

| Problem | What to try |
|---------|-------------|
| Engine shows **Ready to load** after console restart | This is expected: full restart clears model VRAM but restores saved listeners idle; click **Load** when needed |
| **400 Bad Request** on chat | Checkpoint may not be loaded; load the model first |
| Stats stuck on dashes | Run at least one completion through the engine or console proxy |
| CPU looks wrong | Hard-refresh the page (Ctrl+Shift+R) after updating |
| Backend changes not visible | Run `.\scripts\restart-console-server.ps1` |

---

## 10. Tips

- Keep **config.json** and presets backed up via **Settings → Locations**.
- Load heavy models one at a time if VRAM is tight, even though parallel load is supported.
- Use the runtime panel to tune context size and GPU layers before reloading.
- Pin the console URL in your browser — it is always **http://127.0.0.1:8900/** unless you change the port in config.
