# STT Engine Spike — Decision Record

**Status:** Decision recorded · **Author:** ILAN AVIV · **Date:** 2026-08-08
**Gate:** Phase 1.5 (decision only — do not ship STT product yet).

## Question

Which single engine should power Console-proxied Speech-to-Text (`POST /v1/audio/transcriptions`)?

- **A. whisper.cpp `whisper-server`** (native binary, GGML/CUDA, OpenAI `-oa` API)
- **B. faster-whisper** (Python + ctranslate2, HF safetensors repos)

Per the plan: lock **one**, do not ship both. This record picks the engine for Phase 2.

## Comparison

| Criterion | A. whisper.cpp `whisper-server` | B. faster-whisper |
|-----------|--------------------------------|-------------------|
| **Windows packaging cost** | Native `whisper-server.exe` + ggml DLLs — same verified-zip staging as llama-server under `runtimes/`. Thin, proven on this machine (llama.cpp already built with CUDA 13.1). | Embedded Python + ctranslate2 + CUDA/cuDNN env bundle — hundreds of MB, fragile on Windows, "not the same as llama-server zip" per plan §9. |
| **OpenAI `/v1/audio/transcriptions` fit** | Native OpenAI `-oa` server on a loopback port; multipart out of the box. | Needs a custom FastAPI worker + multipart proxy (add `python-multipart`). |
| **Process identity / adopt / cleanup** | Long-lived loopback server → reuses existing `managed_process_identity`, `_kill_listener_on_port`, adopt, shared port registry, and `server.ps1` tokens unchanged (just register a `whisper-server` token). | Separate Python worker → new identity token, new port, new adopt/cleanup path, Python env lifecycle. |
| **Catalog model source** | GGUF-whisper repos (ggml-org whisper.cpp gguf conversion) → fits existing single-file GGUF catalog + download path (`kind: file`, `runtime_id: llama-server`-style). | HF safetensors **repo snapshots** (`kind: repo`) → new repo-download plumbing in the catalog. |
| **Quality / translate** | Good with `whisper-large-v3-turbo`; `--translate` supported. Slightly behind faster-whisper on multilingual/translate quality. | Best-in-class multilingual + translate; native HF model ecosystem. |

## Decision

**Lock: A — whisper.cpp `whisper-server` for Phase 2 (v1 STT).**

Rationale: it reuses the Console's entire native-runtime machinery (packaging, process identity, ports, adopt, cleanup, GGUF catalog, single-file downloads) that Phase 0–1 just built. faster-whisper's quality/translate edge does not justify a parallel Python-env packaging + process lifecycle for v1, and the plan explicitly keeps the packaging cost low and Windows-first.

## Implications for Phase 2 (STT)

- Build/acquire `whisper-server.exe` (whisper.cpp, `GGML_CUDA=ON` — same flow as llama.cpp on this machine).
- Bundle under `runtimes/stt/` (or `runtimes/whisper/`) with a `manifest.json` + Repair/Reinstall path.
- Register process-identity token (`whisper-server`) in Python + `server.ps1` manifest.
- Reuse the shared port registry (`suggest_runtime_port`) — STT gets a server-mode runtime (port > 0), unlike Piper.
- Console proxy `POST /api/runtimes/stt/v1/audio/transcriptions` (OpenAI shape) — add `python-multipart` to `requirements.txt`.
- Catalog: GGUF-whisper models (single-file download) with `modality: speech-to-text`, `runtime_id: stt`.
- Playground **Transcribe** tab (mic/file → transcript).
- GPU default, CPU fallback with honest "slow" warning; approximate VRAM label (not a hard gate).
- Stop-others: Console-owned STT → stop prompt; external whisper/Ollama → warn by name only.

## Revisit triggers

Revisit B (faster-whisper) only if STT becomes a primary multilingual/translate workload, or if GGUF-whisper quality proves insufficient for the user's languages. Do **not** ship both in parallel.

## References

- `docs/MULTI-MODAL-RUNTIME-PLAN.md` §5.5 (OpenAI proxy), §9 (packaging), Phase 1.5 (decision gate), §11 locked decision #2 (spike first).
