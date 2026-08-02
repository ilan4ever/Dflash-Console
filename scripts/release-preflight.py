"""Validate that a checkout contains the files required to run the Console."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for key in ('src', 'href'):
            value = str(values.get(key) or '').strip()
            if value:
                self.references.append(value)
        element_id = str(values.get('id') or '').strip()
        if element_id:
            self.ids.append(element_id)


def local_path(root: Path, reference: str) -> Path | None:
    parsed = urlparse(reference)
    if parsed.scheme or parsed.netloc:
        return None
    clean = parsed.path.split('#', 1)[0].split('?', 1)[0]
    if not clean:
        return None
    if clean in {'/docs', '/redoc', '/openapi.json'}:
        return None
    if clean.startswith('/'):
        return root / clean.lstrip('/')
    return root / 'static' / clean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.root.resolve()
    required = (
        'run.ps1',
        'server.ps1',
        'requirements.txt',
        'requirements.lock',
        'config.example.json',
        'api/app.py',
        'scripts/start-console-server.ps1',
        'scripts/restart-console-server.ps1',
        'scripts/release-managed-gpu.py',
        'scripts/release-preflight.py',
        'static/index.html',
    )
    missing = [path for path in required if not (root / path).is_file()]
    parser = AssetParser()
    index = root / 'static' / 'index.html'
    if index.is_file():
        parser.feed(index.read_text(encoding='utf-8', errors='replace'))
    for reference in parser.references:
        target = local_path(root, reference)
        if target is not None and not target.is_file():
            missing.append(str(target.relative_to(root)))

    duplicate_ids = sorted({key for key in parser.ids if parser.ids.count(key) > 1})
    css_refs: list[str] = []
    for css_path in (root / 'static').rglob('*.css'):
        text = css_path.read_text(encoding='utf-8', errors='replace')
        css_refs.extend(re.findall(r'''url\(\s*['"]?([^'")]+)''', text))
    for reference in css_refs:
        target = local_path(root, reference)
        if target is not None and not target.is_file():
            missing.append(str(target.relative_to(root)))

    missing = sorted(set(missing))
    if missing or duplicate_ids:
        if missing:
            print('Missing release files:')
            print('\n'.join(f'  - {path}' for path in missing))
        if duplicate_ids:
            print('Duplicate HTML ids:')
            print('\n'.join(f'  - {value}' for value in duplicate_ids))
        return 1
    print(f'Release preflight passed for {root}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
