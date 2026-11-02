"""Installer folders must not stay restricted after setup."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'set-install-permissions.ps1'


def _run(args):
    completed = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout or f'exit {completed.returncode}')
    return completed.stdout


def _icacls(path: Path) -> str:
    completed = subprocess.run(
        ['icacls.exe', str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


class InstallPermissionsTests(unittest.TestCase):
    def test_data_folder_is_normal_and_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'DFlash Console'
            nested = root / 'logs' / 'presets'
            nested.mkdir(parents=True)
            preset = nested / 'gemma.ini'
            preset.write_text('model = gemma\n', encoding='utf-8')
            models = root / 'models'
            models.mkdir()
            weight = models / 'model.gguf'
            weight.write_text('weight', encoding='utf-8')
            _run([
                '-Command',
                f"icacls '{root}' /setintegritylevel '(OI)(CI)L' /T /C /Q",
            ])
            self.assertIn('Low Mandatory Level', _icacls(preset))

            _run([
                '-File', str(SCRIPT),
                '-Path', str(root),
                '-Kind', 'data',
                '-Recurse',
            ])

            preset_acl = _icacls(preset)
            self.assertNotIn('Low Mandatory Level', preset_acl)
            self.assertIn('Medium Mandatory Level', preset_acl)
            preset.write_text('model = gemma\nready = true\n', encoding='utf-8')
            self.assertNotIn('Low Mandatory Level', _icacls(weight))
            self.assertIn('Medium Mandatory Level', _icacls(models))

    def test_shared_program_folder_can_be_run_by_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'DFlash Console'
            root.mkdir()
            (root / 'DFlash Console.exe').write_bytes(b'MZ')
            _run([
                '-File', str(SCRIPT),
                '-Path', str(root),
                '-Kind', 'program',
                '-Shared',
                '-Recurse',
            ])
            listing = _icacls(root)
            self.assertNotIn('Low Mandatory Level', listing)
            self.assertIn('Medium Mandatory Level', listing)
            self.assertTrue(
                'BUILTIN\\Users:(OI)(CI)(RX)' in listing or 'S-1-5-32-545:(OI)(CI)(RX)' in listing,
                listing,
            )


if __name__ == '__main__':
    unittest.main()
