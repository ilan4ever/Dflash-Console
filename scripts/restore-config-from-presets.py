"""One-off helper: rebuild config.json server profiles from logs/presets/*.ini."""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.config import normalize_server, save_config, validate_config

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'stack.ini', 'qwen-stack.ini', 'qwen-dflash.ini', 'nomic-embed.ini'}


def main() -> None:
    cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    cfg['dflash_root'] = str(ROOT)
    cfg['models_root'] = str(ROOT / 'models')
    servers = []
    for ini in sorted((ROOT / 'logs' / 'presets').glob('*.ini')):
        if ini.name in SKIP:
            continue
        text = ini.read_text(encoding='utf-8', errors='replace')
        sid = ini.stem
        model_m = re.search(r'^model\s*=\s*(.+)$', text, re.MULTILINE)
        if not model_m:
            continue
        target = Path(model_m.group(1).strip().strip('"'))
        if not target.is_file():
            continue
        draft_m = re.search(r'^draft\s*=\s*(.+)$', text, re.MULTILINE)
        draft = Path(draft_m.group(1).strip().strip('"')) if draft_m else None
        entry = {
            'id': sid,
            'label': sid.replace('-', ' '),
            'profile': sid,
            'port': 8090 + len(servers),
            'host': '127.0.0.1',
            'model_id': sid,
            'target_path': str(target),
            'enabled': True,
            'engine_on': False,
            'context_size': 32768,
        }
        if draft and draft.is_file():
            entry['draft_path'] = str(draft)
        servers.append(normalize_server(entry))
    cfg['servers'] = servers
    validate_config(cfg)
    save_config(cfg)
    print(f'restored {len(servers)} servers')


if __name__ == '__main__':
    main()
