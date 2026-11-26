"""set-install-permissions.ps1 -Only: walk the copied runtime items, not .venv or runtimes.

Walking the whole data root (tens of thousands of Python files) hit the app's
2-minute timeout on every first start of a new version (end-to-end test).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'set-install-permissions.ps1'
WINDOWS_POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


def _explicit_entries(path: Path) -> list[str]:
    out = subprocess.run(['icacls.exe', str(path)], capture_output=True, text=True, check=True).stdout
    # Inherited entries carry "(I)"; the script's /grant adds explicit ones.
    return [line for line in out.splitlines()[:-2] if ':' in line and '(I)' not in line]


@unittest.skipUnless(os.name == 'nt' and WINDOWS_POWERSHELL.is_file(), 'Windows only')
class InstallPermissionsOnlyTests(unittest.TestCase):
    def test_only_walks_the_listed_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'data'
            for name in ('api', '.venv'):
                (root / name).mkdir(parents=True)
                (root / name / 'file.py').write_text('x\n', encoding='utf-8')
            subprocess.run(
                [str(WINDOWS_POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                 '-Path', str(root), '-Kind', 'data', '-Recurse', '-Only', 'api,core'],
                check=True, capture_output=True, timeout=120,
            )
            # icacls merges a grant on a file into its inherited entry, so the
            # explicit entry shows on the walked folder itself.
            self.assertTrue(_explicit_entries(root / 'api'), 'api was not walked')
            self.assertFalse(_explicit_entries(root / '.venv'), '.venv must be left alone')

    def test_app_and_setup_pass_only(self):
        bootstrap = (ROOT / 'electron' / 'bootstrap.js').read_text(encoding='utf-8')
        self.assertIn("'-Only', RUNTIME_ITEMS.join(',')", bootstrap)
        self.assertIn('.install-access-full', bootstrap)
        setup_bootstrap = (ROOT / 'scripts' / 'bootstrap-installed-data-root.ps1').read_text(encoding='utf-8')
        self.assertIn('-Recurse -Only', setup_bootstrap)


if __name__ == '__main__':
    unittest.main()
