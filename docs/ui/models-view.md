# Models view

## Layout
- **Main:** scrollable local model table
- **Right:** Runtime inspector for the selected model (when enabled)
- The view is populated from configured model libraries and current engine state.

## Table columns
| Column | Example |
|--------|---------|
| Model | Display name and load state |
| Family | Model family |
| Scale | Parameter scale |
| Source | Library or provider |
| Disk | File size |
| Updated | Last file update |
| Actions | Load, details, or menu actions |

## Filters

- **All models** — every discovered local model, including Ollama and LM Studio.
- **DFlash stacks** — runnable target-plus-accelerator pairs.
- **Accelerators** — draft checkpoint files.
- **Loaded** — models currently loaded on an engine.

Active Hugging Face transfers and last downloads are on the **Downloads** page.

The same library is available from PowerShell: `dflash list`,
`dflash list --ollama`, `dflash list --lmstudio`, `dflash list --dflash`,
`dflash delete <name>`. See [CLI.md](../CLI.md).
- Model type filters include LLM/chat, DFlash, OCR, translation,
  speech-to-text, text-to-speech, embeddings, vision, and other. Filters wire to
  the catalog's backend `modality` field (not filename heuristics).
- Each row shows a **modality badge** (LLM, Embed, STT, TTS, Vision, OCR,
  Translate).
- **HF accelerators available** — local target models with a compatible DFlash
  or DSpark accelerator currently listed in the Hugging Face catalog.

The Hugging Face filter checks the live catalog when selected. It requires
network access and never treats a standalone accelerator file as a target
model. It includes models discovered from browse-only folders, such as an
unconfigured LM Studio folder. The context menu can open the stack wizard for
these entries, where you can choose an installed accelerator or download the
matching Hugging Face accelerator.

Models from different enabled libraries are shown as separate rows so users can
distinguish DFlash Console files from LM Studio files. The Source column and
source badges identify the library origin; a DFlash stack keeps its DFlash
badge, while a model found under LM Studio is labeled “LM Studio library.”

When identical model files exist in more than one enabled library, each retained
row is marked with the number of copies and the relevant source names. The
Console does not delete or move any file. Same-path duplicates are still
deduplicated.

The library label describes the detected folder source only. LM Studio is a
trademark of Element Labs, Inc.; DFlash Console is independent and is not
affiliated with or endorsed by LM Studio. Model files remain subject to the
licenses and terms provided by their respective authors or distributors. The
Console scans only user-enabled folders and does not redistribute model
weights.

## Toolbar
- Filter models search (`Ctrl+F`)
- **Create DFlash stack** wizard
- Model catalog and download tray are available from the sidebar

## Footer
- Library model count and total disk usage
- Current model library path
- Filter or scan status
