from pathlib import Path

gw = Path("api/gateway.py")
text = gw.read_text(encoding="utf-8")
old = '''Routes:
    GET  /v1/models               aggregated model list
    POST /v1/chat/completions     chat on the default engine (streaming)
    POST /v1/embeddings           embeddings on the default embedding engine
    POST /v1/audio/speech         Piper TTS (WAV)
    POST /v1/audio/transcriptions Whisper STT (multipart)
    GET  /health                  gateway + console health
    GET  /                        info

The default chat engine is ``config.json -> gateway_server_id`` (falls back to
the first enabled non-embedding engine); embeddings route to the first enabled
embedding engine. The gateway is started on the UI process lifespan, so it is
stopped and started together with the console.
'''
new = '''Routes:
    GET  /v1/models               aggregated model list
    POST /v1/chat/completions     chat on the default engine (streaming)
    POST /v1/embeddings           embeddings on the default embedding engine
    POST /v1/audio/speech         Piper TTS (WAV)
    POST /v1/audio/transcriptions Whisper STT (multipart)
    GET  /health                  gateway + console health
    GET  /                        info

Concurrent OpenAI clients on the same loaded engine (for example DeepSeek Harness
main turn + session title) are accepted: the Console queues the ready/start
section per ``server_id``, then llama-server multiplexes within ``parallel_slots``.
Steady-state overlaps must not return HTTP 409. Intentional 409s remain for
``X-DFlash-Strict-Model`` mismatches, duplicate checkpoint on another engine,
DFlash stack / vision repair, and load/unload conflicts.

The default chat engine is ``config.json -> gateway_server_id`` (falls back to
the first enabled non-embedding engine); embeddings route to the first enabled
embedding engine. The gateway is started on the UI process lifespan, so it is
stopped and started together with the console.
'''
if old not in text:
    raise SystemExit("gateway docstring block not found")
gw.write_text(text.replace(old, new, 1), encoding="utf-8")
print("gateway docstring updated")

readme = Path("README.md")
rt = readme.read_text(encoding="utf-8")
needle = "The gateway routes chat, embeddings, TTS, and STT to the loaded engine."
insert = """The gateway routes chat, embeddings, TTS, and STT to the loaded engine.

**Concurrent chat:** multiple OpenAI-compatible clients may call `chat/completions` on the same loaded model at once (Harness agent turn + title, or two UI actions). The Console serializes ready/JIT per engine, then lets llama-server use parallel slots. You should not see `upstream HTTP 409` for that steady-state overlap. HTTP 409 is still used for strict model mismatch (`X-DFlash-Strict-Model`), a checkpoint already loaded on a *different* engine, and stack/vision repair."""
if needle not in rt:
    raise SystemExit("README needle not found")
if "Concurrent chat:" not in rt:
    rt = rt.replace(needle, insert, 1)
    readme.write_text(rt, encoding="utf-8")
    print("README updated")
else:
    print("README already has concurrent note")
