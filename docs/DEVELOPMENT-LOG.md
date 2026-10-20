# Development Log — Workbench

A running record of Console changes that are worth following: what changed, why,
where the code lives, how it was verified, and what is still open.

Newest entries go on top. Each entry references the commit so you can
`git show <hash>` for the full diff.

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
