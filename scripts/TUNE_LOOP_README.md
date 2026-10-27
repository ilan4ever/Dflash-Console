# tune_loop.py - idle-only tuning loop for the OneVoice 31B engine

Added 2026-10-21. To remove it, delete `scripts\tune_loop.py`, this README and `logs\tune_loop\`.

## What it does
1. Copies the live settings of DFlash Console profile `gemma-31b-it-quant-dflash` (model, DFlash draft, mmproj, batch, ubatch, KV type, draft length) as the **baseline**.
2. Builds a replay set from local OneVoice data. Nothing leaves the PC.
   - **chat:** real session history at about 4k, 12k and 26k tokens, the sizes seen in the 31B log. The system prompt is the OneVoice manifest doc unless `--system-prompt-file` gives the exact one.
   - **fluent:** the last real voice turns from `logs\fluent_conversation.log`.
   - **memory:** summarizer, observer and profile style tasks over real transcripts.
   - Use `--prompts-file` (JSONL) to replay exact captured requests instead.
3. Starts its **own** llama-server on port 8199 (hidden), changing one setting at a time:
   - `spec_draft_n_max` 4/5/6/8
   - `ubatch` 512/1024/2048
   - `batch` 2048/4096
   - `cache_type` q4_0/q8_0
   - `cache_ram` 16384/32768
4. For each setting it records:
   - time to first token (TTFT) for chat, Fluent and memory prompts
   - prompt tokens/s, generation tokens/s and draft acceptance
   - a prefix-cache test (chat → memory agent → the same chat plus one turn)
5. Quality check: 16 short questions with known answers plus 3 long-context "needle" questions (about 4k, 16k and 30k tokens). A config passes if it errors nowhere, finds every needle, and scores at most 1 below the baseline.
6. Runs one final config that combines the per-axis winners. It then writes `results.md`, `results.json` and a recommended ini fragment to `logs\tune_loop\<timestamp>\`.

## Safety
- It refuses to start unless all of these pass:
  - :8890 has no active chat replies and no image jobs.
  - The Fluent heartbeat is older than 90 s.
  - No session file was written in the last 10 min.
  - No memory or nightly job process is running.
  - It is not between 02:45 and 05:00.
  - GPU 0 is the 4090 and under 20 % busy.
  - The GPU has enough **free** VRAM for the biggest config, estimated from the GGUF header.
- While it runs, a watchdog repeats the idle checks every 15 s. If OneVoice gets busy, it stops the test server straight away.
- It never connects to, reloads or unloads the live engines, and it refuses to use any port a Console profile owns. It only stops the process it started itself.
- It never changes any config. Applying the recommendation is a separate, approved step.

## Run (hidden)
```
pythonw C:\Users\ilanp\tools\hidden-run\run_job.py --log C:\dev\Dflash-Console\logs\tune_loop\run.log -- ^
  C:\dev\Dflash-Console\.venv\Scripts\python.exe C:\dev\Dflash-Console\scripts\tune_loop.py --dry-run
```
- Drop `--dry-run` for a real sweep. That needs Ilan's OK and about 26 GB free on the 4090, so the other loaded models must be unloaded first by hand.
- `--capture-only` only writes the replay set.
- `--axes ubatch,spec_draft_n_max` limits the sweep.
- `--no-mmproj` saves about 1 GB.
- `--repeats 2` gives steadier numbers.

A full sweep is about 12 configs plus 1 combined. Each one loads the model (about 1-2 min) and replays everything, so expect roughly 40-70 min.
