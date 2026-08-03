# Contributing to DFlash Console

Thanks for helping improve DFlash Console.

## Before you start

1. Check existing issues and pull requests for related work.
2. Keep changes focused and explain the user-facing reason.
3. Do not commit `config.json`, model weights, logs, credentials, or build
   outputs. Use `config.example.json` for configuration examples.

## Development setup

The supported development environment is Windows with Python 3.10+,
PowerShell 7+, and Node.js 22.12+ for the Electron shell.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q
python scripts/release-preflight.py
node --check electron/main.js
npm audit --audit-level=high
```

The Console is local-only by design. Keep test engines bound to loopback and
never use real access tokens in fixtures.

## Pull requests

- Include tests for behavior changes.
- Update documentation when setup or user-visible behavior changes.
- Keep generated artifacts out of commits.
- Report known limitations and validation results in the pull request.

By participating, you agree to follow the project's [Code of
Conduct](./CODE_OF_CONDUCT.md).
