#!/usr/bin/env bash
# Linux companion to run.ps1 -NoElectron (server only, no desktop shell).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/server.sh" --restart --foreground "$@"
