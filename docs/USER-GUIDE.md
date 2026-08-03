# DFlash Console — User Guide

This guide walks you through everyday use of DFlash Console: starting engines,
loading models, reading live stats, managing libraries, and configuring the
local runtime. The current application is developed by **ILAN AVIV** and is
intended for one trusted user on one Windows machine.

---

## 1. First launch

1. Copy `config.example.json` to `config.json` and set your **DFlash root** path and engine profiles.
2. Run `.\run.ps1` from PowerShell, or `.\scripts\run-electron.ps1` for the desktop window.
3. Open **http://127.0.0.1:8900/** in your browser if you started the API without Electron.
4. Open **About** to confirm the release, developer, license, and repository links.

The browser and Electron window use the same sidebar and backend. The main areas
are **Engines**, **Models**, **Playground**, **Nodes**, **Model catalog**,
**Settings**, **Documentation**, and **About**.

### Browser and Electron

The Electron build is a thin Windows shell around the same local Console UI. It
uses `DFLASH_CONSOLE_ROOT` when set, or asks for a Console data root containing
`server.ps1`, `api`, and `static`. Keep Python, PowerShell, llama-server, model
weights, and logs in that data root; they are intentionally not copied into
the desktop installation.

---

## 2. Engines tab

The **Engines** view is your control center for llama-server profiles defined in `config.json`.

### Start an engine

- Use the **power toggle** in the top bar to start or stop the selected engine process.
- Or click **Load** after picking a model from the dropdown — the router starts automatically if needed.

### Load a model

1. Choose a model from **Model to load**
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
- Copy API URL or model path
- View metadata
- Unload model
- Cancel load (while loading)

### Eject vs stop

| Action | Result |
|--------|--------|
| **Eject / Unload** | Removes the model; router keeps listening on the port |
| **Stop engine** | Shuts down the llama-server process entirely |

Embedding engines are different: their listener requires the embedding model
to remain loaded, so use **Stop** when you need to release their
GPU memory.

### Developer logs

Scroll to **Developer logs** at the bottom. Logs stream from the active engine. Use **Clear** in the header to wipe the log file.

---

## 3. Models tab

Browse GGUF and other model files discovered in your configured libraries.

- **DFlash stacks** is the default filter for runnable target-plus-accelerator pairs.
- **All models** includes regular GGUF, OCR, embedding, speech, vision, and other files.
- **Accelerators**, **Downloading**, and **Loaded** provide focused views.
- Click **Load** on a row to load that model on its matching engine.
- Use the inspector on the right for file details and runtime settings before loading.
- Use **Scan PC** or **Add folder** from Settings when a library is not discovered
  automatically. The folder browser supports other local drives.

---

## 4. Model catalog

Open **Model catalog** in the sidebar to search Hugging Face and download GGUF
files into the selected default library.

- Search by model name, author, or repository.
- Filter results by lab and sort by relevance, downloads, or recent activity.
- Open a result to inspect its README, tags, files, and install state.
- Start downloads from the detail panel. Progress remains visible in the global
  downloads tray and in the Models → **Downloading** filter.
- Private repositories use `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` from the
  process environment; never paste tokens into the Console configuration.

---

## 5. Settings

Open **Settings** (gear icon) for workspace, hardware, engine, and integration
configuration. The settings panels are grouped as follows:

- **Model storage** — enabled library folders and the default download folder.
- **Locations** — config, DFlash root, model libraries, logs, presets, and UI URL.
- **System summary** and **Live monitor** — CPU, memory, VRAM, GPU activity, and
  the active multi-GPU strategy.
- **GPU devices** and **Multi-GPU rules** — enabled devices, split strategy,
  dedicated VRAM limits, and KV-cache offload.
- **Network & API** and **Runtime limits** — loopback port, engine behavior,
  idle unload, and related runtime controls.
- **Launch preset** — import and export launch preset files.
- **MCP & clients** — inspect the generated client configuration and endpoint
  information.

### Locations (recommended starting point)

**Settings → Locations** shows every important path on your machine:

- Console config file
- DFlash install directory
- Checkpoint library roots
- Engine log folder
- Launch preset folder
- Console UI URL

From here you can **Export / Import config** and **Export / Import presets** for backup or moving to another PC.

Use **Export / Import config** to back up the Console configuration, and use
**Export / Import presets** to move launch profiles between machines. Imports
replace or write local files only after confirmation.

### Hardware

- Enable or disable specific GPUs
- Choose a **split strategy** (single largest GPU, even split, split by VRAM)
- View live CPU and GPU readings in the system bar

### Engine settings

Configure idle unload behavior under **Settings → Runtime limits**. The engine
gear button opens **Settings → Network & API**. The Console UI and managed
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

The page loads from the selected Console data root, so the browser and Electron
versions show the same documentation. Swagger UI is also available at `/docs`
for interactive testing.

---

## 9. About page

Open **About** in either the browser or Electron app for:

- Developer attribution: **ILAN AVIV**
- Current application version and MIT license
- Links to the Console source repository, developer profile, and DFlash project
- A summary of the FastAPI + llama-server runtime
- The local-only security boundary and external Electron data-root behavior

The About page is informational. It does not change configuration or contact
external services.

---

## 10. Troubleshooting

| Problem | What to try |
|---------|-------------|
| Engine shows **Ready to load** after console restart | This is expected: full restart clears model VRAM but restores saved listeners idle; click **Load** when needed |
| **400 Bad Request** on chat | A model may not be loaded; load the model first |
| Stats stuck on dashes | Run at least one completion through the engine or console proxy |
| CPU looks wrong | Wait for the next monitor poll or restart the Console server |
| Backend changes not visible | Run `.\scripts\restart-console-server.ps1` |

---

## 11. Tips

- Keep **config.json** and presets backed up via **Settings → Locations**.
- Load heavy models one at a time if VRAM is tight, even though parallel load is supported.
- Use the runtime panel to tune context size and GPU layers before reloading.
- Pin the console URL in your browser — it is always **http://127.0.0.1:8900/** unless you change the port in config.
