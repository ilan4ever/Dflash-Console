# Development Log — Workbench

A running record of Console changes that are worth following: what changed, why,
where the code lives, how it was verified, and what is still open.

Newest entries go on top. Each entry references the commit so you can
`git show <hash>` for the full diff.

---

## 2026-10-25 — engine modules show versions and can update themselves

**Tests:** `tests/test_component_versions.py`, `tests/test_components_hub.py`
and the full suite

### What changed

- **Downloads & engines** now shows the installed and latest version for vLLM,
  Transformers and FreeToken. A newer version is bold. Bundled engines show
  the Console bundle version.
- **Check for updates now** probes PyPI and the installed packages. Version
  checks are cached so polling the settings panel does not launch WSL imports.
- A new **Automatically update engine modules** toggle starts one installer at a
  time when a newer version is available. It skips an engine that is currently
  running, then retries on the next check.
- Each installing card shows a progress bar and the current installer step.
  A failed update stays visible as **Install failed** instead of falling back to
  **Update available**.
- Updating an already installed engine uses the installer's forced/update path;
  previously **Update / repair** saw the engine as installed and did nothing.
  The running adapter is stopped first, so Windows releases `python.exe`.
- The Transformers updater reuses a compatible 3.11/3.12/3.13 environment,
  keeps its CUDA PyTorch wheel, and uses a timeout on dependency resolution.
  The system Python 3.14 cannot install the cu124 torch wheel.
- Active-download polling no longer scans every model folder. The expensive
  incomplete-shard discovery runs only for an explicit discover request.
- The Electron shell remembers its page zoom across UI reloads, so a UI refresh
  no longer snaps a zoomed-out window back to 100%.

---

## 2026-10-25 — a reload cannot kill a load that is already running

**Tests:** `tests/test_server_boot.py` (reload guard), full suite

### The problem

OneVoice switched to compressed Gemma while its chat request was already
bringing the engine up. Its settings/reload calls arrived during that load; each
reload stopped the router and started it again. The card sat on **Loading into
GPU** for minutes, then the engine disappeared. The last reload waited 250 s
and returned 400 "timed out waiting for the model server to finish starting".

### What changed

- `reload_server` checks for an active model load and for a held per-port boot
  lock before stopping anything. If either is present it returns
  `{success: true, starting: true}` and leaves the working load alone.
- The reload endpoint no longer records a "loaded" attribution for a reload
  that was skipped because a load is in progress.
- Unloading Strata no longer leaves a synthetic **STOPPED** card on Engines; a
  temporary loading card exists only while a launcher process is actually
  starting.

---

## 2026-10-25 — Other GPU chips show the full process details

**Tests:** `tests/test_gpu_processes.py` and the full suite

### What changed

Hovering or focusing an **Other GPU** chip now opens a details panel with the
application name, process name, PID, executable path, command line, GPU, VRAM,
VRAM source, component and parent process. The process payload now includes
`executable_path`, `parent_process_name`, `parent_pid` and a longer command
line. The panel is fixed-positioned so it is not clipped by the Engines card,
and it is keyboard accessible.

Protected Windows processes can report an empty executable path; the panel
says so instead of inventing one.

---

## 2026-10-25 — a bad local HTTP banner cannot hide every engine card

**Tests:** `tests/test_gpu_processes.py` (92 passed, 1 skipped); full suite
982 passed, 2 skipped

### The problem

One unrelated local app answered its port with a plain sentence instead of an
HTTP status line (`Android Console: Authentication required`). The external
model scan probed every listening port; `http.client.BadStatusLine` escaped the
probe, aborted the whole scan, and left `external_gpu_loads` empty.  Strata then
appeared only in **Other GPU** and its Console card disappeared.

### What changed

- `_probe_models_fast` treats a malformed HTTP banner as "not an engine" and
  moves on to the next port.
- `get_external_gpu_loads` isolates each process card build: one broken external
  app can no longer remove every other model card.
- A regression test feeds the Android banner through the probe.

Verified live: the scan is clean and Strata is back under **This app** instead
of **Other GPU**.

---

## 2026-10-25 — Strata is a Console engine card

**Tests:** `tests/test_gpu_processes.py`, `tests/test_external_models.py`,
`tests/test_external_launchers.py`, `tests/test_load_progress.py`,
`tests/test_ui_layout.py` (123 passed, 1 skipped); full suite 981 passed,
2 skipped

### The problem

Strata's engine is started through the Console's external launcher, but the
GPU scan treated the process as an external workload.  The model was only
visible as an "Other GPU" chip (or an external card if another app started
it), never as a Console model card.

### What the Console does now

| behaviour | detail |
|---|---|
| Card | a running Strata engine is a regular Console card under **This app** |
| Ownership | `console_engine: true`, `external: false`, `launcher_id: "strata"` |
| Title | the model from the launcher config, for example `qwen3.8-flash-next-unsloth-ud-iq4_xs` |
| Other GPU | the engine and its vision reader are removed from the chip list |
| Unload | the card's **Unload** calls the launcher stop endpoint, not the GPU-process kill path |
| Discovery | any registered launcher process is recognized, including one started by another app |
| Attribution | a `STRATA` engine badge sits beside the app that asked for the load (for example `OneVoice`), not the Console name |
| Context | the context length comes from Strata's live health and config (`max_context`, for example `131072`); it is shown on the card and in Runtime settings |
| Path | the model file path is shown on the card (and in full under Details) |
| Slots | the card and Runtime settings offer 1, 2, 3, 4, 6 or 8 parallel requests; changing the value restarts Strata through the launcher |
| Restart progress | a slot change starts the launcher without blocking the request thread, then polls its status. The card shows **Restarting Strata**, then **Loading Strata** with the target slot count and elapsed time instead of a long **UNLOADING** overlay |
| Runtime hint | the Strata context hint is fixed text, not refreshed by hardware recommendations; its recommendation bar is hidden for launcher engines so the line cannot flicker and shift the fields below |
| Stopped | unloading Strata removes its Engines card, like any other unloaded engine. A temporary loading card is shown only while the process is actually starting |
| Gateway | `/v1/models` lists the launcher model; a chat request starts a stopped launcher |

The gateway still advertises the launcher model through `_launcher_entries`; the
external scan skips the Console-owned card so it is not duplicated. The model
stays in the gateway model list while the launcher is stopped, so the first
request can start it the same way a normal JIT load does.

### Files

| file | role |
|---|---|
| `core/external_launchers.py` | process-to-launcher matching, model/context/path lookup, caller attribution |
| `core/gpu_processes.py` | launcher-managed Strata card is marked Console-owned; a temporary card exists only while the launcher is starting |
| `core/external_models.py` | external scan skips Console-owned launcher cards |
| `api/app.py` | records the caller on launcher load/status |
| `api/gateway.py` | starts a stopped launcher for a gateway request |
| `static/js/server-live.js` | regular card, engine badge, caller badge, context/slots and launcher-aware Unload |
| `static/js/models-live.js` | a starting launcher row does not mark a catalog model as loaded |
| `static/css/dflash-console.css`, `static/css/dflash-shell.css` | launcher loading card, header and progress overlay |

---

## 2026-10-25 — A router reload no longer leaves a card LOADING

**Tests:** `tests/test_load_progress.py` and the focused engine suite
(319 passed, 1 skipped)

### The problem

After a router reload, the engine log still contained an older
`load_model: loading model` line from the previous child.  `is_active_model_load`
scanned across the new `=== boot` / `=== router idle ready` boundary, so the
router stayed `booting` forever even though `/v1/models` reported `unloaded`.

### What changed

- `core/load_progress.py` closes a load at a newer boot or idle/unload marker.
- `static/js/server-live.js` clears an optimistic loading card when the server
  reports `stopped`/`error`, or after 8 s without a loading or ready card.

---

## 2026-10-25 — Embedding engine loads on demand

**Tests:** focused embedding and engine suites (67 + 239 passed)

### The problem

The embedding model was started with `-m --embedding` during Console boot, so
it held GPU memory even when no app was using it.  External apps call the
engine port directly, so simply deferring the start would have broken them.

### What the Console does now

| behaviour | detail |
|---|---|
| Listener | the embedding port runs a router, not a loaded model |
| First use | the router starts the embedding child on the first `/v1/embeddings` request |
| Idle | the child sleeps after `idle_unload_minutes` (`--sleep-idle-seconds`) |
| Wake | the next embedding request reloads it automatically |
| Unload | frees the model but keeps the router ready |
| Names | aliases cover the checkpoint filename, the configured id, the engine id, and common `nomic-embed` spellings |

Direct clients must send a known model name.  The Console's own embedding
routes fill in the configured name automatically.

### Status polls must not wake the model

The router autoloads its child when anything asks `/slots`.  The engine status
builder and the `inference-stats` endpoint were polling `/slots` for the
embedding card, so **Unload** freed the GPU and the next status tick loaded it
again.  Both now skip token stats for embedding engines:

- `core/runtime.py` — the embedding status card reports empty inference stats.
- `api/app.py` — `/api/servers/{id}/inference-stats` returns `status: embedding`
  without touching the engine.

Verified live: after Unload the router reports `unloaded`, no child process
stays behind, and a direct `/v1/embeddings` request loads it again (768 dims).

### Files

| file | role |
|---|---|
| `core/model_presets.py` | embedding preset keys (`embeddings`, `pooling`, `jinja = false`) and model aliases |
| `core/embedding_server.py` | router start, status detection, router unload |
| `core/server_boot.py` | embedding profiles use the normal router load path |
| `scripts/start_llama_server.ps1` | `-ModelsAutoload` for embedding routers |
| `core/engine_state.py` | legacy direct listener migration and router-aware release |
| `core/runtime.py` | embedding status avoids `/slots` so polls cannot autoload |

---

## 2026-10-20 — Context & parallel slots (the "32K request fails" problem)

**Commits:** `8d17f84` → `2256c98`
**Tests:** 946 passed, 2 skipped

### The problem

llama-server splits the configured context across parallel slots:

$$\text{per\_slot} = \frac{\texttt{--ctx-size}}{\texttt{--parallel}}$$

The Console defaulted `parallel_slots` to **4**, so an engine configured with
65536 tokens actually served **16384 per request**. An app asking for 32K was
rejected by llama-server with a generic "context length exceeded" — nothing in
the Console explained why. The first manual fix (setting 1 slot by hand) worked
but then every other app lost the ability to share the model in parallel.

### What the Console does now

| concept | behaviour |
|---|---|
| `context_size` | stored as the **total** (per-slot × slots); the UI shows per-slot |
| `load_settings.parallel_slots` | **cap** on slots for that engine |
| `load_settings.requested_parallel_slots` | the shape the policy currently wants (starts at 1) |
| `load_settings.parallel_slots_mode` | `auto` (default) or `fixed` (legacy: always launch `parallel_slots`) |
| `X-DFlash-Min-Context` | hard floor — may trigger a reload, otherwise HTTP 409 |
| `X-DFlash-Optimal-Context` | preference — never triggers a reload, only spent if free |
| `X-DFlash-Load-Context` | still accepted, treated as *min* |

Decision rules (`core/context_policy.py::plan_context`):

1. `per_slot` = largest minimum any sharing client declared (never below 2K,
   never above the model capability).
2. `slots` = one per client that actually overlaps, capped by
   `capability // per_slot` and by the configured cap.
3. More context needed → **drop slots first** (32K on 1 slot instead of 32K on 4,
   a quarter of the KV memory).
4. A minimum above what the model can hold → `409 context_below_minimum` with
   `required_context` and `capability`, before the prompt is tokenised.
5. Idle engines shrink to 1 slot; busy engines are never restarted.

### Files

| file | role |
|---|---|
| `core/context_policy.py` | **new** — pure planner (`plan_context`, `max_slots_for`), fully unit tested |
| `core/context_maintenance.py` | **new** — 60 s background pass: `grow_for_clients_pass` + `auto_shrink_pass` |
| `core/config.py` | `effective_parallel_slots()`, `parallel_slots_mode`, `requested_parallel_slots` |
| `core/runtime.py` | `loaded_per_slot_context()`, `parallel_slots` / `per_slot_context` in status payloads |
| `core/server_boot.py` | launcher uses the effective slot count |
| `api/app.py` | rewired `_grow_context_for_chat`, `_reject_unservable_context`, `context-maintenance` thread |
| `static/js/server-live.js` | CTX chip renders per-slot with a `×N` multiplier |
| `static/css/dflash-shell.css` | `.lm-ctx-slots` styling |
| `tests/test_context_policy.py` | **new** — 11 planner tests |
| `tests/test_context_autoshrink.py` | **rewritten** — 14 load-shape / grow / shrink tests |

### Knobs

| setting | default | meaning |
|---|---|---|
| `context_auto_grow_slots` | on | give one slot per recently active client |
| `context_auto_shrink` | on | return idle multi-slot engines to one slot |
| `context_shrink_idle_seconds` | 300 | quiet time before shrinking |
| `context_client_memory_seconds` | 1800 | how long a client counts as "still using" the engine |

### Verified live

- Broken shape reproduced: `65536 / 4 slots` → per-slot **16384**.
- 32K request with `X-DFlash-Min-Context: 32768` → reloaded to **32768 total /
  1 slot** (old code would have used 131072), chat answered in 0.9 s.
- Impossible minimum (500000) → **409** with numbers, no reload, per-slot unchanged.
- Cohort growth: two client identities → `grew_for_clients, to_slots 2,
  per_slot 32768, total 65536`.
- UI card shows **`CTX 32K×2`** (per-request window × slots).
- Test engine restored to its original shape afterwards.

### Still open

- `context_size` is still *total* in storage; only the display is per-slot.
  Changing the stored meaning needs a migration.
- Truly simultaneous large-context requests on a 1-slot engine queue rather than
  reload mid-stream (intentional); growth happens at the next quiet moment.
- VRAM is not yet part of the slot ceiling (it uses `capability // per_slot`);
  KV quantization (`-ctk q8_0 -ctv q8_0`) would make extra slots cheaper.

---

## 2026-10-20 — Engine visibility, ownership and leftovers

**Commits:** `f1d6000` … `8e89597`

- Console-spawned `llama-server` processes on unconfigured ports stay **visible**
  as cards, in Console colours, with the DFlash logo — they are no longer hidden
  (hiding them made "GPU at 60 % with no cards" possible).
- Cards show the **real calling app** (`core/client_identity.py`), not
  "LLAMA-SERVER": in-flight client → last touch → `loaded_by`.
- `Unloading…` gets a large animated overlay instead of greying the card.
- Unload falls back to the **parent router** (`POST /models/unload`) when
  `taskkill` is denied, and to the **WMI provider** (`wmic … Terminate`) when the
  process was started by an elevated Console. No UAC prompt needed.
- Boot sweep no longer unloads models from leftovers and never touches a
  configured port — that sweep is what previously killed an engine an app was
  using.

## 2026-10-20 — Embedding engine kept alive (OneVoice)

**Commit:** `4f2d390`

- `onevoice.embedding` → **"OneVoice AI"** added to `COMPONENT_LABELS`, so the
  card is attributed correctly instead of showing the raw component key.
- Armed embedding engines are **started at boot** (`restore_engines`) instead of
  being deferred — clients call the engine port directly, so a deferred engine
  looked like an outage.
- `server.ps1 -ApiRestart` sets `DFLASH_CONSOLE_PRESERVE_ENGINES=1`, honoured by
  the shutdown hook, so an API-only restart no longer frees VRAM.
- Canonical embedding endpoint: id `nomic-embed-text-v1-5-q8-0`, port **8101**,
  `http://127.0.0.1:8101/v1`, model `nomic-embed-text-v1.5.Q8_0.gguf`.

---

## How to follow development

```powershell
git log --date=short --pretty=format:"%h %ad %s" -15   # recent entries
git show 2256c98                                       # one change in detail
```

Per-release notes live in `docs/release-notes-<version>.md`; this file records
the *engineering* trail (decisions, measurements, dead ends).
