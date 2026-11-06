#!/usr/bin/env python3
# tests/test_shortcut.py - the desktop-launcher law: one request, ONE file on
# the desktop, nothing else, nowhere else, and it points at Jarvis's own
# start script. Run:  python tests/test_shortcut.py
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402

class DesktopShortcutTest(unittest.TestCase):
    def test_linux_desktop_entry(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("this recipe is linux-specific; the mac/win paths live on those machines")
        old_home = os.environ.get("HOME")
        try:
            with tempfile.TemporaryDirectory() as home:
                os.environ["HOME"] = home
                res = server.make_desktop_shortcut()
                self.assertTrue(res.get("ok"), res)
                path = res["path"]
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(os.access(path, os.X_OK), "the launcher must be executable")
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("[Desktop Entry]", content)
                self.assertIn("start.sh", content)
                self.assertIn(ROOT, content, "it must point at THIS jarvis folder")
                desk = os.path.join(home, "Desktop")
                self.assertEqual(os.listdir(desk), ["jarvis.desktop"],
                                 "exactly one file lands on the desktop - nothing else")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home

    def test_windows_and_mac_recipes_exist_in_code(self):
        # honest guard: the two recipes the sandbox cannot execute must at
        # least be present in the source, aimed at the right launchers
        with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("WScript.Shell", src)          # the real .lnk writer
        self.assertIn("start.bat", src)              # windows launcher target
        self.assertIn("Jarvis.command", src)         # mac launcher name

if __name__ == "__main__":
    unittest.main(verbosity=2)
