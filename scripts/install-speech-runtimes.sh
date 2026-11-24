#!/usr/bin/env bash
# Linux: install the speech runtimes the Windows installer ships ready-made.
#   stt             whisper.cpp whisper-server (built from source, CUDA when nvcc is present)
#   piper           Piper TTS (official Linux release) + en_US-lessac-medium voice
#   faster-whisper  faster-whisper worker venv (CTranslate2, CUDA 12 libs from pip)
#   vibevoice       Microsoft VibeVoice realtime TTS worker venv + voice presets
# Usage: scripts/install-speech-runtimes.sh [all|stt|piper|faster-whisper|vibevoice]...
# Progress lines use "DFLASH_PROGRESS <pct> <message>".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/runtimes"
SRC_DIR="${DFLASH_SRC_DIR:-$HOME/dev}"           # where whisper.cpp is cloned/built
WHISPER_CPP_REF="${WHISPER_CPP_REF:-}"            # empty = latest release tag
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
PIPER_VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
VIBEVOICE_REPO="https://github.com/microsoft/VibeVoice.git"
VIBEVOICE_COMMIT="94da20d"                        # same commit as the Windows bundle

# The CUDA toolkit is often only on PATH in interactive shells.
for d in /usr/local/cuda/bin /opt/cuda/bin; do
  [[ -x "$d/nvcc" && ":$PATH:" != *":$d:"* ]] && PATH="$d:$PATH"
done
export PATH

[[ $# -eq 0 ]] && set -- all
want() { local c; for c in "${TARGETS[@]}"; do [[ "$c" == all || "$c" == "$1" ]] && return 0; done; return 1; }
TARGETS=("$@")

have_cuda() { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }
pick_python() {
  local c
  for c in python3.12 python3.11 python3.10 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1 \
      && "$c" -c 'import sys; v=sys.version_info; sys.exit(0 if (3,10)<=(v.major,v.minor)<=(3,13) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  echo "Python 3.10-3.13 is required (sudo apt install python3 python3-venv)." >&2
  return 1
}
make_venv() {  # make_venv <dir>
  local venv="$1" py
  if [[ -x "$venv/bin/python" ]] && "$venv/bin/python" -m pip --version >/dev/null 2>&1; then return 0; fi
  py="$(pick_python)"
  rm -rf "$venv"
  "$py" -m venv "$venv"
  "$venv/bin/python" -m pip install --upgrade pip wheel setuptools
}

# ------------------------------------------------------------------ whisper-server
if want stt; then
  echo "DFLASH_PROGRESS 5 whisper.cpp: getting source"
  command -v cmake >/dev/null && command -v g++ >/dev/null || {
    echo "cmake and a C++ compiler are needed: sudo apt install cmake build-essential" >&2; exit 2; }
  mkdir -p "$SRC_DIR"
  if [[ ! -d "$SRC_DIR/whisper.cpp/.git" ]]; then
    git clone --quiet https://github.com/ggml-org/whisper.cpp.git "$SRC_DIR/whisper.cpp"
  fi
  git -C "$SRC_DIR/whisper.cpp" fetch --quiet --tags
  ref="$WHISPER_CPP_REF"
  [[ -n "$ref" ]] || ref="$(git -C "$SRC_DIR/whisper.cpp" tag --sort=-v:refname | grep -E '^v[0-9]' | head -1)"
  git -C "$SRC_DIR/whisper.cpp" checkout --quiet "$ref"
  cuda_flags=(-DGGML_CUDA=OFF)
  if have_cuda && command -v nvcc >/dev/null 2>&1; then
    arch="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')"
    cuda_flags=(-DGGML_CUDA=ON "-DCMAKE_CUDA_ARCHITECTURES=${arch:-native}")
  fi
  echo "DFLASH_PROGRESS 10 whisper.cpp $ref: configuring (${cuda_flags[*]})"
  cmake -S "$SRC_DIR/whisper.cpp" -B "$SRC_DIR/whisper.cpp/build" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_TESTS=OFF "${cuda_flags[@]}" >/dev/null
  echo "DFLASH_PROGRESS 15 whisper.cpp: building whisper-server (long with CUDA)"
  nice -n 15 cmake --build "$SRC_DIR/whisper.cpp/build" --config Release --target whisper-server -j"$(nproc)"
  mkdir -p "$RT/stt"
  install -m 755 "$SRC_DIR/whisper.cpp/build/bin/whisper-server" "$RT/stt/whisper-server"
  [[ -f "$RT/stt/sample_en.wav" ]] || cp -f "$SRC_DIR/whisper.cpp/samples/jfk.wav" "$RT/stt/sample_en.wav" 2>/dev/null || true
  echo "whisper-server installed: $RT/stt/whisper-server"
fi

# ------------------------------------------------------------------ piper
if want piper; then
  echo "DFLASH_PROGRESS 40 Piper: downloading release"
  tmp="$(mktemp -d)"
  curl -fsSL "$PIPER_URL" -o "$tmp/piper.tar.gz"
  tar -xzf "$tmp/piper.tar.gz" -C "$tmp"
  mkdir -p "$RT/piper/voices"
  cp -a "$tmp/piper/." "$RT/piper/"
  rm -rf "$tmp"
  chmod 755 "$RT/piper/piper"
  for f in en_US-lessac-medium.onnx en_US-lessac-medium.onnx.json; do
    [[ -s "$RT/piper/voices/$f" ]] || curl -fsSL "$PIPER_VOICE_BASE/$f" -o "$RT/piper/voices/$f"
  done
  echo "Piper installed: $RT/piper/piper"
fi

# ------------------------------------------------------------------ faster-whisper
if want faster-whisper; then
  echo "DFLASH_PROGRESS 55 faster-whisper: creating environment"
  mkdir -p "$RT/faster-whisper"
  cp -f "$ROOT/runtime-bundles/faster-whisper/server.py" "$RT/faster-whisper/server.py"
  make_venv "$RT/faster-whisper/venv"
  "$RT/faster-whisper/venv/bin/python" -m pip install --upgrade -r "$ROOT/runtime-bundles/faster-whisper/requirements.txt"
  if have_cuda; then
    # CTranslate2 4.x needs CUDA 12 cuBLAS + cuDNN 9; the adapter puts these on LD_LIBRARY_PATH.
    "$RT/faster-whisper/venv/bin/python" -m pip install --upgrade "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"
  fi
  "$RT/faster-whisper/venv/bin/python" -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
fi

# ------------------------------------------------------------------ vibevoice
if want vibevoice; then
  echo "DFLASH_PROGRESS 70 VibeVoice: getting Microsoft's repo"
  mkdir -p "$RT/vibevoice/voices"
  if [[ ! -d "$RT/vibevoice/repo/.git" ]]; then
    git clone --quiet "$VIBEVOICE_REPO" "$RT/vibevoice/repo"
  fi
  git -C "$RT/vibevoice/repo" fetch --quiet origin
  git -C "$RT/vibevoice/repo" checkout --quiet "$VIBEVOICE_COMMIT"
  cp -f "$RT/vibevoice/repo/demo/voices/streaming_model/"*.pt "$RT/vibevoice/voices/"
  cp -f "$ROOT/runtime-bundles/vibevoice/server.py" "$RT/vibevoice/server.py"
  echo "DFLASH_PROGRESS 78 VibeVoice: creating environment"
  make_venv "$RT/vibevoice/venv"
  if have_cuda; then
    "$RT/vibevoice/venv/bin/python" -m pip install --upgrade torch
  else
    "$RT/vibevoice/venv/bin/python" -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cpu
  fi
  "$RT/vibevoice/venv/bin/python" -m pip install --upgrade -r "$ROOT/runtime-bundles/vibevoice/requirements.txt"
  (cd "$RT/vibevoice" && venv/bin/python -c "import sys; sys.path.insert(0, 'repo'); import vibevoice; print('vibevoice ok')")
fi

echo "DFLASH_PROGRESS 100 Speech runtimes installed"
