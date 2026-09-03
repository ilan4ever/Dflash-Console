# DFlash Console is public — testers wanted (v0.3.122)

DFlash Console is now an **open-source public preview**. The Windows installer,
automatic updates, and source repo are all on GitHub.

## What it is

A local Windows control panel for **DFlash 1 and DFlash 2** speculative-decoding
stacks and a unified model runtime — **DFlash / llama-server**, **vLLM**,
**Transformers**, **FreeToken**, models, Hugging Face downloads, Piper TTS,
Whisper STT, embeddings, and a `dflash` terminal CLI.

## Install

1. **Recommended:** download **`DFlash-Console-Setup-0.3.122-x64.exe`** from the [latest GitHub Release](https://github.com/ilan4ever/Dflash-Console/releases/latest).
2. **Terminal CLI:** `pip install dflash-console` then `dflash serve` ([PyPI](https://pypi.org/project/dflash-console/)).
3. Requires **Windows 10+**, **Python 3.10+**, and **PowerShell 7+** for the Console data root.
4. Open **http://127.0.0.1:8900/** after install (or use the desktop app).

Installed desktop apps update from the same GitHub Release (`latest.json` + the setup EXE). Windows may show an Unknown publisher warning because the installer is unsigned.

Only one Console API should run on port 8900.

## Please report

- **Bugs** → [Bug report](https://github.com/ilan4ever/Dflash-Console/issues/new?template=bug_report.yml) (include Console version from **About**, GPU, and steps)
- **Questions** → [Discussions → Q&A](https://github.com/ilan4ever/Dflash-Console/discussions/categories/q-a)
- **Ideas** → [Feature request](https://github.com/ilan4ever/Dflash-Console/issues/new?template=feature_request.yml)
- **Security** → use GitHub private vulnerability reporting (do not open a public issue)

This is a **preview** — expect rough edges. Your reports directly shape what we fix before a wider launch.

Thank you for trying it.
