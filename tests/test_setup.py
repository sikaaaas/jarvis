#!/usr/bin/env python3
# tests/test_setup.py - the honest-brain laws: when a cloud brain refuses,
# Jarvis quotes the vendor's own words and names the next step - never a bare
# "unreachable". Proven against a REAL local fake vendor over HTTP: key
# refused, model unknown, quota out, wire down, success clearing the complaint.
# Also guards the setup screen's one door (apply_setup) and the local-brain
# no-key law. Run:  python tests/test_setup.py

import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402
import voice   # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


VENDOR_PORT = _free_port()


class FakeVendor(BaseHTTPRequestHandler):
    """A cloud brain that misbehaves on demand, driven by the pasted key:
    bad-key -> 400 key refused, poor-key -> 404 model unknown,
    tired-key -> 429 quota, anything else -> a working 200 answer."""

    def log_message(self, *a):                          # quiet
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        # error bodies are list-wrapped exactly like the real Gemini OpenAI layer
        if key == "bad-key":
            return self._send(400, [{"error": {"code": 400, "message": "Please pass a valid API key", "status": "INVALID_ARGUMENT"}}])
        if key == "poor-key":
            return self._send(404, [{"error": {"code": 404, "message": "models/test-model-flash is not found for API version v1beta", "status": "NOT_FOUND"}}])
        if key == "tired-key":
            return self._send(429, [{"error": {"code": 429, "message": "Resource has been exhausted, check quota", "status": "RESOURCE_EXHAUSTED"}}])
        return self._send(200, {"choices": [{"message": {"content": "At your service, sir."}}]})

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": "test-flash-a"}, {"id": "test-flash-b"}]})
        return self._send(404, {"error": {"message": "no such door"}})


def _start_vendor():
    httpd = ThreadingHTTPServer(("127.0.0.1", VENDOR_PORT), FakeVendor)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class TestHonestBrain(unittest.TestCase):
    httpd = None

    @classmethod
    def setUpClass(cls):
        cls.httpd = _start_vendor()
        server.CFG["brains"]["testcloud"] = {
            "kind": "openai", "base_url": f"http://127.0.0.1:{VENDOR_PORT}/v1beta/openai",
            "model": "test-model-flash", "api_key": "bad-key"}
        # the fake vendor lives on loopback, but the test is about the CLOUD
        # branch - teach the is_local door that these two addresses are cloud
        cls._orig_is_local = server.net.is_local

        def _patched_is_local(url):
            if f":{VENDOR_PORT}" in str(url) or ":1/" in str(url):
                return False
            return cls._orig_is_local(url)

        server.net.is_local = _patched_is_local

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown()
        server.net.is_local = cls._orig_is_local
        server.STATE["brain"] = server.CFG.get("boot_brain", "local")
        server.CFG["brains"].pop("testcloud", None)

    def _ask(self):
        return server.brain_ask([{"role": "user", "content": "hello"}])

    def test_01_key_refused_quotes_the_vendor(self):
        server.STATE["brain"] = "testcloud"
        line, err = self._ask()
        self.assertIn("Please pass a valid API key", line)   # the vendor's own words
        self.assertIn("AGENT", line)                          # and the way out
        self.assertEqual(server.STATE["brain_error"]["why"], "key")

    def test_02_model_unknown_offers_the_shelf(self):
        server.CFG["brains"]["testcloud"]["api_key"] = "poor-key"
        server.STATE["brain"] = "testcloud"
        line, err = self._ask()
        self.assertIn("test-model-flash", line)
        self.assertIn("test-flash-a", line)                   # the /models courtesy list
        self.assertIn("test-flash-b", line)
        self.assertEqual(server.STATE["brain_error"]["why"], "model")

    def test_03_quota_out_is_named(self):
        server.CFG["brains"]["testcloud"]["api_key"] = "tired-key"
        server.STATE["brain"] = "testcloud"
        line, err = self._ask()
        self.assertIn("quota", line.lower())
        self.assertEqual(server.STATE["brain_error"]["why"], "quota")

    def test_04_wire_down_when_nothing_answers(self):
        server.CFG["brains"]["testcloud"]["base_url"] = "http://127.0.0.1:1/v1"
        server.STATE["brain"] = "testcloud"
        line, err = self._ask()
        self.assertIn("unreachable", line.lower())
        self.assertEqual(server.STATE["brain_error"]["why"], "wire")
        server.CFG["brains"]["testcloud"]["base_url"] = f"http://127.0.0.1:{VENDOR_PORT}/v1beta/openai"

    def test_05_success_clears_the_complaint(self):
        server.CFG["brains"]["testcloud"]["api_key"] = "good-key"
        server.STATE["brain"] = "testcloud"
        line, err = self._ask()
        self.assertEqual(line, "At your service, sir.")
        self.assertIsNone(err)
        self.assertIsNone(server.STATE["brain_error"])

    def test_06_placeholder_switch_points_at_the_setup_screen(self):
        line, switched = server.switch_brain("claude")       # still PUT-YOUR-KEY-HERE
        self.assertFalse(switched)
        self.assertIn("AGENT", line)
        self.assertIn("paste the full key", line)

    def test_07_local_brain_needs_no_key(self):
        # regression: 'ollama' is the key the local endpoint IGNORES by law,
        # so the local brain must never be called unconfigured for having it.
        self.assertFalse(server.brain_is_placeholder("local"))
        line, switched = server.switch_brain("local")
        self.assertTrue(switched)
        self.assertIn("costs nothing", line)

    def test_08_apply_setup_writes_key_never_echoes_it(self):
        path = server.CONFIG_PATH
        snap = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                snap = f.read()
        try:
            res = server.apply_setup("gemini", "test-key-abcdef123456", "")
            self.assertTrue(res.get("ok"))
            self.assertTrue(res.get("key_set"))
            self.assertNotIn("test-key-abcdef123456", json.dumps(res))   # never echoed
            raw = None
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            self.assertIn("test-key-abcdef123456", raw)                  # but written to config
            self.assertEqual(server.CFG["boot_brain"], "gemini")
            bad = server.apply_setup("openai", "PUT-YOUR-KEY-HERE")      # placeholder refused
            self.assertFalse(bad.get("ok"))
        finally:
            if snap is not None:
                with open(path, "wb") as f:
                    f.write(snap)
                server.CFG.update(json.loads(snap.decode("utf-8")))
            server.STATE["brain"] = server.CFG.get("boot_brain", "local")

    def test_09_voice_diagnose_never_crashes_and_explains(self):
        voice.init({"url": "http://127.0.0.1:3900"}, server.net)
        d = voice.diagnose()
        self.assertIn("home", d)
        self.assertIn("detail", d)
        self.assertIsInstance(d["detail"], str)
        self.assertTrue(len(d["detail"]) > 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
