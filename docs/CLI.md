# DFlash Console CLI

The Console is the source of truth for models on this PC. The same library you
see in the **Models** tab is available from PowerShell with `dflash`, the same
way `ollama list` talks to Ollama.

The Console server must be running (`.\server.ps1` or `dflash serve`).

## Install the command

```powershell
.\dflash.ps1 install
dflash help
```

After install, type `dflash` in any new PowerShell window. From the repo folder
you can also run `.\dflash.ps1` or `.\dflash.cmd`.

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
| `--dflash` | DFlash stacks and Console library models |
| `--source NAME` | `ollama`, `lmstudio`, `dflash`, or `library` |
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
dflash list --source library --type llm
dflash list --filter gemma --json
```

## Other commands

| Command | What it does |
|---------|----------------|
| `dflash status` | Server health |
| `dflash ps` | Models loaded right now |
| `dflash engines` | Engine profiles |
| `dflash show <name>` | One model |
| `dflash load <name>` | Load a model |
| `dflash unload <name>` | Unload an engine or runtime |
| `dflash start / stop <engine>` | Start or stop an engine |
| `dflash chat "hello"` | Send a prompt |
| `dflash search qwen` | Search Hugging Face |
| `dflash pull org/repo --file name.gguf` | Download a file |
| `dflash downloads [--range 7]` | Current and last downloads |
| `dflash hardware` / `stats` / `report` | Machine status |
| `dflash logs` | Console or engine logs |
| `dflash api GET /api/models` | Call any Console route |
| `dflash open` | Open the UI |

Use `dflash <command> --help` for flags. Names can be short when they are unique.

## Matching API

`dflash list` reads `GET /api/models`, the same catalog as the Models tab.

| Query | Meaning |
|-------|---------|
| `source=ollama` | Ollama models only |
| `source=lmstudio` | LM Studio library only |
| `source=dflash` | DFlash stacks / Console library |
| `source=library` | Console library files |
| `quick=1` | Engine profiles only |
| `refresh=1` | Rescan folders |

`GET /api/installed` groups the same models by library.
