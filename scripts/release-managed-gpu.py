"""Unload and stop all DFlash Console managed llama-server engines."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine_state import release_and_stop_all_managed_engines  # noqa: E402


def main() -> int:
    results = release_and_stop_all_managed_engines()
    print(json.dumps({'released': results}, indent=2))
    failed = any(
        not (row.get('released') or {}).get('success', True)
        or not (row.get('stopped') or {}).get('success', True)
        for row in results
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
