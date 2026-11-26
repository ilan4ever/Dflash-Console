"""config.json with a UTF-8 byte-order mark (fresh installs on PCs without PowerShell 7)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import config as cfg

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


class ConfigBomTests(unittest.TestCase):
    def test_load_config_accepts_byte_order_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_bytes(b'\xef\xbb\xbf' + json.dumps({'ui_port': 8900, 'servers': []}).encode('utf-8'))
            original = cfg.CONFIG_PATH
            cfg.CONFIG_PATH = path
            try:
                data = cfg.load_config()
            finally:
                cfg.CONFIG_PATH = original
        self.assertEqual(data['ui_port'], 8900)

    @unittest.skipUnless(os.name == 'nt' and WINDOWS_POWERSHELL.is_file(), 'needs Windows PowerShell 5.1')
    def test_bootstrap_writes_config_without_byte_order_mark_in_windows_powershell(self):
        # Setup falls back to Windows PowerShell 5.1 when PowerShell 7 is missing;
        # its Set-Content -Encoding UTF8 wrote a BOM that broke the first start.
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / 'program'
            runtime = program / 'resources' / 'console-runtime'
            runtime.mkdir(parents=True)
            (runtime / 'server.ps1').write_text('# test\n', encoding='utf-8')
            shutil.copy(ROOT / 'config.example.json', runtime / 'config.example.json')
            scripts = Path(tmp) / 'scripts'
            scripts.mkdir()
            shutil.copy(ROOT / 'scripts' / 'bootstrap-installed-data-root.ps1', scripts)
            # The real permission script changes ACLs; a stub keeps the test local.
            (scripts / 'set-install-permissions.ps1').write_text('param($Path, $Kind, [switch]$Recurse)\n', encoding='utf-8')
            data_root = Path(tmp) / 'data'
            subprocess.run(
                [str(WINDOWS_POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(scripts / 'bootstrap-installed-data-root.ps1'),
                 '-ProgramRoot', str(program), '-DataRoot', str(data_root)],
                check=True, capture_output=True, timeout=120,
            )
            raw = (data_root / 'config.json').read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'), 'config.json must not start with a BOM')
        self.assertEqual(json.loads(raw.decode('utf-8'))['dflash_root'], str(data_root))


if __name__ == '__main__':
    unittest.main()
