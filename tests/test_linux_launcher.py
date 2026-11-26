"""Linux launcher: the menu, desktop and taskbar show the DFlash icon."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LinuxLauncherTests(unittest.TestCase):
    def test_icons_exist(self):
        for size in (256, 64):
            icon = ROOT / 'static' / 'icons' / f'dflash-console-{size}.png'
            self.assertTrue(icon.is_file(), icon)
            self.assertEqual(icon.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    def test_launcher_uses_the_installed_icon_and_app_window(self):
        script = (ROOT / 'install-linux.sh').read_text(encoding='utf-8')
        self.assertIn('Icon=dflash-console', script)
        self.assertNotIn('utilities-terminal', script)
        self.assertIn('hicolor', script)
        self.assertIn('StartupWMClass=127.0.0.1', script)
        self.assertIn('scripts/open-linux-ui.sh', script)

    def test_open_script_starts_once_and_opens_an_app_window(self):
        script = (ROOT / 'scripts' / 'open-linux-ui.sh').read_text(encoding='utf-8')
        self.assertIn('--app=', script)
        self.assertIn('dflash-console.service', script)
        self.assertNotIn('--restart', script)

    def test_page_has_an_icon(self):
        page = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/static/icons/dflash-console-256.png', page)


if __name__ == '__main__':
    unittest.main()
