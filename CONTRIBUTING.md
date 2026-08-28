# Contributing to DFlash Console

Thanks for helping improve DFlash Console.

## License and contribution terms

DFlash Console is free software under the
[GNU Affero General Public License version 3 or later](./LICENSE).
Contributions accepted into this repository are intended to be distributed
under the same license. By submitting a contribution, you confirm that you
have the right to submit it and that it does not knowingly include
third-party code or assets whose terms conflict with the project license.

You retain copyright in your contribution. The project may copy, modify, and
redistribute accepted contributions as part of DFlash Console under the AGPL
and any later version permitted by the project notices. We do not require a
copyright assignment. If a future contribution requires separate commercial
licensing, the maintainer will ask for a separate written agreement rather
than assuming that right from a pull request.

The DFlash name and logo are governed by
[TRADEMARKS.md](./TRADEMARKS.md), not by the AGPL.

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
pip install -e .[dev]
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
