#!/usr/bin/env python3
# tests/test_resilience.py - the drowning-model law: a vendor that answers
# HTTP 503 'high demand' is knocked politely (three tries, 3s then 6s), a
# permanent refusal is NOT retried, a hung wire is admitted by the timeout,
# and the cloud brain never gets a five-minute leash. Run:
#   python tests/test_resilience.py
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402

class BrainRetryTest(unittest.TestCase):
    def test_transient_503_recovers_on_third_knock(self):
        calls = {"n": 0}

        def builder(cfg, system, messages, floor):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("HTTP 503: This model is currently experiencing high demand.")
            return "At your service, sir."

        with mock.patch.object(server.time, "sleep") as sleeper:
            out = server._brain_call_with_retries(builder, {}, "", [], 100)
        self.assertEqual(out, "At your service, sir.")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleeper.call_count, 2)          # waited twice, then answered
        waits = [c.args[0] for c in sleeper.call_args_list]
        self.assertEqual(waits, [3.0, 6.0])              # bounded backoff

    def test_permanent_404_is_never_retried(self):
        def builder(*a):
            raise RuntimeError("HTTP 404: model not found")

        with mock.patch.object(server.time, "sleep") as sleeper:
            with self.assertRaises(RuntimeError):
                server._brain_call_with_retries(builder, {}, "", [], 100)
        sleeper.assert_not_called()

    def test_three_503s_raise_after_bounded_wait(self):
        def builder(*a):
            raise RuntimeError("HTTP 503: still drowning")

        with mock.patch.object(server.time, "sleep") as sleeper:
            with self.assertRaises(RuntimeError):
                server._brain_call_with_retries(builder, {}, "", [], 100)
        self.assertEqual(sleeper.call_count, 2)          # 3s + 6s, then the honest line

    def test_wire_errors_are_not_retried(self):
        # a hung wire is admitted by the timeout - retrying a dead wire just
        # triples the silence
        def builder(*a):
            raise OSError("timed out")

        with mock.patch.object(server.time, "sleep") as sleeper:
            with self.assertRaises(OSError):
                server._brain_call_with_retries(builder, {}, "", [], 100)
        sleeper.assert_not_called()

class CloudTimeoutTest(unittest.TestCase):
    def test_cloud_brain_gets_60s_local_gets_300s(self):
        captured = {}

        def fake_request(url, **kw):
            captured["timeout"] = kw.get("timeout")
            return 503, b'{"error": {"message": "high demand"}}'

        cloud = {"base_url": "https://generativelanguage.googleapis.com", "model": "m", "api_key": "k"}
        local = {"base_url": "http://127.0.0.1:11434", "model": "m"}
        with mock.patch.object(server.net, "request", side_effect=fake_request):
            with self.assertRaises(RuntimeError):
                server._request_builder_openai(cloud, "s", [], 10)
        self.assertEqual(captured["timeout"], 60)
        with mock.patch.object(server.net, "request", side_effect=fake_request):
            with self.assertRaises(RuntimeError):
                server._request_builder_openai(local, "s", [], 10)
        self.assertEqual(captured["timeout"], 300)

if __name__ == "__main__":
    unittest.main(verbosity=2)
