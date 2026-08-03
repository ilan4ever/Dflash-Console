#!/usr/bin/env python3
"""Bump DFlash Console version across package/UI/backend sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bump(part: str, set_version: str | None = None) -> str:
    pkg_path = ROOT / 'package.json'
    pkg = json.loads(pkg_path.read_text(encoding='utf-8'))
    current = str(pkg.get('version') or '0.0.0')
    if set_version:
        if not re.fullmatch(r'\d+\.\d+\.\d+', set_version):
            raise SystemExit(f'Unsupported version {set_version!r}')
        next_version = set_version
    else:
        major, minor, patch = map(int, current.split('.'))
        if part == 'major':
            major, minor, patch = major + 1, 0, 0
        elif part == 'minor':
            minor, patch = minor + 1, 0
        else:
            patch += 1
        next_version = f'{major}.{minor}.{patch}'

    print(f'{current} -> {next_version}')
    pkg['version'] = next_version
    pkg_path.write_text(json.dumps(pkg, indent=2) + '\n', encoding='utf-8')

    lock_path = ROOT / 'package-lock.json'
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        lock['version'] = next_version
        if '' in lock.get('packages', {}):
            lock['packages']['']['version'] = next_version
        lock_path.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')

    (ROOT / 'core' / 'version.py').write_text(
        '"""DFlash Console release label."""\n\n'
        f'APP_VERSION = "{next_version}"\n',
        encoding='utf-8',
    )

    html_path = ROOT / 'static' / 'index.html'
    html = html_path.read_text(encoding='utf-8')
    html = re.sub(
        r'(id="dfAppVersion"[^>]*>)v\d+\.\d+\.\d+',
        rf'\g<1>v{next_version}',
        html,
    )
    html_path.write_text(html, encoding='utf-8')

    readme = ROOT / 'README.md'
    if readme.is_file():
        text = readme.read_text(encoding='utf-8')
        text = re.sub(r'\*\*Version:\*\* v\d+\.\d+\.\d+', f'**Version:** v{next_version}', text)
        text = re.sub(
            r'## Recent improvements \(v\d+\.\d+\.\d+\)',
            f'## Recent improvements (v{next_version})',
            text,
            count=1,
        )
        readme.write_text(text, encoding='utf-8')

    print(next_version)
    return next_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Bump DFlash Console version')
    parser.add_argument('--part', choices=('patch', 'minor', 'major'), default='patch')
    parser.add_argument('--set', dest='set_version', default='')
    args = parser.parse_args(argv)
    bump(args.part, args.set_version or None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
