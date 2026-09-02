# DFlash Console — public preview testers wanted

We're opening DFlash Console for early testing on real Windows machines.

## What it is

A local control panel for **DFlash 1 and DFlash 2** speculative-decoding stacks
and a unified model runtime — **DFlash / llama-server**, **vLLM**,
**Transformers**, **FreeToken**, models, Hugging Face downloads, Piper TTS,
Whisper STT, embeddings, and a `dflash` terminal CLI.

## Install (Windows)

1. **Recommended:** download the latest **`DFlash-Console-Setup-*-x64.exe`** from [GitHub Releases](https://github.com/ilan4ever/Dflash-Console/releases/latest).
2. **Terminal CLI:** `pip install dflash-console` then `dflash serve` ([PyPI](https://pypi.org/project/dflash-console/)).
3. Requires **Windows 10+**, **Python 3.10+**, and **PowerShell 7+** for the Console data root.
4. Open **http://127.0.0.1:8900/** after install (or use the desktop app).

Only one Console API should run on port 8900. The installer and `dflash serve` stop a foreign instance before starting.

## Please report

- **Bugs** → [Bug report](https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml) (include Console version from **About**, GPU, and steps)
- **Questions** → [Discussions → Q&A](https://github.com/ilan4ever/Dflash-Console/discussions/categories/q-a)
- **Ideas** → [Feature request](https://github.com/ilan4ever/Dflash-Console/issues/new?template=feature_request.yml)

This is a **preview** — expect rough edges. Your reports directly shape what we fix before a wider launch.

Thank you for trying it.
