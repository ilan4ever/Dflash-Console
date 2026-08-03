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

- **All models** — every discovered local model.
- **DFlash stacks** — runnable target-plus-accelerator pairs.
- **Accelerators** — draft checkpoint files.
- **Downloading** — active Hugging Face transfers.
- **Loaded** — models currently loaded on an engine.
- Model type filters include LLM/chat, DFlash, OCR, translation,
  speech-to-text, text-to-speech, embeddings, vision, and other.

## Toolbar
- Filter models search (`Ctrl+F`)
- **Create DFlash stack** wizard
- Model catalog and download tray are available from the sidebar

## Footer
- Library model count and total disk usage
- Current model library path
- Filter or scan status
