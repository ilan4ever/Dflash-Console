#!/usr/bin/env python3
"""tune_loop.py - idle-only llama-server tuning loop for the OneVoice 31B engine.

Idea taken from "I asked Qwen to Optimise Itself" (Weights & Wonders, 2026-10-19):
restart a server with one changed setting, replay a fixed workload, time it, check
quality, keep the winner. Here it runs against a SEPARATE test llama-server on its
own port. It never talks to, reloads or unloads the live engines, and it refuses to
run unless OneVoice is idle and the GPU has enough free memory.

Stdlib only. Windows: run hidden, e.g.
  pythonw C:\\Users\\ilanp\\tools\\hidden-run\\run_job.py --log <log> -- ^
      C:\\dev\\Dflash-Console\\.venv\\Scripts\\python.exe scripts\\tune_loop.py --dry-run
See scripts\\TUNE_LOOP_README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CONSOLE_ROOT = Path(r"C:\dev\Dflash-Console")
ONEVOICE_ROOT = Path(r"C:\dev\OneVoice")
DEFAULT_SERVER_ID = "gemma-31b-it-quant-dflash"
DEFAULT_TEST_PORT = 8199
GiB = 1024 ** 3
MiB = 1024 ** 2

# One axis is changed at a time from the baseline. "default" = flag omitted.
SWEEP_AXES: dict[str, list] = {
    "spec_draft_n_max": [4, 5, 6, 8],
    "ubatch": [512, 1024, 2048],
    "batch": [2048, 4096],
    "cache_type": ["q4_0", "q8_0"],
    "cache_ram": ["default", 16384, 32768],
}

MEMORY_JOB_PATTERNS = re.compile(
    r"consolidat|backfill_shared_mind|shared_mind_distiller|seed_communal|harvest_shared|memory_nightly",
    re.I,
)


def log(msg: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def run_hidden(cmd: list[str], timeout: float = 30.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace",
        )
        return out.stdout or ""
    except Exception as exc:  # noqa: BLE001
        return f"__ERROR__ {exc}"


def http_json(url: str, body: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


# --------------------------------------------------------------------------- GGUF
_GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def read_gguf_meta(path: Path, wanted=("block_count", "head_count_kv", "key_length", "value_length",
                                       "sliding_window", "key_length_swa", "value_length_swa")) -> dict:
    """Read a few header keys from a GGUF file (header only, read-only)."""
    out: dict = {}
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError(f"not a GGUF file: {path}")
        _ver, _nt, nkv = struct.unpack("<IQQ", f.read(20))

        def rstr():
            (n,) = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", "replace")

        def rval(t):
            if t in _GGUF_SCALARS:
                fmt = _GGUF_SCALARS[t]
                return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
            if t == 8:
                return rstr()
            if t == 9:
                (it,) = struct.unpack("<I", f.read(4))
                (n,) = struct.unpack("<Q", f.read(8))
                if it == 8 or n > 4096:
                    for _ in range(n):
                        rval(it)
                    return f"<array {n}>"
                return [rval(it) for _ in range(n)]
            raise ValueError(f"unknown gguf type {t}")

        for _ in range(nkv):
            key = rstr()
            (t,) = struct.unpack("<I", f.read(4))
            val = rval(t)
            short = key.split(".", 1)[-1]
            if short.startswith("attention."):
                short = short[len("attention."):]
            if short in wanted or short == "sliding_window_pattern":
                out[short] = val
    return out


BYTES_PER_ELEM = {"q4_0": 18 / 32, "q4_1": 20 / 32, "q5_0": 22 / 32, "q5_1": 24 / 32, "q8_0": 34 / 32,
                  "f16": 2.0, "bf16": 2.0, "f32": 4.0}


def kv_bytes(meta: dict, ctx: int, ubatch: int, cache_type: str, slots: int = 1) -> int:
    n = int(meta.get("block_count") or 0)
    kvh = meta.get("head_count_kv")
    kvh = kvh if isinstance(kvh, list) else [int(kvh or 0)] * n
    pat = meta.get("sliding_window_pattern")
    pat = pat if isinstance(pat, list) else [False] * n
    swa = int(meta.get("sliding_window") or 0)
    kl, vl = int(meta.get("key_length") or 128), int(meta.get("value_length") or 128)
    kls, vls = int(meta.get("key_length_swa") or kl), int(meta.get("value_length_swa") or vl)
    bpe = BYTES_PER_ELEM.get(cache_type, 2.0)
    per_slot = ctx // max(1, slots)
    total = 0.0
    for i in range(n):
        is_swa = bool(pat[i]) if i < len(pat) else False
        if is_swa and swa:
            cells = min(per_slot, swa + ubatch) * slots
            total += cells * kvh[i] * (kls + vls) * bpe
        else:
            total += ctx * kvh[i] * (kl + vl) * bpe
    return int(total)


# ------------------------------------------------------------------ baseline config
def load_console_server(server_id: str) -> dict:
    cfg = json.loads((CONSOLE_ROOT / "config.json").read_text(encoding="utf-8"))
    for s in cfg.get("servers") or []:
        if s.get("id") == server_id:
            return {"server": s, "all_ports": sorted({int(x.get("port") or 0) for x in cfg["servers"] if x.get("port")})}
    raise SystemExit(f"server id not found in Console config: {server_id}")


def parse_preset(path: Path) -> dict:
    out: dict = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def build_baseline(server_id: str, args) -> dict:
    info = load_console_server(server_id)
    s = info["server"]
    preset = parse_preset(CONSOLE_ROOT / "logs" / "presets" / f"{server_id}.ini")
    load = s.get("load_settings") or {}
    base = {
        "binary": str(CONSOLE_ROOT / "llama.cpp" / "build" / "bin" / "Release" / "llama-server.exe"),
        "model": s.get("target_path") or preset.get("model"),
        "draft": s.get("draft_path") or preset.get("model-draft"),
        "mmproj": None if args.no_mmproj else (s.get("mmproj_path") or preset.get("mmproj")),
        "spec_type": preset.get("spec-type", "draft-dflash"),
        "spec_draft_n_max": int(preset.get("spec-draft-n-max", 8)),
        "ctx": int(args.ctx),
        "slots": int(args.slots),
        "ngl": int(load.get("gpu_layers") or 99),
        "threads": int(load.get("cpu_threads") or 9),
        "batch": int(load.get("eval_batch_size") or preset.get("b", 2048)),
        "ubatch": int(load.get("physical_batch_size") or preset.get("ub", 512)),
        "cache_type": preset.get("cache-type-k", "q4_0"),
        "cache_ram": "default",
        "main_gpu": int(args.main_gpu),
        "inference": s.get("inference_settings") or {},
        "live_ports": info["all_ports"],
        "live_port": int(s.get("port") or 0),
    }
    return base


def server_cmd(cfg: dict, port: int) -> list[str]:
    cmd = [cfg["binary"], "--host", "127.0.0.1", "--port", str(port), "-m", cfg["model"],
           "-c", str(cfg["ctx"]), "-np", str(cfg["slots"]), "-ngl", str(cfg["ngl"]),
           "-t", str(cfg["threads"]), "-fa", "on", "-b", str(cfg["batch"]), "-ub", str(cfg["ubatch"]),
           "--cache-type-k", str(cfg["cache_type"]), "--cache-type-v", str(cfg["cache_type"]),
           "--main-gpu", str(cfg["main_gpu"]), "--split-mode", "none", "--jinja", "--kv-offload"]
    if cfg.get("draft"):
        cmd += ["--model-draft", cfg["draft"], "--spec-type", cfg["spec_type"],
                "--spec-draft-n-max", str(cfg["spec_draft_n_max"]), "--n-gpu-layers-draft", "all"]
    if cfg.get("mmproj"):
        cmd += ["--mmproj", cfg["mmproj"]]
    if cfg.get("cache_ram") not in (None, "default"):
        cmd += ["--cache-ram", str(cfg["cache_ram"])]
    return cmd


def estimate_vram(cfg: dict) -> dict:
    files = [p for p in (cfg.get("model"), cfg.get("draft"), cfg.get("mmproj")) if p]
    weights = sum(Path(p).stat().st_size for p in files if Path(p).is_file())
    kv_main = kv_draft = 0
    notes = []
    try:
        kv_main = kv_bytes(read_gguf_meta(Path(cfg["model"])), cfg["ctx"], cfg["ubatch"], cfg["cache_type"], cfg["slots"])
    except Exception as exc:  # noqa: BLE001
        notes.append(f"main KV estimate failed: {exc}")
        kv_main = int(3 * GiB)
    if cfg.get("draft"):
        try:
            kv_draft = kv_bytes(read_gguf_meta(Path(cfg["draft"])), cfg["ctx"], cfg["ubatch"], "f16", cfg["slots"])
        except Exception as exc:  # noqa: BLE001
            notes.append(f"draft KV estimate failed: {exc}")
            kv_draft = int(0.5 * GiB)
    compute = int((0.9 + 0.9 * cfg["ubatch"] / 512) * GiB)
    headroom = int(1.0 * GiB)
    total = weights + kv_main + kv_draft + compute + headroom
    return {"weights_gib": weights / GiB, "kv_main_gib": kv_main / GiB, "kv_draft_gib": kv_draft / GiB,
            "compute_gib": compute / GiB, "headroom_gib": headroom / GiB, "total_gib": total / GiB, "notes": notes}


# ------------------------------------------------------------------ guards
def gpu_state(index: int) -> dict:
    out = run_hidden(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.free,memory.total,utilization.gpu",
                      "--format=csv,noheader,nounits"])
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6 and parts[0] == str(index):
            return {"index": index, "name": parts[1], "used_mib": int(parts[2]), "free_mib": int(parts[3]),
                    "total_mib": int(parts[4]), "util_pct": int(parts[5])}
    return {"index": index, "error": out.strip()[:300] or "nvidia-smi gave no data"}


def memory_jobs_running() -> list[str]:
    if os.name != "nt":
        return []
    out = run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "Get-CimInstance Win32_Process | ForEach-Object { [string]$_.ProcessId + ' ' + [string]$_.CommandLine }"],
                     timeout=60)
    me = str(os.getpid())
    hits = []
    for line in out.splitlines():
        if MEMORY_JOB_PATTERNS.search(line) and not line.startswith(me + " ") and "tune_loop" not in line:
            hits.append(line.strip()[:200])
    return hits


def newest_session_age_minutes() -> float | None:
    newest = 0.0
    root = ONEVOICE_ROOT / "memory" / "users"
    if not root.is_dir():
        return None
    for user in root.iterdir():
        sess = user / "sessions"
        if not sess.is_dir():
            continue
        for p in sess.glob("session_*.json"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    return None if not newest else (time.time() - newest) / 60.0


def idle_report(args) -> dict:
    """Every check must pass. Read-only: only OneVoice status endpoints, files, nvidia-smi."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    code, jobs = http_json(f"{args.onevoice_url}/api/chat/active-jobs", timeout=5)
    if code == 200:
        add("onevoice chat replies", int(jobs.get("count") or 0) == 0, f"active={jobs.get('count')}")
    else:
        add("onevoice chat replies", args.allow_onevoice_down, f"{args.onevoice_url} unreachable (code {code})"
            + ("; allowed by --allow-onevoice-down" if args.allow_onevoice_down else "; refusing (use --allow-onevoice-down)"))
    code, img = http_json(f"{args.onevoice_url}/api/imagegen/bg-jobs", timeout=5)
    add("onevoice image jobs", code != 200 or int(img.get("active") or 0) == 0,
        f"active={img.get('active') if code == 200 else 'n/a'}")
    hb = ONEVOICE_ROOT / "logs" / "fluent_ui_heartbeat.json"
    hb_age = (time.time() - hb.stat().st_mtime) if hb.is_file() else None
    add("fluent voice mode", hb_age is None or hb_age > 90,
        "no heartbeat file" if hb_age is None else f"heartbeat {hb_age:.0f}s ago")
    age = newest_session_age_minutes()
    add("recent chat activity", age is None or age >= args.min_quiet_minutes,
        f"newest session write {age:.1f} min ago (need >= {args.min_quiet_minutes})" if age is not None else "no sessions")
    jobs_running = memory_jobs_running()
    add("memory agents / nightly jobs", not jobs_running, "; ".join(jobs_running) or "none running")
    now = dt.datetime.now().time()
    in_window = dt.time(2, 45) <= now <= dt.time(5, 0)
    add("nightly consolidator window (02:45-05:00)", not in_window or args.ignore_nightly_window,
        f"now {now.strftime('%H:%M')}")
    samples = [gpu_state(args.gpu_index).get("util_pct", 100) for _ in range(3) if not time.sleep(0.7)]
    util = statistics.mean(samples) if samples else 100
    add("gpu busy", util < args.max_gpu_util, f"util avg {util:.0f}% over 3 samples (limit {args.max_gpu_util}%)")
    return {"idle": all(c["ok"] for c in checks), "checks": checks}


def vram_report(cfg: dict, args) -> dict:
    est = estimate_vram(cfg)
    g = gpu_state(args.gpu_index)
    need_mib = est["total_gib"] * 1024
    ok = "free_mib" in g and g["free_mib"] >= need_mib
    if args.expect_gpu and "name" in g and args.expect_gpu.lower() not in g["name"].lower():
        ok = False
        est["notes"].append(f"GPU {args.gpu_index} is '{g['name']}', expected '{args.expect_gpu}'")
    return {"ok": ok, "need_mib": round(need_mib), "gpu": g, "estimate": est}


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


# ------------------------------------------------------------------ prompt capture
def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.6))


def _session_messages(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    msgs = []
    for m in data.get("messages") or []:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            msgs.append({"role": role, "content": content.strip()})
    return msgs


def _system_text(args) -> str:
    if args.system_prompt_file and Path(args.system_prompt_file).is_file():
        return Path(args.system_prompt_file).read_text(encoding="utf-8", errors="replace")
    manifest = ONEVOICE_ROOT / "memory" / "core" / "long_term" / "platform" / "docs" / "onevoice-system-manifest.md"
    base = ("You are OneVoice, Ilan's personal AI assistant. Reply warmly and concisely, in the user's language. "
            "Use the context below when it helps.\n\n")
    if manifest.is_file():
        base += manifest.read_text(encoding="utf-8", errors="replace")
    return base


def capture_prompts(args) -> list[dict]:
    """Rebuild a replay set from real local OneVoice data (stays on this PC)."""
    if args.prompts_file:
        rows = [json.loads(l) for l in Path(args.prompts_file).read_text(encoding="utf-8").splitlines() if l.strip()]
        log(f"loaded {len(rows)} prompts from {args.prompts_file}")
        return rows
    rows: list[dict] = []
    sessions = []
    for user in (ONEVOICE_ROOT / "memory" / "users").iterdir():
        sd = user / "sessions"
        if not sd.is_dir():
            continue
        for p in sd.glob("session_*.json"):
            if "TEST" in p.name.upper():
                continue
            sessions.append(p)
    sessions.sort(key=lambda p: p.stat().st_size, reverse=True)
    system = _system_text(args)

    # chat: long real histories at the sizes seen in the 31B log (median ~4k, p90 ~12k, max ~26k tokens)
    pool: list[dict] = []
    for p in sessions[:60]:
        pool.extend(_session_messages(p))
        if _approx_tokens("".join(m["content"] for m in pool)) > 40000:
            break
    for target in (4000, 12000, 26000):
        hist, used = [], _approx_tokens(system)
        for m in reversed(pool):
            t = _approx_tokens(m["content"])
            if used + t > target:
                break
            hist.insert(0, m)
            used += t
        while hist and hist[-1]["role"] != "user":
            hist.pop()
        if not hist:
            continue
        rows.append({"kind": "chat", "name": f"chat_{target // 1000}k", "max_tokens": 400,
                     "messages": [{"role": "system", "content": system}] + hist, "approx_tokens": used})

    # fluent: short voice turns from logs/fluent_conversation.log
    flog = ONEVOICE_ROOT / "logs" / "fluent_conversation.log"
    turns = []
    if flog.is_file():
        for line in flog.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\[FLUENT\] (user|assistant): (.+)$", line)
            if m:
                turns.append({"role": m.group(1), "content": m.group(2).strip()})
    fsys = ("You are OneVoice in Fluent voice mode: a warm, natural spoken conversation. "
            "Answer in one to three short spoken sentences, no lists or markdown.")
    users = [i for i, t in enumerate(turns) if t["role"] == "user"]
    for i in users[-args.fluent_count:]:
        ctx = turns[max(0, i - 6): i + 1]
        rows.append({"kind": "fluent", "name": f"fluent_{len(rows)}", "max_tokens": 120,
                     "messages": [{"role": "system", "content": fsys}] + ctx,
                     "approx_tokens": _approx_tokens(fsys + "".join(t["content"] for t in ctx))})

    # memory agents: summarizer / observer style tasks over real transcripts
    tasks = [
        ("memory_summarizer", "Summarize this conversation for long-term memory: key facts, decisions, open tasks. "
                              "Return JSON with keys summary, facts, tasks."),
        ("memory_observer", "You are the Deep Observer. From this conversation list the user's emotional state, "
                            "preferences and people mentioned. Return JSON."),
        ("memory_profile", "Update the user profile from this conversation. Return only new or changed profile "
                           "facts as a JSON list."),
    ]
    for (name, instr), p in zip(tasks, sessions[1:4]):
        msgs = _session_messages(p)
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)[: 5000 * 4]
        rows.append({"kind": "memory", "name": name, "max_tokens": 400,
                     "messages": [{"role": "system", "content": instr},
                                  {"role": "user", "content": transcript}],
                     "approx_tokens": _approx_tokens(instr + transcript)})
    return rows


# ------------------------------------------------------------------ quality set
QUALITY = [
    ("What is 17 * 23? Answer with the number only.", r"\b391\b"),
    ("What is the capital of Australia? One word.", r"canberra"),
    ("Spell the word onevoice backwards, lowercase, no spaces.", r"eciovneno"),
    ("How many days are in a leap year? Number only.", r"\b366\b"),
    ("What is the chemical symbol for gold?", r"\bau\b"),
    ("A train leaves at 14:40 and the trip takes 2 hours 35 minutes. Arrival time as HH:MM?", r"17:15"),
    ("Translate 'thank you' into Hebrew, Hebrew script only.", r"תודה"),
    ("Which planet is called the Red Planet? One word.", r"mars"),
    ("What is the square root of 144? Number only.", r"\b12\b"),
    ("Sort ascending, comma-separated, nothing else: 9, 2, 7, 4", r"2\s*,\s*4\s*,\s*7\s*,\s*9"),
    ("What is the next prime number after 31? Number only.", r"\b37\b"),
    ("Output only a JSON object with name Alice and age 30.", r'"name"\s*:\s*"alice".*30|30.*"name"\s*:\s*"alice"'),
    ("Who wrote Pride and Prejudice? Surname only.", r"austen"),
    ("Convert 5 kilometres to metres. Number only.", r"\b5,?000\b"),
    ("In Python, what does len([1, 2, 3]) return? Number only.", r"\b3\b"),
    ("Ilan has 3 apples, buys 5 more and gives 2 away. How many now? Number only.", r"\b6\b"),
]
NEEDLE = "The tuning-loop passphrase is BLUE-HERON-42."


def needle_items(prompts: list[dict]) -> list[tuple[str, list[dict], str]]:
    text = "\n".join(m["content"] for r in prompts if r["kind"] == "chat" for m in r["messages"][1:])
    items = []
    for target in (4000, 16000, 30000):
        chunk = text[: int(target * 3.6)]
        mid = len(chunk) // 2
        doc = chunk[:mid] + "\n" + NEEDLE + "\n" + chunk[mid:]
        items.append((f"needle_{target // 1000}k",
                      [{"role": "user", "content": doc + "\n\nWhat is the tuning-loop passphrase? Reply with it only."}],
                      r"blue-heron-42"))
    return items


# ------------------------------------------------------------------ test server
class TestServer:
    def __init__(self, cfg: dict, port: int, logdir: Path):
        self.cfg, self.port, self.logdir = cfg, port, logdir
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: float = 420.0) -> bool:
        if not port_free(self.port):
            raise RuntimeError(f"test port {self.port} is already in use")
        cmd = server_cmd(self.cfg, self.port)
        self.logf = open(self.logdir / f"server_{self.cfg['label']}.log", "w", encoding="utf-8")
        self.logf.write(" ".join(shlex.quote(c) for c in cmd) + "\n")
        self.logf.flush()
        self.proc = subprocess.Popen(cmd, stdout=self.logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            code, _ = http_json(f"http://127.0.0.1:{self.port}/health", timeout=3)
            if code == 200:
                return True
            time.sleep(2)
        return False

    def stop(self) -> None:
        """Stops ONLY the process this object started."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        self.proc = None
        try:
            self.logf.close()
        except Exception:  # noqa: BLE001
            pass


def stream_chat(port: int, messages: list[dict], max_tokens: int, infer: dict, seed: int = 42) -> dict:
    body = {"messages": messages, "max_tokens": max_tokens, "stream": True, "seed": seed,
            "temperature": float(infer.get("temperature", 0.7)), "top_p": float(infer.get("top_p", 0.9)),
            "top_k": int(infer.get("top_k", 40)), "repeat_penalty": float(infer.get("repeat_penalty", 1.1)),
            "cache_prompt": True, "timings_per_token": False}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = None
    timings: dict = {}
    text = []
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                piece = d.get("content") or d.get("reasoning_content")
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    text.append(piece)
            if chunk.get("timings"):
                timings = chunk["timings"]
    total = time.perf_counter() - t0
    dn, da = timings.get("draft_n"), timings.get("draft_n_accepted")
    return {"ttft_s": ttft, "total_s": total, "prompt_n": timings.get("prompt_n"), "cache_n": timings.get("cache_n"),
            "prompt_tps": timings.get("prompt_per_second"), "gen_tps": timings.get("predicted_per_second"),
            "gen_n": timings.get("predicted_n"),
            "draft_accept": (da / dn) if dn else None, "chars": len("".join(text))}


def ask(port: int, messages: list[dict], max_tokens: int = 96) -> str:
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0, "top_k": 1, "seed": 42,
            "chat_template_kwargs": {"enable_thinking": False}}
    code, out = http_json(f"http://127.0.0.1:{port}/v1/chat/completions", body, timeout=900)
    if code != 200:
        return f"__HTTP_{code}__"
    msg = ((out.get("choices") or [{}])[0].get("message") or {})
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


# ------------------------------------------------------------------ one config
def run_config(cfg: dict, prompts: list[dict], args, rundir: Path, guard_stop: threading.Event) -> dict:
    res = {"label": cfg["label"], "axis": cfg.get("axis"), "value": cfg.get("value"),
           "settings": {k: cfg[k] for k in ("spec_draft_n_max", "ubatch", "batch", "cache_type", "cache_ram", "ctx", "slots")}}
    srv = TestServer(cfg, args.port, rundir)
    t0 = time.time()
    try:
        if not srv.start():
            res["error"] = "test server failed to start (see server log)"
            return res
        res["load_s"] = round(time.time() - t0, 1)
        stream_chat(args.port, [{"role": "user", "content": "Say hi."}], 8, cfg["inference"])  # warm-up
        rows = []
        for rep in range(args.repeats):
            for p in prompts:
                if guard_stop.is_set():
                    raise RuntimeError("OneVoice became busy - test server stopped")
                r = stream_chat(args.port, p["messages"], p.get("max_tokens", 300), cfg["inference"], seed=42 + rep)
                r.update({"name": p["name"], "kind": p["kind"], "rep": rep})
                rows.append(r)
        # prefix-cache scenario: chat -> memory agent -> same chat + 1 turn
        chats = [p for p in prompts if p["kind"] == "chat"]
        mems = [p for p in prompts if p["kind"] == "memory"]
        if chats and mems:
            a = chats[min(1, len(chats) - 1)]
            stream_chat(args.port, a["messages"], 64, cfg["inference"])
            stream_chat(args.port, mems[0]["messages"], 64, cfg["inference"])
            follow = a["messages"] + [{"role": "assistant", "content": "Okay."},
                                      {"role": "user", "content": "And one more thing - say that in one line."}]
            r = stream_chat(args.port, follow, 64, cfg["inference"])
            r.update({"name": "cache_followup", "kind": "cache", "rep": 0})
            rows.append(r)
        res["rows"] = rows
        # quality
        q = []
        for question, pattern in QUALITY:
            ans = ask(args.port, [{"role": "user", "content": question}])
            q.append({"q": question, "answer": ans, "correct": bool(re.search(pattern, ans.lower()))})
        for name, msgs, pattern in needle_items(prompts):
            ans = ask(args.port, msgs, 32)
            q.append({"q": name, "answer": ans, "correct": bool(re.search(pattern, ans.lower()))})
        res["quality"] = q
        res["correct"] = sum(1 for x in q if x["correct"])
        res["needles_ok"] = all(x["correct"] for x in q if x["q"].startswith("needle_"))
    except Exception as exc:  # noqa: BLE001
        res["error"] = str(exc)
    finally:
        srv.stop()
    return res


def summarize(res: dict) -> dict:
    rows = res.get("rows") or []

    def med(kind, key):
        vals = [r[key] for r in rows if (kind is None or r["kind"] == kind) and r.get(key) is not None]
        return round(statistics.median(vals), 3) if vals else None

    return {"ttft_chat": med("chat", "ttft_s"), "ttft_fluent": med("fluent", "ttft_s"),
            "ttft_memory": med("memory", "ttft_s"), "ttft_cache_followup": med("cache", "ttft_s"),
            "cache_n_followup": med("cache", "cache_n"), "prompt_tps": med(None, "prompt_tps"),
            "gen_tps": med(None, "gen_tps"), "draft_accept": med(None, "draft_accept")}


def score(s: dict, base: dict) -> float:
    """Lower is better. Relative to baseline: half TTFT (chat+fluent), half generation speed."""
    def ratio(a, b):
        return (a / b) if (a and b) else 1.0
    ttft = (ratio(s["ttft_chat"], base["ttft_chat"]) + ratio(s["ttft_fluent"], base["ttft_fluent"])) / 2
    gen = ratio(base["gen_tps"], s["gen_tps"])
    return round(0.5 * ttft + 0.5 * gen, 4)


def passes(res: dict, base_res: dict) -> bool:
    return (not res.get("error") and res.get("needles_ok")
            and res.get("correct", 0) >= base_res.get("correct", 0) - 1)


# ------------------------------------------------------------------ report
def write_report(rundir: Path, base_cfg: dict, results: list[dict], recommended: dict | None) -> None:
    base = next((r for r in results if r["label"] == "baseline"), None)
    lines = ["# tune_loop results", "", f"Run: {rundir.name}", "",
             "| config | load s | TTFT chat | TTFT fluent | TTFT memory | TTFT cache follow-up | prompt tok/s | gen tok/s | draft accept | quality | score | pass |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r.get("summary") or {}
        if r.get("error"):
            lines.append(f"| {r['label']} | - | - | - | - | - | - | - | - | - | - | ERROR: {r['error'][:60]} |")
            continue
        lines.append(f"| {r['label']} | {r.get('load_s')} | {s.get('ttft_chat')} | {s.get('ttft_fluent')} | "
                     f"{s.get('ttft_memory')} | {s.get('ttft_cache_followup')} | {s.get('prompt_tps')} | "
                     f"{s.get('gen_tps')} | {s.get('draft_accept')} | {r.get('correct')}/{len(r.get('quality') or [])} | "
                     f"{r.get('score')} | {'yes' if r.get('pass') else 'no'} |")
    lines += ["", "Score: lower is better; 1.0 = baseline. Pass = no error, all long-context needles found, "
              "and quality at most 1 below baseline.", ""]
    if recommended:
        lines += ["## Recommended", "", "```ini"] + recommended["ini"] + ["```", "",
                  f"From config `{recommended['label']}` (score {recommended['score']}). Apply it to the "
                  "DFlash Console profile only with Ilan's OK, then reload the engine when OneVoice is idle."]
    else:
        lines += ["## Recommended", "", "Keep the current settings: nothing beat the baseline and passed quality."]
    (rundir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (rundir / "results.json").write_text(json.dumps({"baseline": {k: v for k, v in base_cfg.items() if k != "inference"},
                                                     "results": results, "recommended": recommended}, indent=2,
                                                    ensure_ascii=False), encoding="utf-8")


def ini_for(cfg: dict) -> list[str]:
    out = [f"b = {cfg['batch']}", f"ub = {cfg['ubatch']}", f"cache-type-k = {cfg['cache_type']}",
           f"cache-type-v = {cfg['cache_type']}", f"spec-draft-n-max = {cfg['spec_draft_n_max']}"]
    if cfg.get("cache_ram") not in (None, "default"):
        out.append(f"cache-ram = {cfg['cache_ram']}")
    return out


# ------------------------------------------------------------------ main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Idle-only tuning loop for the OneVoice 31B llama-server (test instance).")
    ap.add_argument("--server-id", default=DEFAULT_SERVER_ID, help="DFlash Console profile to copy settings from")
    ap.add_argument("--port", type=int, default=DEFAULT_TEST_PORT, help="port for the TEST llama-server")
    ap.add_argument("--ctx", type=int, default=65536, help="test context (one slot)")
    ap.add_argument("--slots", type=int, default=1)
    ap.add_argument("--main-gpu", type=int, default=0, help="CUDA device for the test server")
    ap.add_argument("--gpu-index", type=int, default=0, help="nvidia-smi index used for VRAM/util checks")
    ap.add_argument("--expect-gpu", default="4090", help="refuse if the checked GPU name does not contain this")
    ap.add_argument("--axes", default=",".join(SWEEP_AXES), help="comma list of axes to sweep")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--no-mmproj", action="store_true", help="test without the vision projector (saves ~1 GB)")
    ap.add_argument("--no-combined", action="store_true", help="skip the final run combining per-axis winners")
    ap.add_argument("--prompts-file", help="JSONL replay set (kind,name,messages,max_tokens) instead of capture")
    ap.add_argument("--system-prompt-file", help="exact OneVoice system prompt text to use for chat prompts")
    ap.add_argument("--fluent-count", type=int, default=4)
    ap.add_argument("--onevoice-url", default="http://127.0.0.1:8890")
    ap.add_argument("--min-quiet-minutes", type=float, default=10.0)
    ap.add_argument("--max-gpu-util", type=float, default=20.0)
    ap.add_argument("--allow-onevoice-down", action="store_true")
    ap.add_argument("--ignore-nightly-window", action="store_true")
    ap.add_argument("--out", default=str(CONSOLE_ROOT / "logs" / "tune_loop"))
    ap.add_argument("--dry-run", action="store_true", help="parse, capture, check idle + VRAM, print the plan; launch nothing")
    ap.add_argument("--capture-only", action="store_true", help="only write the replay set")
    return ap.parse_args(argv)


def plan_configs(base: dict, axes: list[str]) -> list[dict]:
    plan = [dict(base, label="baseline", axis=None, value=None)]
    for axis in axes:
        for v in SWEEP_AXES[axis]:
            if base.get(axis) == v or (axis == "cache_ram" and v == "default"):
                continue
            plan.append(dict(base, **{axis: v}, label=f"{axis}={v}", axis=axis, value=v))
    return plan


def main(argv=None) -> int:
    args = parse_args(argv)
    axes = [a.strip() for a in args.axes.split(",") if a.strip()]
    bad = [a for a in axes if a not in SWEEP_AXES]
    if bad:
        log(f"unknown axes: {bad}; valid: {list(SWEEP_AXES)}")
        return 2
    rundir = Path(args.out) / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rundir.mkdir(parents=True, exist_ok=True)
    base = build_baseline(args.server_id, args)
    if args.port in base["live_ports"] or args.port == base["live_port"]:
        log(f"REFUSING: port {args.port} belongs to a live Console engine {base['live_ports']}")
        return 2
    for key in ("binary", "model"):
        if not base.get(key) or not Path(base[key]).is_file():
            log(f"REFUSING: missing {key}: {base.get(key)}")
            return 2
    log(f"baseline from Console profile '{args.server_id}': n_max={base['spec_draft_n_max']} b={base['batch']} "
        f"ub={base['ubatch']} kv={base['cache_type']} ctx={base['ctx']} slots={base['slots']} mmproj={'yes' if base['mmproj'] else 'no'}")

    prompts = capture_prompts(args)
    with open(rundir / "prompts.jsonl", "w", encoding="utf-8") as f:
        for p in prompts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    for p in prompts:
        log(f"  prompt {p['name']:<20} kind={p['kind']:<7} ~{p.get('approx_tokens')} tokens, max_tokens={p.get('max_tokens')}")
    log(f"captured {len(prompts)} replay prompts -> {rundir / 'prompts.jsonl'}")
    if args.capture_only:
        return 0

    plan = plan_configs(base, axes)
    log(f"plan: {len(plan)} configs + {'0' if args.no_combined else '1'} combined: " + ", ".join(c["label"] for c in plan))
    biggest = max(plan, key=lambda c: estimate_vram(c)["total_gib"])
    vr = vram_report(biggest, args)
    est = vr["estimate"]
    log(f"VRAM need (largest config '{biggest['label']}'): {vr['need_mib']} MiB = weights {est['weights_gib']:.1f} + "
        f"KV {est['kv_main_gib']:.2f} + draft KV {est['kv_draft_gib']:.2f} + compute {est['compute_gib']:.1f} + "
        f"headroom {est['headroom_gib']:.1f} GiB")
    g = vr["gpu"]
    log(f"GPU {g.get('index')} {g.get('name')}: free {g.get('free_mib')} / {g.get('total_mib')} MiB -> "
        f"{'OK' if vr['ok'] else 'NOT ENOUGH'}" + (f" ({'; '.join(est['notes'])})" if est["notes"] else ""))
    idle = idle_report(args)
    for c in idle["checks"]:
        log(f"  idle check {'OK  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
    log(f"port {args.port}: {'free' if port_free(args.port) else 'IN USE'}")
    (rundir / "preflight.json").write_text(json.dumps({"vram": vr, "idle": idle, "plan": [c["label"] for c in plan],
                                                       "server_cmd_baseline": server_cmd(plan[0], args.port)},
                                                      indent=2), encoding="utf-8")
    if args.dry_run:
        log("DRY RUN: nothing launched. Baseline test command:")
        log("  " + " ".join(server_cmd(plan[0], args.port)))
        log(f"would {'RUN' if (idle['idle'] and vr['ok'] and port_free(args.port)) else 'REFUSE'} a real sweep now")
        return 0
    if not vr["ok"]:
        log("REFUSING: not enough free VRAM. Free GPU memory first (this script never unloads models).")
        return 3
    if not idle["idle"]:
        log("REFUSING: OneVoice is not idle.")
        return 3
    if not port_free(args.port):
        log(f"REFUSING: port {args.port} in use")
        return 3

    stop = threading.Event()
    done = threading.Event()

    def watchdog():
        while not done.wait(15):
            rep = idle_report(argparse.Namespace(**{**vars(args), "max_gpu_util": 101}))  # own load uses the GPU
            if not rep["idle"]:
                log("watchdog: OneVoice busy -> stopping the test server")
                stop.set()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    results: list[dict] = []
    base_res = None
    try:
        for cfg in plan:
            if stop.is_set():
                break
            log(f"=== {cfg['label']} ===")
            r = run_config(cfg, prompts, args, rundir, stop)
            r["summary"] = summarize(r)
            if cfg["label"] == "baseline":
                base_res = r
                if r.get("error"):
                    log(f"baseline failed: {r['error']}")
                    results.append(r)
                    break
            r["score"] = score(r["summary"], base_res["summary"]) if base_res and not r.get("error") else None
            r["pass"] = bool(base_res and passes(r, base_res))
            results.append(r)
            write_report(rundir, base, results, None)
            log(f"    {r['summary']} quality={r.get('correct')} score={r['score']} pass={r['pass']} {r.get('error') or ''}")
        recommended = None
        if base_res and not base_res.get("error") and not stop.is_set():
            winners = {}
            for r in results:
                if r.get("axis") and r.get("pass") and r.get("score") is not None and r["score"] < 0.98:
                    if r["axis"] not in winners or r["score"] < winners[r["axis"]]["score"]:
                        winners[r["axis"]] = r
            best_cfg = dict(base)
            for axis, r in winners.items():
                best_cfg[axis] = r["value"]
            candidates = [r for r in results if r.get("pass") and r.get("score") is not None]
            if len(winners) > 1 and not args.no_combined:
                comb = dict(best_cfg, label="combined:" + "+".join(f"{a}={w['value']}" for a, w in winners.items()),
                            axis="combined", value=None)
                if vram_report(comb, args)["ok"]:
                    log(f"=== {comb['label']} ===")
                    r = run_config(comb, prompts, args, rundir, stop)
                    r["summary"] = summarize(r)
                    r["score"] = None if r.get("error") else score(r["summary"], base_res["summary"])
                    r["pass"] = passes(r, base_res)
                    r["settings_cfg"] = {a: comb[a] for a in SWEEP_AXES}
                    results.append(r)
                    if r["pass"]:
                        candidates.append(r)
            best = min(candidates, key=lambda r: r["score"], default=None)
            if best and best["label"] != "baseline" and best["score"] < 0.98:
                cfg = dict(base)
                if best.get("axis") == "combined":
                    cfg.update(best["settings_cfg"])
                elif best.get("axis"):
                    cfg[best["axis"]] = best["value"]
                recommended = {"label": best["label"], "score": best["score"], "ini": ini_for(cfg)}
        write_report(rundir, base, results, recommended)
        log(f"report: {rundir / 'results.md'}")
    finally:
        done.set()
    return 0 if not stop.is_set() else 4


if __name__ == "__main__":
    sys.exit(main())
