# Models View

## Layout
- **Left:** "My Models" table (scrollable)
- **Right:** Inspector for selected model (same Load/Inference tabs as Server)

## Table columns
| Column | Example |
|--------|---------|
| Arch | gemma4, qwen35moe |
| Params | 31B, 12B |
| Publisher | google, bartowski |
| LLM | model name + capability tags (instruct, chat, tools) |
| Quant | Q4_0, Q4_K_S |
| Size | 17.7 GB |
| Modified | 10 days ago |
| Actions | ⋯ menu, gear |

## Mock models (5 rows)
1. gemma-4-31b-it-dflash — google — 31B — Q4_0 — 17.7 GB
2. gemma-4-12b-it-qat — google — 12B — Q4_0 — 7.2 GB *(selected)*
3. qwen3.5-9b — qwen — 9B — Q4_K_S — 6.5 GB
4. deepseek-v2-lite — deepseekv2 — 12B — Q8_0 — 12.1 GB
5. laguna-8b — laguna — 9B — Q4_0 — 5.0 GB

## Toolbar
- Filter models search (Ctrl+F)
- Model Search button (opens HF modal)
- Downloads tray icon

## Footer
- "You have N local models, taking up X GB"
- Models directory path
