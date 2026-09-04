# DFlash Console

Local Windows control panel for **DFlash speculative-decoding stacks** and a
unified model runtime. Load GGUF, Hugging Face, speech, and embedding models
from one UI, then talk to them through a single OpenAI-compatible port.

> **Status:** Public preview for local, single-user Windows use.

**Developer:** ILAN AVIV · **UI:** [http://127.0.0.1:8900/](http://127.0.0.1:8900/) · **Version:** v0.3.139

## Download (Windows)

| Package | Where |
|---------|--------|
| **Setup installer** (recommended) | [Latest GitHub Release](https://github.com/ilan4ever/Dflash-Console/releases/latest) — `DFlash-Console-Setup-*-x64.exe` |
| **Portable** | Same page — `DFlash-Console-Portable-*-x64.exe` |
| **CLI only** | `pip install dflash-console` then `dflash serve` |

Installed desktop apps check for updates from the latest GitHub Release
(`latest.json` + the setup EXE). Windows may show an Unknown publisher warning
because the installer is unsigned.

---

## Engines

Pick an engine in **Engines**, **Models**, or **Playground** before you load.

| Engine | Models | When to use it |
|--------|--------|----------------|
| **DFlash / llama-server** | GGUF chat, vision, OCR, embeddings | Default local path. Supports **DFlash 1** and **DFlash 2** draft accelerators. |
| **vLLM** | Hugging Face SafeTensors | Fast NVIDIA GPU path for large HF models. Optional; installs on demand. |
| **Transformers** | Hugging Face SafeTensors | Works on more PCs (CPU or GPU). Slower than vLLM. Optional; installs on demand. |
| **FreeToken** | Large HF MoE folders via WSL2 | For very large models on lower-VRAM machines. Needs WSL2 + NVIDIA/CUDA. |

Install the optional engines from **Settings → Downloads & engines** or the
first-run wizard. They stay out of the Windows installer so the download stays
small.

### DFlash 1 and DFlash 2

A DFlash **stack** is a target GGUF plus a smaller **draft / accelerator** used
for speculative decoding.

| Generation | What it is |
|------------|------------|
| **DFlash 1** | Original DFlash draft family. Works with the bundled llama-server. |
| **DFlash 2** | Newer draft family (Gemma 4 / Qwen 3.8 and similar). Needs a llama.cpp build with DFlash 2 support. |

The catalog can filter **DFlash** and **DFlash 2** separately. On a target
model, right-click **Find and attach draft** — the app searches the local
library and Hugging Face, checks architecture compatibility, then registers
and attaches the matching draft.

---

## What else it does

| Area | What you get |
|------|--------------|
| **Models** | Full PC library: DFlash stacks, GGUF, Ollama, LM Studio, Hugging Face folders, OCR, speech, embeddings |
| **Catalog** | Search and download Hugging Face models into your library folders |
| **Playground** | **Chat · Speak · Transcribe · Embed** against the loaded engine |
| **Gateway** | One OpenAI URL: `http://127.0.0.1:8001/v1` |
| **Speech** | Piper TTS and whisper.cpp STT |
| **CLI** | `dflash list`, `load`, `chat`, `search`, `pull` — same library as the UI |
| **Docs** | In-app user guide, CLI reference, and API catalog |

---

## Quick start

```powershell
pip install dflash-console
dflash serve
```

Or install the Windows EXE and open the app. The UI is
**http://127.0.0.1:8900/**.

Typical first session:

1. Open **Engines** and pick **DFlash**, **vLLM**, **Transformers**, or **FreeToken**.
2. Load a model from the dropdown or the **Models** tab.
3. For a DFlash GGUF, right-click and **Find and attach draft** if you want speculative decoding.
4. Chat in the **Playground**, or point any OpenAI client at `http://127.0.0.1:8001/v1`.

From a git checkout: copy `config.example.json` to `config.json`, then
`.\server.ps1`. Full walkthrough: [docs/USER-GUIDE.md](./docs/USER-GUIDE.md).

---

## Terminal CLI

```powershell
dflash serve
dflash list
dflash list --dflash
dflash list --vllm
dflash list --transformers
dflash load qwen
dflash chat "hello"
dflash search "qwen dflash2"
```

The Console must be running except for `help`, `version`, `serve`, and
`install`. Full command list: [docs/CLI.md](./docs/CLI.md).

---

## OpenAI gateway and API

Point clients at:

```
http://127.0.0.1:8001/v1
```

The gateway routes chat, embeddings, TTS, and STT to the loaded engine.
Model names are tolerant (engine id, file name, or an alias such as `gpt-4o`).

Selected Console routes (UI/API on port **8900**):

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/servers` | Engine status |
| `POST` | `/api/models/load` | Load any catalog model; pick engine with `runtime_id` |
| `POST` | `/api/servers/{id}/v1/chat/completions` | Chat through a llama-server / DFlash engine |
| `POST` | `/api/servers/vllm/v1/chat/completions` | Chat through vLLM |
| `POST` | `/api/servers/transformers/v1/chat/completions` | Chat through Transformers |
| `POST` | `/api/servers/freetoken/v1/chat/completions` | Chat through FreeToken |
| `POST` | `/api/stacks/find-and-attach-draft` | Find a compatible DFlash 1 or DFlash 2 draft and attach it |
| `POST` | `/api/runtimes/piper/v1/audio/speech` | Piper TTS |
| `POST` | `/api/runtimes/stt/v1/audio/transcriptions` | Whisper STT |
| `GET` | `/api/models` | Full local library |
| `GET` | `/api/runtimes` | All runtimes and adapters |
| `GET` | `/api/docs/catalog` | In-app documentation |

Load a Hugging Face folder on a specific engine:

```bash
curl -X POST http://127.0.0.1:8900/api/models/load \
  -H "Content-Type: application/json" \
  -d "{\"path\": \"C:\\\\models\\\\org\\\\model\", \"runtime_id\": \"vllm\"}"
```

Use `"runtime_id": "transformers"` or `"freetoken"` the same way.

Full list: **Documentation** in the app, or Swagger at
`http://127.0.0.1:8900/docs`.

---

## Requirements

- Windows 10+
- Python 3.10+
- PowerShell 7+ (`pwsh`)
- NVIDIA GPU recommended for multi-model loads
- Optional: WSL2 Ubuntu + CUDA for FreeToken
- Optional: Node.js 22.12+ only if you build the Electron shell from source

---

## Configuration and security

Copy `config.example.json` → `config.json` (never commit the live file).

| Setting | Purpose |
|---------|---------|
| `servers[]` | llama-server / DFlash GGUF profiles |
| `runtimes[]` | Piper, Whisper, vLLM, Transformers, FreeToken |
| `model_libraries[]` | Folders scanned for local models |
| `gateway_port` | OpenAI gateway (default 8001) |

The Console binds to loopback. It is for one trusted user on one PC. Do not
expose ports 8900 or 8001 to a LAN or the internet without adding your own
auth. Keep Hugging Face tokens in the environment (`HF_TOKEN`), not in
`config.json`.

---

## Community

- Questions: [Discussions](https://github.com/ilan4ever/Dflash-Console/discussions)
- Bugs: [Issues](https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml)
- Security: [SECURITY.md](./SECURITY.md)

Include the version from **About**. Do not paste `config.json`, tokens, or
model weights.

## License

GNU AGPL v3 or later. See [LICENSE](./LICENSE), [NOTICE.md](./NOTICE.md), and
[TRADEMARKS.md](./TRADEMARKS.md).

DFlash Console is developed by **ILAN AVIV**. Related project:
[DFlash](https://github.com/z-lab/DFlash).
