# Production readiness

This checklist captures what it takes to move DFlash Console — including the
multi-modal runtimes (Piper TTS, whisper.cpp STT, embeddings) — from a
developer checkout to a trusted production deployment.

## 1. Code & tests

- [ ] `python -m pytest -q` green (adapter + lifecycle + catalog suites)
- [ ] `python scripts/release-preflight.py` passes
- [ ] `node --check electron/main.js` clean
- [ ] `npm audit --audit-level=high` clean

## 2. Runtime bundles (Piper / Whisper)

- [ ] `runtimes/piper/` contains the verified Piper binary + `espeak-ng-data`
- [ ] `runtimes/piper/voices/*.onnx(+.json)` present; `manifest.json` written
- [ ] `runtimes/stt/` contains `whisper-server.exe` + `ggml-*.dll` (incl. CUDA)
- [ ] `runtimes/stt/manifest.json` written
- [ ] `runtimes/process-tokens.json` regenerated at boot
- [ ] Smoke: **Speak** → WAV; **Transcribe** → text; **Embed** → vectors + `.jsonl`
- [ ] Unload/stop leaves no orphan piper/whisper processes; VRAM recovers

## 3. Dependencies

- [ ] `requirements.lock` is the single source of truth (pinned, SHA-verified)
- [ ] `python-multipart==0.0.20` pinned (STT multipart proxy)
- [ ] No `pip install` from the UI; verified native bundles only

## 4. Security posture

- [ ] Console + all children bind loopback only
- [ ] `validate_config` rejects non-loopback host/`api_url` and port collisions
- [ ] Process identity uses path-specific tokens (never bare exe names)
- [ ] Child spawn uses fixed argument lists (no shell)
- [ ] Downloads restricted to allowed library roots; extension/path checks

## 5. Packaging (Windows)

- [ ] Electron installer stays thin; `runtimes/` + weights live in the data root
- [ ] Version bumped via `scripts/bump-version.ps1` (package, lockfile, About, README)
- [ ] Windows artifacts signed before public distribution
- [ ] Update manifest + update endpoint deployed (see `automatic-updates.md`)

## 6. Docs

- [ ] `README.md` capability matrix current
- [ ] `docs/ARCHITECTURE.md`, `docs/USER-GUIDE.md`, `docs/ui/*` current
- [ ] Docs deployed to the public site via `scripts/deploy-docs.ps1`
