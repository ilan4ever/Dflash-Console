# DFlash Console CLI

The Console is the source of truth for models on this PC. The same library you
see in the **Models** tab is available from PowerShell, cmd, or any terminal
with `dflash`, the same way `ollama list` talks to Ollama.

The Console server must be running for most commands (`dflash serve` or
`.\server.ps1`). These work without a running server: `help`, `version`,
`serve`, `install`.

Public docs: [https://onevoiceai.in/dflash-console/docs/CLI.md](https://onevoiceai.in/dflash-console/docs/CLI.md)

---

## Install

The PyPI package name will be **`dflash-console`** (not published yet). The
command you type is **`dflash`**. Do not run `pip install dflash` — that is a
different project.

### From pip (after PyPI publish)

```powershell
pip install dflash-console
dflash serve
dflash help
```

Until PyPI is live, use a git checkout (below).

### From a git checkout (current)

```powershell
pip install -e .
dflash serve
```

Or register the command without pip:

```powershell
.\dflash.ps1 install
dflash help
```

From the repo folder you can also run `.\dflash.ps1` or `.\dflash.cmd`.

### Desktop installer

The Windows setup app is a thin shell around the same Console. It starts the
local server; you can still use `dflash` in a terminal against that server.

---

## Command map

| Command | What it does |
|---------|----------------|
| `dflash help` | Command overview |
| `dflash version` | CLI and server version |
| `dflash status` | Server health and loaded count |
| `dflash serve` | Start the Console if it is not running |
| `dflash open` | Open the UI in a browser |
| `dflash install` | Add `dflash` to your user PATH |
| `dflash list` | Every local model the Console can see |
| `dflash ps` | Models loaded right now |
| `dflash show <name>` | Details for one model |
| `dflash load <name>` | Load a model |
| `dflash unload <name>` | Unload an engine or runtime |
| `dflash start <engine>` | Start an engine |
| `dflash stop <engine>` | Stop an engine |
| `dflash delete <name>` | Delete a local model from disk |
| `dflash chat "hello"` | Send a prompt |
| `dflash embed "hello"` | Turn text into vectors |
| `dflash search qwen` | Search Hugging Face |
| `dflash pull org/repo --file name.gguf` | Download a file |
| `dflash downloads` | Current and last downloads |
| `dflash engines` | Engine profiles |
| `dflash runtimes` | Speech and other runtimes |
| `dflash nodes` | Remote Consoles |
| `dflash settings` | Show or change settings |
| `dflash hardware` | GPUs |
| `dflash stats` | CPU, RAM, VRAM |
| `dflash report` | Full machine report |
| `dflash logs` | Console or engine logs |
| `dflash api GET /api/health` | Call any Console HTTP route |

Names can be short when they are unique. `dflash load gemma` matches Gemma 31B
if that is the only Gemma match.

---

## Global flags

These work on every command:

| Flag | Meaning |
|------|---------|
| `-u`, `--url URL` | Console URL, or set `DFLASH_URL` |
| `-p`, `--port N` | Port (default 8900) |
| `-j`, `--json` | Print raw JSON |
| `-q`, `--quiet` | Less text |
| `--timeout SEC` | HTTP timeout (default 30) |

Example against another PC on your LAN (that Console must already allow it):

```powershell
dflash status --url http://192.168.1.37:8900
```

---

## Start the Console

```powershell
dflash serve
dflash serve -P 8900
dflash open
dflash version
dflash status
```

`serve` does nothing if the UI is already up. `open` launches the browser.

---

## List every local model

```powershell
dflash list
```

This is the full Console library: DFlash stacks, GGUF files, Ollama models,
LM Studio folders, speech, OCR, and embeddings.

| Flag | Result |
|------|--------|
| `--ollama` | Only models Ollama installed on this PC |
| `--lmstudio` | Only LM Studio library models |
| `--vllm` | Hugging Face models that can run on vLLM |
| `--transformers` | Hugging Face models that can run on Transformers |
| `--dflash` | DFlash stacks and Console library models |
| `--source NAME` | `ollama`, `lmstudio`, `dflash`, `library`, `vllm`, or `transformers` |
| `--loaded` | Models currently on an engine |
| `--type llm` | Filter by type (`ocr`, `embedding`, `speech-to-text`, …) |
| `--filter qwen` | Text match on name, id, or path |
| `--refresh` | Rescan folders |
| `--quick` | Engine profiles only |
| `--json` | Raw JSON from the server |

Examples:

```powershell
dflash list --ollama
dflash list --lmstudio
dflash list --dflash
dflash list --vllm
dflash list --source library --type llm
dflash list --filter gemma --json
dflash show qwen
dflash ps
```

---

## Load, unload, start, stop

```powershell
dflash load qwen
dflash load qwen --engine qwen3-8-27b
dflash unload qwen
dflash start nomic-embed
dflash stop nomic-embed
dflash engines
dflash runtimes
```

`load` picks a unique name from the library. `unload` accepts an engine,
runtime, or model name.

---

## Delete a local model

Removes the file or Hugging Face folder from disk. Asks for confirmation
unless you pass `--yes`.

```powershell
dflash delete qwen
dflash delete "Qwen2.5-32B-Instruct" --yes
```

This uses the same delete path as the Models tab. Ollama models go through
Ollama. Hugging Face hub folders (snapshots and blobs) are removed together.

---

## Chat and embeddings

```powershell
dflash chat "hello"
dflash chat -e gemma-12b-ar "summarize this"
dflash embed "a document to index"
dflash embed --file notes.txt
dflash embed -e nomic-embed "one" "two"
```

`embed` uses an embedding engine (for example Nomic). Load that model first if
nothing is loaded. `--file` reads one item per line.

---

## Catalog and downloads

```powershell
dflash search qwen
dflash search qwen --limit 20 --sort downloads
dflash pull org/repo --file name.gguf
dflash pull "qwen 3.8" --install
dflash pull org/repo --file name.gguf --wait
dflash downloads
dflash downloads --active
dflash downloads --range 7
dflash downloads --clear
```

`pull` without `--file` lists downloadable files in the repo.

---

## Remote nodes

Talk to other DFlash Console instances you have already registered.

```powershell
dflash nodes
dflash nodes --fresh
dflash nodes add http://192.168.1.10:8900 --label Lab
dflash nodes add http://192.168.1.10:8900 --label Lab --token SECRET
dflash nodes health Lab
dflash nodes remove Lab
```

The Nodes tab in the UI shows the same list.

---

## Settings

Shows safe values only (ports, roots, counts). Tokens stay hidden.

```powershell
dflash settings
dflash settings --get ui_port
dflash settings --get download_settings.parallel_connections
dflash settings --set ui_port=8900
dflash settings --set download_settings.parallel_connections=4
dflash settings --json
```

Writable keys include `ui_port`, `gateway_port`, `dflash_root`,
`gateway_server_id`, `context_auto_grow`, `context_max`,
`download_settings.parallel_connections`, and
`hardware_settings.gpu_strategy`. Use `dflash api` for anything else.

---

## Machine and logs

```powershell
dflash hardware
dflash stats
dflash report
dflash logs
dflash logs --errors
dflash logs --engine gemma-12b-ar --tail 80
```

---

## Raw API

```powershell
dflash api GET /api/health
dflash api GET /api/models
dflash api GET "/api/models?source=ollama"
dflash api POST /api/models/load --body "{\"path\": \"C:\\\\models\\\\model.gguf\"}"
```

Use this when a dedicated command does not exist yet.

---

## Matching HTTP routes

| Command | Route |
|---------|-------|
| `list` | `GET /api/models` |
| `ps` | `GET /api/status/loaded` |
| `show` / `load` | `GET /api/models`, `POST /api/models/load` |
| `delete` | `DELETE /api/models/file` |
| `chat` | `POST /api/servers/{id}/v1/chat/completions` |
| `embed` | `POST /api/servers/{id}/v1/embeddings` |
| `nodes` | `GET/POST/DELETE /api/nodes` |
| `settings` | `GET/PUT /api/config` |
| `search` / `pull` | `GET /api/hf/search`, `POST /api/hf/download` |

`dflash list` query flags map to:

| Query | Meaning |
|-------|---------|
| `source=ollama` | Ollama models only |
| `source=lmstudio` | LM Studio library only |
| `source=dflash` | DFlash stacks / Console library |
| `source=library` | Console library files |
| `quick=1` | Engine profiles only |
| `refresh=1` | Rescan folders |

`GET /api/installed` groups the same models by library.

---

## Environment

| Variable | Meaning |
|----------|---------|
| `DFLASH_URL` | Console URL if you do not pass `--url` |
| `DFLASH_PORT` | Console port if you do not pass `--port` |
| `DFLASH_CONSOLE_ROOT` | Writable data folder (config, logs) |
| `DFLASH_ROOT` | llama-server / DFlash engine tree |

The full user walkthrough is [USER-GUIDE.md](./USER-GUIDE.md). The in-app copy
is **Documentation → Terminal CLI**.
