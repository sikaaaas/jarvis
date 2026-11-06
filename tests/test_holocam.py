#!/usr/bin/env python3
# test_holocam.py - the holocam (barehands) bay, tested for real: vendored files,
# then the actual stdlib server raised on loopback, ring round-trip, board
# command channel (allowlist + jail verified from the outside), routing, and
# a clean strike. No camera is needed - the sandbox has none - the vision
# part runs only in the user's browser, which is exactly where upstream put it.
import json
import os
import sys
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import arms  # noqa: E402

BH = "http://127.0.0.1:8794"

def _get_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

def _post_json(url, obj, timeout=4):
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status

class TestHolocam(unittest.TestCase):
    def test_01_vendored_files(self):
        for f in ("server.py", "stage.html", "LICENSE", "README.md",
                  "barehands.json", "bin/board.sh", "media", "sample-notes"):
            self.assertTrue(os.path.exists(os.path.join(arms.BH_DIR, f)),
                            f"missing vendored file: {f}")

    def test_02_config_and_url(self):
        self.assertEqual(arms._holocam_port(), 8794)
        self.assertEqual(arms.holocam_url(), "http://127.0.0.1:8794/stage.html")

    def test_03_start_boots_the_real_server(self):
        res = arms.holocam_start()
        self.assertIn("url", res)
        self.assertTrue(arms.STATE["holocam_running"])
        status, orb = _get_json(BH + "/orb")
        self.assertEqual(status, 200)
        self.assertEqual(orb.get("state"), "idle")
        _, cfg = _get_json(BH + "/config")
        self.assertEqual(cfg.get("name"), "JARVIS")
        titles = [o.get("title") for o in cfg.get("orbs", [])]
        for want in ("Notes", "Galaxy", "Manual", "Props"):
            self.assertIn(want, titles)

    def test_04_ring_roundtrip(self):
        self.assertTrue(arms.holocam_ring("thinking"))
        self.assertEqual(_get_json(BH + "/orb")[1]["state"], "thinking")
        self.assertTrue(arms.holocam_ring("speaking"))
        self.assertEqual(_get_json(BH + "/orb")[1]["state"], "speaking")
        self.assertTrue(arms.holocam_ring("listening"))
        self.assertEqual(_get_json(BH + "/orb")[1]["state"], "listening")
        self.assertFalse(arms.holocam_ring("dancing"))      # not one of the four words
        arms.holocam_ring("idle")

    def test_05_board_channel_and_jail(self):
        self.assertEqual(arms.holocam_board(
            {"a": "present", "title": "TEST CARD", "body": "sir, a test"}), 204)
        self.assertEqual(arms.holocam_board({"a": "format_c_drive"}), 400)   # allowlist refuses
        arms.set_last_answer("The galaxy holds every note you gave me, sir.")
        res = arms.holocam_last_answer()
        self.assertEqual(res.get("arm"), "holocam")
        self.assertIn("board", res["answer"].lower())
        self.assertEqual(arms.holocam_board(
            {"a": "add_img", "src": "../../../etc/passwd"}), 400)          # the airlock holds

    def test_06_routing(self):
        self.assertEqual(arms.route("holocam status")["arm"], "holocam")
        self.assertIn("live", arms.route("holocam status")["answer"].lower())
        self.assertEqual(arms.route("is barehands running")["arm"], "holocam")
        self.assertEqual(arms.route("open the holocam")["arm"], "holocam")   # already up: harmless
        self.assertEqual(arms.route("barehands")["arm"], "holocam")          # plain word raises it

    def test_07_strike(self):
        res = arms.holocam_stop()
        self.assertEqual(res.get("arm"), "holocam")
        self.assertFalse(arms.STATE["holocam_running"])
        time.sleep(0.4)
        self.assertFalse(arms.holocam_alive(0.8))

    def test_08_graceful_when_dark(self):
        res = arms.route("put that on the board")   # holocam down: butler explains, never crashes
        self.assertEqual(res.get("arm"), "holocam")
        self.assertFalse(arms.holocam_ring("thinking"))  # fails silent
        self.assertIn("holocam", arms.route("holocam status")["answer"].lower())

if __name__ == "__main__":
    unittest.main(verbosity=2)
