#!/usr/bin/env python3
# tests/test_full_qa.py - THE FULL QA PASS, part 1: code logic.
#
# Every law of the project, proven without a live server:
#   BOOT LAWS      - zero bytes fetched at boot; template shelf honest;
#                    persona stays lean and placeholder-free.
#   BRAIN LAWS     - the fallback order (Gemini first, bounded), every
#                    vendor-complaint branch, the setup-needed law.
#   EARS LAWS      - the /health ladder (whisper > gemini > browser), the
#                    Gemini ears' request shape and cooldown, honest status,
#                    `hear.py status` needing no network.
#   VOICE LAWS     - the cloud tier: Gemini TTS shape + cascade + cache,
#                    the OpenAI RIFF law, the down-breaker, cloud_capable.
#   NET LAWS       - the one logged door never writes the key to the log.
#   VIEWER LAWS    - the ears ladder in boot(), Firefox/Zen honesty, the
#                    mute gate, chunk packing, voice ranking (run in node).
#   HOUSEKEEPING   - gitignore, leak scan, README/handoff sync, vendored AGPL.
#
# Run:  python -m unittest tests.test_full_qa -v   (or the whole discover pass)

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import net          # noqa: E402
import hear         # noqa: E402
import voice        # noqa: E402
import server       # noqa: E402
import memory       # noqa: E402

NODE = shutil.which("node")

def _fake_net(fn):
    """A net MODULE double: hear/voice call net.request(...), so the mock
    needs a .request attribute, not a call-through side_effect."""
    m = mock.Mock()
    m.request = mock.Mock(side_effect=fn)
    return m

def _bare_handler():
    """A Handler instance with no socket - enough for health()."""
    return server.Handler.__new__(server.Handler)

def _wav_bytes(rate, ms=200):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * ms / 1000))
    return buf.getvalue()

# ===========================================================================
# BOOT LAWS
# ===========================================================================
class BootLaws(unittest.TestCase):
    def test_01_boot_fetches_zero_bytes_from_the_internet(self):
        """THE download law: boot work (ears bootstrap, status, voice probes,
        setup check) opens NO socket that is not localhost - even when the
        Whisper engine is installed but its weights are missing."""
        seen = []
        real = net.request

        def recorder(url, **kw):
            seen.append(url)
            return real(url, **kw)

        old_backend = hear._backend
        hear._backend = "ct2"                     # pretend the engine IS installed
        try:
            with mock.patch("net.request", recorder):
                hear.bootstrap()                  # weights missing -> refuse, print, NO fetch
                st = hear.status()
                voice.available()                 # probes 127.0.0.1 only
                voice.diagnose()
                server._setup_needed()
            remote = [u for u in seen if not net.is_local(u)]
            self.assertEqual(remote, [], f"boot touched the internet: {remote}")
            self.assertIn(st["weights"], ("missing", "not installed", "on disk"))
        finally:
            hear._backend = old_backend
            hear._weights_missing = False

    def test_02_template_brain_shelf_is_honest(self):
        with open(os.path.join(ROOT, "config.template.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        shelf = set(cfg.get("brains", {}))
        for name in ("gemini", "openrouter", "claude", "openai", "local"):
            self.assertIn(name, shelf, f"{name} missing from the brain shelf")
        for name, b in cfg["brains"].items():
            key = str(b.get("api_key", "x")).strip().lower()
            self.assertIn(key, server.PLACEHOLDER_KEYS | {"ollama"},
                          f"{name} ships with a non-placeholder key")
            self.assertNotIn("sk-", key)
        self.assertEqual(cfg.get("port", 4700), 4700)
        self.assertTrue(cfg.get("hearing", {}).get("allow_browser_stt", True))
        self.assertTrue(cfg.get("voice", {}).get("cloud_fallback", True))

    def test_03_persona_stays_lean_and_fills_its_placeholders(self):
        words = len(server.PERSONA.split())
        self.assertLess(words, 300, f"PERSONA is {words} words - its own law says under 300")
        for tag in ("{tags}", "{home}", "{platform}"):
            self.assertNotIn(tag, server.SYSTEM_PREFIX, f"unfilled placeholder {tag}")
        self.assertIn("Never say you cannot touch this PC", server.SYSTEM_PREFIX,
                      "the persona must teach the hands")
        self.assertIn("TOOL:", server.SYSTEM_PREFIX)

    def test_04_hear_cli_status_needs_no_network(self):
        """`python hear.py status` must print honest state with zero downloads."""
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "hear.py"), "status"],
                           capture_output=True, text=True, timeout=60, cwd=ROOT, env=env)
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        st = json.loads(p.stdout[p.stdout.index("{"):p.stdout.rindex("}") + 1])
        self.assertIn(st["weights"], ("not installed", "missing", "on disk"))

    def test_05_memory_paths_live_on_this_disk(self):
        self.assertTrue(memory.JOURNAL.endswith("journal.jsonl"))
        self.assertTrue(memory.JOURNAL.startswith(ROOT))

# ===========================================================================
# BRAIN LAWS
# ===========================================================================
class BrainLaws(unittest.TestCase):
    def setUp(self):
        self._cfg = json.loads(json.dumps(server.CFG))
        self._brain = server.STATE["brain"]

    def tearDown(self):
        server.CFG.clear()
        server.CFG.update(self._cfg)
        server.STATE["brain"] = self._brain
        server.STATE.pop("brain_error", None)

    def test_10_fallback_order_gemini_first_bounded_three(self):
        server.CFG["brains"] = {
            "gemini": {"kind": "openai", "base_url": "https://g", "model": "m", "api_key": "k1"},
            "openrouter": {"kind": "openai", "base_url": "https://o", "model": "m", "api_key": "k2"},
            "claude": {"kind": "anthropic", "base_url": "https://c", "model": "m", "api_key": "k3"},
            "openai": {"kind": "openai", "base_url": "https://a", "model": "m", "api_key": "k4"},
            "dead": {"kind": "openai", "base_url": "https://d", "model": "m", "api_key": "PUT-YOUR-KEY-HERE"},
            "local": {"kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q", "api_key": "ollama"},
        }
        order = server._fallback_order("openrouter")
        self.assertEqual(order[0], "gemini", "Gemini (free tier) borrows first")
        self.assertIn("local", order)
        self.assertNotIn("dead", order, "placeholder brains never qualify")
        self.assertNotIn("openrouter", order, "the primary never lends to itself")
        self.assertLessEqual(len(order), 3, "a bad night must not become a five-brain marathon")

    def test_11_vendor_complaint_5xx_offers_the_wait(self):
        server.CFG["brains"] = {"openai": {"kind": "openai", "base_url": "https://api.openai.com/v1",
                                           "model": "gpt-4.1-mini", "api_key": "k"}}
        line = server._vendor_complaint("openai", RuntimeError("HTTP 503: overloaded"))
        self.assertIn("ChatGPT", line)
        self.assertIn("knocked three times", line)
        self.assertEqual(server.STATE["brain_error"]["why"], "http")

    def test_12_vendor_complaint_wire_down_names_the_brain(self):
        line = server._vendor_complaint("gemini", OSError("connection refused"))
        self.assertIn("Gemini", line)
        self.assertIn("unreachable", line)
        self.assertEqual(server.STATE["brain_error"]["why"], "wire")

    def test_13_vendor_complaint_names_each_shelf(self):
        for name, brand in (("gemini", "Gemini"), ("openai", "ChatGPT"),
                            ("openrouter", "OpenRouter"), ("claude", "Claude")):
            server.CFG["brains"] = {name: {"kind": "anthropic" if name == "claude" else "openai",
                                           "base_url": "https://x", "model": "m", "api_key": "k"}}
            line = server._vendor_complaint(name, RuntimeError("HTTP 500: nope"))
            self.assertIn(brand, line, f"{name} must be called {brand}")

    def test_14_brain_code_parses_http_prefix(self):
        self.assertEqual(server._brain_code("HTTP 429: slow down"), 429)
        self.assertEqual(server._brain_code("wire down"), 0)

    def test_15_placeholder_laws(self):
        server.CFG["brains"] = {
            "a": {"kind": "openai", "base_url": "https://x", "model": "m", "api_key": "PUT-YOUR-KEY-HERE"},
            "b": {"kind": "openai", "base_url": "https://x", "model": "change-me", "api_key": "k"},
            "c": {"kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q", "api_key": "ollama"},
        }
        self.assertTrue(server.brain_is_placeholder("a"))
        self.assertTrue(server.brain_is_placeholder("b"))
        self.assertFalse(server.brain_is_placeholder("c"), "a local brain needs no key by law")
        self.assertTrue(server.brain_is_placeholder("ghost"))

    def test_16_setup_needed_tracks_the_boot_brain(self):
        server.CFG["brains"] = {
            "gemini": {"kind": "openai", "base_url": "https://g", "model": "m", "api_key": "PUT-YOUR-KEY-HERE"},
            "local": {"kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "q", "api_key": "ollama"},
        }
        # the FIRST law: no config.json at all -> setup needed, whatever the shelf says
        self.assertTrue(server._setup_needed())
        # now a real config exists: only a placeholder CLOUD boot brain demands setup
        tmp = tempfile.mkdtemp(prefix="jarvis-setup-")
        old_path = server.CONFIG_PATH
        try:
            server.CONFIG_PATH = os.path.join(tmp, "config.json")
            with open(server.CONFIG_PATH, "w") as f:
                json.dump({"boot_brain": "local"}, f)
            server.CFG["boot_brain"] = "local"
            self.assertFalse(server._setup_needed(), "a local boot brain needs no key")
            server.CFG["boot_brain"] = "gemini"
            self.assertTrue(server._setup_needed(), "placeholder cloud boot brain = setup needed")
        finally:
            server.CONFIG_PATH = old_path
            shutil.rmtree(tmp, ignore_errors=True)

    def test_17_apply_setup_refuses_tiny_keys(self):
        res = server.apply_setup("gemini", "short")
        self.assertFalse(res["ok"])
        self.assertIn("full", res["error"])

    def test_18_apply_setup_refuses_unknown_brain(self):
        res = server.apply_setup("no-such-brain", "whatever-key-123")
        self.assertFalse(res["ok"])
        self.assertIn("shelf", res["error"])

    def test_19_trimmed_config_never_crashes_the_butler(self):
        """A hand-trimmed config.json (no 'brains' section) must not kill
        /health or the 'which brain' route - the live rundown caught exactly
        this: KeyError 'brains' on every /health poll."""
        trimmed = {"port": 8977}
        server._normalize_cfg(trimmed)
        self.assertIn("brains", trimmed, "the shelf must exist, even empty")
        self.assertEqual(trimmed["boot_brain"], "claude",
                         "an empty shelf falls back to the classic name")
        # and with a shelf but a stale boot brain: the first brain wins
        shelfd = {"port": 8977, "brains": {"gemini": {"kind": "openai"},
                                           "openrouter": {"kind": "openai"}},
                  "boot_brain": "claude"}
        server._normalize_cfg(shelfd)
        self.assertEqual(shelfd["boot_brain"], "gemini",
                         "a boot brain not on the shelf falls to the first one that is")
        # health() itself must answer on the trimmed shelf (the live crash)
        with mock.patch.dict(server.CFG, {"port": 8977}, clear=True):
            server._normalize_cfg(server.CFG)
            h = _bare_handler().health()
            self.assertTrue(h["ok"])
            self.assertIsNone(h["brain_kind"], "no brain on the shelf -> no kind, no crash")

    def test_19b_broken_json_boots_on_the_template(self):
        """One trailing comma in a hand-edited config.json must never keep
        the butler from booting: the template steps in, loudly and honestly."""
        path = server.CONFIG_PATH
        snap = None
        existed = os.path.exists(path)
        if existed:
            snap = open(path, "rb").read()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"port": 4700, "brains": {')      # broken JSON
            cfg, source = server.load_config()
            self.assertIn("broken", source, "the source line must say the file is broken")
            self.assertIn("brains", cfg, "the template shelf is there")
            with open(path, "w", encoding="utf-8") as f:
                f.write("[1, 2, 3]")                       # valid JSON, wrong shape
            cfg, source = server.load_config()
            self.assertIn("broken", source)
            self.assertIsInstance(cfg, dict)
        finally:
            if existed:
                with open(path, "wb") as f:
                    f.write(snap)
            elif os.path.exists(path):
                os.remove(path)

    def test_19c_setup_heals_a_broken_config(self):
        """The setup door rewrites config.json whole - so one visit to the
        setup screen mends a file that a hand-edit mangled."""
        path = server.CONFIG_PATH
        snap = None
        existed = os.path.exists(path)
        if existed:
            snap = open(path, "rb").read()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json at all")
            server._set_config_key(["boot_brain"], "gemini")   # must not raise
            healed = json.load(open(path, encoding="utf-8"))    # valid again
            self.assertEqual(healed["boot_brain"], "gemini")
            self.assertIn("brains", healed, "the healed file carries the shelf")
            with open(path, "w", encoding="utf-8") as f:
                f.write("[1, 2, 3]")                           # wrong shape heals too
            server._set_config_key(["boot_brain"], "claude")
            self.assertEqual(json.load(open(path, encoding="utf-8"))["boot_brain"], "claude")
        finally:
            if existed:
                with open(path, "wb") as f:
                    f.write(snap)
            elif os.path.exists(path):
                os.remove(path)

# ===========================================================================
# EARS LAWS
# ===========================================================================
class EarsLaws(unittest.TestCase):
    def setUp(self):
        self._gem = dict(hear._gemini)
        hear._gemini["down_until"] = 0.0

    def tearDown(self):
        hear._gemini.clear()
        hear._gemini.update(self._gem)

    def test_20_gemini_ears_request_shape(self):
        """One utterance = one inline-audio request to the Gemini endpoint,
        keyed by the header, prompted for words only."""
        calls = []

        def fake_net(url, method="GET", data=None, headers=None, timeout=0, reason=""):
            calls.append({"url": url, "headers": headers, "data": data})
            body = {"candidates": [{"content": {"parts": [{"text": "hello there sir"}]}}]}
            return 200, json.dumps(body).encode()

        hear._gemini.update({"net": _fake_net(fake_net),
                             "key_for": lambda n: "test-key-123",
                             "model": "gem-ears-test"})
        text = hear._gemini_transcribe(b"fakewavbytes")
        self.assertEqual(text, "hello there sir")
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertTrue(c["url"].endswith("/models/gem-ears-test:generateContent"))
        self.assertEqual(c["headers"]["x-goog-api-key"], "test-key-123")
        sent = json.loads(c["data"].decode())
        parts = sent["contents"][0]["parts"]
        self.assertIn("Transcribe", parts[0]["text"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "audio/wav")
        self.assertEqual(parts[1]["inline_data"]["data"], "ZmFrZXdhdmJ5dGVz")  # base64 of fakewavbytes

    def test_21_gemini_ears_cooldown_never_hammers_a_dead_key(self):
        knocks = []

        def fake_net(url, **kw):
            knocks.append(url)
            return 403, json.dumps({"error": {"message": "key refused"}}).encode()

        hear._gemini.update({"net": _fake_net(fake_net),
                             "key_for": lambda n: "test-key-123",
                             "down_until": 0.0})
        with self.assertRaises(RuntimeError):
            hear._gemini_transcribe(b"x")
        self.assertEqual(len(knocks), 1)
        with self.assertRaises(RuntimeError) as cm:
            hear._gemini_transcribe(b"x")
        self.assertIn("cooling down", str(cm.exception))
        self.assertEqual(len(knocks), 1, "the cooldown must skip the wire entirely")

    def test_22_gemini_ears_silence_is_a_failure(self):
        def fake_net(url, **kw):
            body = {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}
            return 200, json.dumps(body).encode()

        hear._gemini.update({"net": _fake_net(fake_net),
                             "key_for": lambda n: "k", "down_until": 0.0})
        with self.assertRaises(RuntimeError):
            hear._gemini_transcribe(b"x")

    def test_21b_day_quota_rests_long_and_names_the_remedy(self):
        """The live catch: Google's 'You exceeded your current quota' (the
        free DAY spent) used to get the same flat 60 s cooldown, so the
        banner kept promising 'a minute' all day long."""
        knocks = []

        def fake_net(url, **kw):
            knocks.append(url)
            body = {"error": {"code": 429, "message": "You exceeded your current quota, "
                              "please check your plan and billing details.",
                              "status": "RESOURCE_EXHAUSTED",
                              "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                           "violations": [{"quotaMetric": "generativelanguage"
                                                           ".googleapis.com/generate_content_"
                                                           "free_tier_requests",
                                                           "quotaId": "GenerateRequestsPerDay"
                                                                       "PerProjectPerModel-FreeTier"}]}]}}
            return 429, json.dumps(body).encode()

        hear._gemini.update({"net": _fake_net(fake_net),
                             "key_for": lambda n: "test-key-123",
                             "down_until": 0.0, "down_reason": None})
        try:
            with self.assertRaises(RuntimeError) as cm:
                hear._gemini_transcribe(b"x")
            self.assertIn("free day", str(cm.exception), "the day quota must be named")
            self.assertIn("hear.py download", str(cm.exception), "the remedy must be named")
            rest = hear._gemini["down_until"] - time.time()
            self.assertGreater(rest, hear.DAY_QUOTA_REST - 10,
                               "a spent day rests much longer than a minute")
            self.assertEqual(hear._gemini["down_reason"], "day")
            # while resting: no more knocks, and the message stays honest
            with self.assertRaises(RuntimeError) as cm2:
                hear._gemini_transcribe(b"x")
            self.assertEqual(len(knocks), 1, "the cooldown must skip the wire entirely")
            self.assertIn("free day", str(cm2.exception))
            self.assertIn("midnight", hear.ears_note(), "the note says when the reset comes")
        finally:
            hear._gemini["down_until"] = 0.0
            hear._gemini["down_reason"] = None

    def test_21c_minute_quota_rests_briefly(self):
        """A per-minute burst is a short rest, honestly described."""
        def fake_net(url, **kw):
            body = {"error": {"code": 429, "message": "Resource has been exhausted",
                              "status": "RESOURCE_EXHAUSTED",
                              "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                           "violations": [{"quotaId": "GenerateRequestsPerMinute"
                                                                       "PerProjectPerModel-FreeTier"}]}]}}
            return 429, json.dumps(body).encode()

        hear._gemini.update({"net": _fake_net(fake_net),
                             "key_for": lambda n: "test-key-123",
                             "down_until": 0.0, "down_reason": None})
        try:
            with self.assertRaises(RuntimeError):
                hear._gemini_transcribe(b"x")
            rest = hear._gemini["down_until"] - time.time()
            self.assertLess(rest, 75, "a per-minute burst rests about a minute, not half a day")
            self.assertGreater(rest, 55)
            self.assertEqual(hear._gemini["down_reason"], "minute")
            self.assertIn("minute", hear.ears_note())
        finally:
            hear._gemini["down_until"] = 0.0
            hear._gemini["down_reason"] = None

    def test_21d_health_carries_the_quota_note(self):
        """/health's ears_detail speaks when the Gemini ears are resting."""
        hear._gemini["down_until"] = time.time() + 300
        hear._gemini["down_reason"] = "day"
        try:
            with mock.patch("hear.available", return_value=False), \
                 mock.patch("hear.gemini_ears_available", return_value=True):
                h = _bare_handler().health()
            self.assertIn("free day", h["ears_detail"] or "",
                          "the butler says WHY the mic is quiet")
        finally:
            hear._gemini["down_until"] = 0.0
            hear._gemini["down_reason"] = None

    def test_23_gemini_ears_need_a_real_key(self):
        hear._gemini["key_for"] = lambda n: "put-your-key-here"
        self.assertFalse(hear.gemini_ears_available())
        hear._gemini["key_for"] = lambda n: ""
        self.assertFalse(hear.gemini_ears_available())
        hear._gemini["key_for"] = lambda n: "AIza-real-looking"
        self.assertTrue(hear.gemini_ears_available())

    def test_24_health_ears_ladder_whisper_then_gemini_then_browser(self):
        """THE EARS LADDER, from /health's point of view."""
        old_allow = server.CFG.setdefault("hearing", {}).get("allow_browser_stt", True)
        server.CFG["hearing"]["allow_browser_stt"] = True
        try:
            with mock.patch("hear.available", return_value=True):
                self.assertEqual(_bare_handler().health()["ears"], "whisper")
            with mock.patch("hear.available", return_value=False), \
                 mock.patch("hear.gemini_ears_available", return_value=True):
                self.assertEqual(_bare_handler().health()["ears"], "gemini")
            with mock.patch("hear.available", return_value=False), \
                 mock.patch("hear.gemini_ears_available", return_value=False):
                self.assertEqual(_bare_handler().health()["ears"], "browser")
                server.CFG["hearing"]["allow_browser_stt"] = False
                self.assertEqual(_bare_handler().health()["ears"], "none")
        finally:
            server.CFG["hearing"]["allow_browser_stt"] = old_allow

    def test_25_health_carries_the_weights_hint(self):
        with mock.patch("hear.status", return_value={"hint": "run python hear.py download"}), \
             mock.patch("hear.available", return_value=False), \
             mock.patch("hear.gemini_ears_available", return_value=False):
            h = _bare_handler().health()
        self.assertEqual(h["ears_detail"], "run python hear.py download")

    def test_26_one_second_of_silence_is_a_valid_wav(self):
        raw = hear._one_second_of_silence()
        with wave.open(io.BytesIO(raw), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnframes(), 16000)
            self.assertEqual(w.getnchannels(), 1)

    def test_27_status_is_honest_without_a_backend(self):
        old = (hear._backend, hear._ready, hear._weights_missing)
        hear._backend, hear._ready, hear._weights_missing = None, False, False
        try:
            st = hear.status()
            self.assertEqual(st["weights"], "not installed")
            self.assertIsNone(st["hint"])
            self.assertFalse(hear.weights_on_disk())
            self.assertFalse(hear.available())
        finally:
            hear._backend, hear._ready, hear._weights_missing = old

    def test_28_transcribe_any_prefers_whisper_when_ready(self):
        old = (hear._backend, hear._ready)
        hear._backend, hear._ready = "ct2", True
        try:
            with mock.patch("hear.transcribe", return_value={"text": "local heard", "seconds": 0.1}) as tr:
                out = hear.transcribe_any(b"wav", "full")
            self.assertEqual(out["text"], "local heard")
            self.assertNotIn("via", out, "whisper output keeps its plain shape")
            tr.assert_called_once()
        finally:
            hear._backend, hear._ready = old

# ===========================================================================
# VOICE LAWS - the cloud tier
# ===========================================================================
class VoiceLaws(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cache = voice.CACHE_DIR
        cls.tmp = tempfile.mkdtemp(prefix="jarvis-voice-")
        voice.CACHE_DIR = cls.tmp

    @classmethod
    def tearDownClass(cls):
        voice.CACHE_DIR = cls._cache
        shutil.rmtree(cls.tmp, ignore_errors=True)
        voice.init(server.CFG.get("voice", {}), net, server._brain_api_key)  # boot state back
        voice._state["cloud_down_until"] = 0.0

    def setUp(self):
        self._cfg = dict(voice._cfg)
        self._state = dict(voice._state)
        voice._state["cloud_down_until"] = 0.0

    def tearDown(self):
        voice._cfg.clear()
        voice._cfg.update(self._cfg)
        voice._state.clear()
        voice._state.update(self._state)

    @staticmethod
    def _gemini_payload(pcm):
        import base64
        return json.dumps({"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "audio/L16;rate=24000",
                            "data": base64.b64encode(pcm).decode()}}]}}]}).encode()

    def test_30_pcm_to_wav_wraps_a_real_header(self):
        wav = voice._pcm_to_wav(b"\x01\x02" * 100, 24000)
        self.assertTrue(wav[:4] == b"RIFF" and wav[8:12] == b"WAVE")
        with wave.open(io.BytesIO(wav), "rb") as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.getnframes(), 100)

    def test_31_gemini_tts_shape_cascade_and_cache(self):
        """First model 404s -> the cascade knocks the second; the WAV lands in
        the cache and the SECOND call touches no wire at all."""
        calls = []
        pcm = b"\x10\x00" * 1000

        def fake_net(url, method="GET", data=None, headers=None, timeout=0, reason=""):
            calls.append({"url": url, "headers": headers, "data": data})
            if "gemini-3.8-flash-tts" in url:
                return 404, b'{"error": {"message": "model gone"}}'
            return 200, self._gemini_payload(pcm)

        voice._cfg.clear()
        voice._cfg.update({"cloud_fallback": True, "gemini_voice": "Charon"})
        with mock.patch.object(voice, "net", _fake_net(fake_net)):
            got = voice._gemini_tts("Stand by, sir.", "test-key")
            self.assertIsNotNone(got)
            wav, tag = got
            self.assertEqual(tag, "cloud:gemini:Charon")
            self.assertTrue(wav[:4] == b"RIFF")
            self.assertEqual(len(calls), 2, "the cascade must move to the next model")
            self.assertEqual(calls[0]["headers"]["x-goog-api-key"], "test-key")
            body = json.loads(calls[1]["data"].decode())
            self.assertEqual(body["generationConfig"]["responseModalities"], ["AUDIO"])
            self.assertEqual(body["generationConfig"]["speechConfig"]["voiceConfig"]
                             ["prebuiltVoiceConfig"]["voiceName"], "Charon")
            with mock.patch.object(voice, "net", _fake_net(
                    lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache must not touch the wire")))):
                wav2, tag2 = voice._gemini_tts("Stand by, sir.", "test-key")
            self.assertEqual(wav2, wav)
            self.assertEqual(tag2, tag)

    def test_32_openai_tts_requires_riff(self):
        voice._cfg.clear()
        voice._cfg.update({"cloud_fallback": True, "openai_voice": "onyx"})
        ok = _fake_net(lambda *a, **k: (200, b"RIFFxxxxWAVEfmt fake"))
        with mock.patch.object(voice, "net", ok):
            got = voice._openai_tts("hello", "k")
        self.assertIsNotNone(got)
        self.assertEqual(got[1], "cloud:openai:onyx")
        bad = _fake_net(lambda *a, **k: (200, b'{"error": "not audio"}'))
        with mock.patch.object(voice, "net", bad):
            self.assertIsNone(voice._openai_tts("other text", "k"))

    def test_33_speak_cloud_breaker_goes_straight_to_browser(self):
        """A dead cloud voice is skipped INSTANTLY for five minutes - the text
        never waits on a voice that stopped coming."""
        import time as _t
        voice._cfg.clear()
        voice._cfg.update({"cloud_fallback": True})
        dead = _fake_net(lambda *a, **k: (_ for _ in ()).throw(OSError("wire down")))
        with mock.patch.object(voice, "net", dead), \
             mock.patch.object(voice, "key_for", lambda n: "k"):
            self.assertIsNone(voice.speak_cloud("speak to me"))
            self.assertGreater(voice._state["cloud_down_until"], _t.time())
            knocks = dead.request.call_count
            self.assertIsNone(voice.speak_cloud("speak again"))
            self.assertEqual(dead.request.call_count, knocks, "the breaker must skip the wire")

    def test_34_speak_cloud_honors_cloud_fallback_false(self):
        voice._cfg.clear()
        voice._cfg.update({"cloud_fallback": False})
        with mock.patch.object(voice, "net", _fake_net(
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call when disabled")))), \
             mock.patch.object(voice, "key_for", lambda n: "k"):
            self.assertIsNone(voice.speak_cloud("hi"))
        self.assertFalse(voice.cloud_capable())

    def test_35_cloud_capable_needs_a_key(self):
        voice._cfg.clear()
        voice._cfg.update({"cloud_fallback": True})
        with mock.patch.object(voice, "key_for", lambda n: None):
            self.assertFalse(voice.cloud_capable())
        with mock.patch.object(voice, "key_for", lambda n: "AIza-key"):
            self.assertTrue(voice.cloud_capable())

    def test_36_speak_cache_hit_needs_no_engine(self):
        wav = _wav_bytes(22050)
        p = os.path.join(voice.CACHE_DIR, "fake.wav")
        with open(p, "wb") as f:
            f.write(wav)
        with mock.patch.object(voice, "_cache_path", return_value=p), \
             mock.patch.object(voice, "net", _fake_net(
                 lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache hit must not call the engine")))):
            self.assertEqual(voice.speak("cached line"), wav)

    def test_37_speak_returns_none_when_engine_dark(self):
        with mock.patch.object(voice, "net", _fake_net(
                lambda *a, **k: (_ for _ in ()).throw(OSError("no engine")))):
            self.assertIsNone(voice.speak("nobody home"))

# ===========================================================================
# NET LAWS - the one logged door
# ===========================================================================
class NetLaws(unittest.TestCase):
    def test_40_is_local_law(self):
        for u in ("http://127.0.0.1:4700", "http://localhost:3900/x", "http://[::1]:8794/stage.html",
                  "http://localhost:11434/v1"):
            self.assertTrue(net.is_local(u), u)
        for u in ("https://api.openai.com/v1", "https://generativelanguage.googleapis.com/v1beta",
                  "https://openrouter.ai/api/v1", "http://169.254.1.1/"):
            self.assertFalse(net.is_local(u), u)

    def test_41_the_log_never_sees_the_key(self):
        """Every request is logged with host+reason - never with headers."""
        tmp = tempfile.mkdtemp(prefix="jarvis-net-")
        old = net.LOG_PATH
        net.LOG_PATH = os.path.join(tmp, "outbound.log")
        try:
            try:                                    # the wire itself may refuse - that is fine
                net.request("http://127.0.0.1:9/x", headers={"Authorization": "Bearer sk-super-secret"},
                            timeout=1, reason="qa:key-safety")
            except OSError:
                pass
            line = open(net.LOG_PATH, encoding="utf-8").read().strip()
            self.assertIn("qa:key-safety", line)
            self.assertNotIn("sk-super-secret", line, "the key must never reach the log")
        finally:
            net.LOG_PATH = old
            shutil.rmtree(tmp, ignore_errors=True)

# ===========================================================================
# VIEWER LAWS - static laws in python, dynamic laws in node
# ===========================================================================
def _viewer_src():
    return open(os.path.join(ROOT, "viewer", "index.html"), encoding="utf-8").read()

class ViewerStaticLaws(unittest.TestCase):
    def test_50_boot_ears_ladder_order(self):
        """whisper -> browser-capable -> gemini -> honest message. Firefox/Zen
        without a key must land on the honest message, never a dead mic."""
        src = _viewer_src()
        boot = src[src.index("async function boot()"):src.index("\nboot();")]
        self.assertIn('h.ears === "whisper"', boot)
        self.assertIn("window.webkitSpeechRecognition", boot)
        self.assertIn('h.ears === "gemini"', boot)
        self.assertIn("startMic", boot, "gemini ears must raise the server mic")
        self.assertIn("startBrowserEars", boot)

    def test_51_firefox_zen_honesty_names_the_fixes(self):
        src = _viewer_src()
        self.assertIn("/zen/i", src, "Zen must be named in the no-ears message")
        self.assertIn("/firefox/i", src, "Firefox must be named")
        self.assertIn("aistudio.google.com", src, "the free Gemini key is the fix")
        self.assertIn("Chrome or Edge", src, "and the browser that has ears")
        self.assertIn("Typing still works", src, "honesty always leaves a working door")

    def test_52_mute_gate_lives_in_speak(self):
        src = _viewer_src()
        i = src.index("function speak(text, opts)")
        speak = src[i:src.index("\n}", i)]
        self.assertIn("if (MUTE)", speak)
        self.assertIn('return Promise.resolve("muted")', speak)
        self.assertEqual(src.count("const MUTE ="), 1)

    def test_53_interrupt_stop_wait_is_enforced(self):
        src = _viewer_src()
        self.assertRegex(src, r"stop\|wait")
        self.assertIn("stopSpeaking", src)

    def test_54_client_chunks_fit_the_server_cap(self):
        m = re.search(r"const CHUNK_CHARS\s*=\s*(\d+)", _viewer_src())
        self.assertIsNotNone(m)
        self.assertGreater(int(m.group(1)), 0)
        self.assertLessEqual(int(m.group(1)), server.TTS_CHUNK_CAP,
                             "client chunks must never trip the 413 cap")

    def test_55_desktop_chip_and_setup_screen_wiring(self):
        src = _viewer_src()
        self.assertIn('id="tglDesk"', src)
        self.assertIn('"/desktop-shortcut"', src)
        setup = open(os.path.join(ROOT, "viewer", "setup.html"), encoding="utf-8").read()
        self.assertIn("/setup", setup)
        self.assertIn("/setup/test", setup)
        self.assertIn("FREE tier", setup, "Gemini must be offered as the free option")

    def test_56_viewer_carries_no_real_keys_and_the_pinned_libs(self):
        vendor = os.path.join(ROOT, "viewer", "vendor")
        for lib in ("three.min.js", "3d-force-graph.min.js"):
            self.assertTrue(os.path.exists(os.path.join(vendor, lib)), f"pinned lib missing: {lib}")
        for f in os.listdir(os.path.join(ROOT, "viewer")):
            p = os.path.join(ROOT, "viewer", f)
            if os.path.isfile(p) and f.endswith((".html", ".js")):
                body = open(p, encoding="utf-8", errors="replace").read()
                for pat in (r"AIza[0-9A-Za-z\-_]{20,}", r"sk-[A-Za-z0-9]{20,}",
                            r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}"):
                    self.assertIsNone(re.search(pat, body), f"real-looking key in viewer/{f}")

@unittest.skipUnless(NODE, "node not available - JS laws skipped")
class ViewerJsLaws(unittest.TestCase):
    @staticmethod
    def _extract(name):
        src = _viewer_src()
        i = src.index(f"function {name}(")
        j = src.index("\n}", i)
        return src[i:j + 2]

    def _node(self, setup, expr):
        js = setup + "\nconsole.log(JSON.stringify(eval(" + json.dumps(expr) + ")));"
        p = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_57_pack_sentences_never_cuts_mid_sentence(self):
        fn = self._extract("packSentences")
        text = ("One line here. A second line follows! And a question? "
                "Then a very long final sentence that keeps going and going and going "
                "and going and going and going and going and going and going and going.")
        chunks, rejoins = self._node(fn + f"\nconst t = {json.dumps(text)};",
                                     "[packSentences(t, 60), packSentences(t, 60).join(' ') === t]")
        self.assertTrue(rejoins, "sentence-packed chunks must rejoin to the original")
        for c in chunks:
            self.assertRegex(c.strip(), r'[.!?…]["\')\]]*$',
                             f"chunk cut mid-sentence: {c!r}")

    def test_58_voice_ranking_casts_the_butler(self):
        fn = self._extract("rankBrowserVoice")
        voices = [
            {"name": "Microsoft Zira - English (Female)", "lang": "en-US"},
            {"name": "Google UK English Male", "lang": "en-GB"},
            {"name": "Daniel", "lang": "en-GB"},
            {"name": "Bad News", "lang": "en-US"},
            {"name": "Thomas", "lang": "fr-FR"},
            {"name": "Microsoft David - English (Male)", "lang": "en-US"},
        ]
        scores = self._node(fn + "\nconst vs = " + json.dumps(voices) + ";",
                            "vs.map(v => rankBrowserVoice(v))")
        ranked = sorted(zip(scores, voices), key=lambda t: -t[0])
        self.assertEqual(ranked[0][1]["name"], "Google UK English Male")
        self.assertLess(ranked[-1][0], ranked[0][0], "novelty/non-english/female must sink")

    def test_59_wake_words_law(self):
        m = re.search(r"const WAKE_WORDS = (.*?);", _viewer_src())
        self.assertIsNotNone(m)
        law = m.group(1)
        hit, stripped, hit2, miss = self._node(
            f"const WAKE_WORDS = {law};",
            "[WAKE_WORDS.test('jarvis, open the pod bay'),"
            " 'jarvis, open the pod bay'.replace(WAKE_WORDS, '').trim(),"
            " WAKE_WORDS.test('hey jarvis! status'),"
            " WAKE_WORDS.test('jarvison supplies the beans')]")
        self.assertTrue(hit)
        self.assertEqual(stripped, "open the pod bay")
        self.assertTrue(hit2)
        self.assertFalse(miss, "the near-miss name must not wake the butler")

    def test_60_browser_tune_stays_sane(self):
        fn = self._extract("pickBrowserTune")
        for name in ("Microsoft George - Online (Natural)", "Microsoft David"):
            tune = self._node(fn + f"\nconst v = {json.dumps({'name': name})};",
                              "pickBrowserTune(v)")
            self.assertTrue(0.5 < tune["rate"] < 1.6)
            self.assertTrue(0.4 < tune["pitch"] < 1.6)

# ===========================================================================
# HOUSEKEEPING
# ===========================================================================
class Housekeeping(unittest.TestCase):
    def _tracked(self):
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, "git ls-files failed - run inside the repo")
        return [l for l in out.stdout.splitlines() if l.strip()]

    def test_70_gitignore_blocks_the_private_shelves(self):
        gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        for token in ("config.json", "wav-cache/", "logs/", "memory/", "notes/captures/"):
            self.assertIn(token, gi)
        tracked = self._tracked()
        self.assertIn("barehands/LICENSE", tracked,
                      "the vendored AGPL copy stays tracked - only its runtime state is ignored")
        self.assertNotIn("barehands/state/state", tracked)

    def test_71_no_config_json_tracked(self):
        tracked = self._tracked()
        self.assertNotIn("config.json", tracked)
        self.assertNotIn("push.bat", tracked)
        self.assertNotIn("push.sh", tracked)

    def test_72_leak_scan_over_every_tracked_file(self):
        pats = [re.compile(p) for p in
                (r"AIza[0-9A-Za-z\-_]{30,}", r"sk-[A-Za-z0-9]{20,}",
                 r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
                 r"xox[baprs]-[A-Za-z0-9\-]{20,}")]
        bad = []
        for rel in self._tracked():
            p = os.path.join(ROOT, rel)
            if os.path.isdir(p) or os.path.getsize(p) > 3_000_000:
                continue
            try:
                body = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for rx in pats:
                m = rx.search(body)
                if m:
                    bad.append(f"{rel}: {m.group(0)[:18]}...")
        self.assertEqual(bad, [], f"possible real keys in tracked files: {bad}")

    def test_73_vendored_barehands_keeps_its_license(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "barehands", "LICENSE")),
                        "AGPL license must ship with the vendored copy")

    def test_74_readme_carries_the_laws(self):
        r = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        low = r.lower()
        self.assertIn("hear.py download", r)
        self.assertIn("never downloads", low)
        self.assertIn("zen", low, "Firefox/Zen ears guidance must be in the README")
        self.assertIn("gemini", low)
        self.assertIn("gemini key", low, "the free-key door must be stated")
        self.assertIn("desktop", low, "the desktop launcher row must exist")

    @unittest.skipUnless(os.path.exists(os.path.join(os.path.dirname(ROOT),
                                                     "JARVIS_GITHUB_HANDOFF.md")),
                         "handoff file lives beside the repo in the publish kit")
    def test_75_handoff_description_stays_in_sync(self):
        h = open(os.path.join(os.path.dirname(ROOT), "JARVIS_GITHUB_HANDOFF.md"),
                 encoding="utf-8").read()
        r = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        m = re.search(r'^"?(A local-first AI butler[^"\n]+)"?$', h, re.M)
        self.assertIsNotNone(m, "the About description must be in the handoff")
        desc = m.group(1).strip()
        self.assertLessEqual(len(desc), 350, "About descriptions should stay short")
        for feature in ("butler", "voice", "hands"):
            self.assertIn(feature.lower(), r.lower(),
                          f"description feature '{feature}' missing from README")

    def test_76_runtime_shelves_are_never_tracked(self):
        tracked = self._tracked()
        for forbidden in ("memory/journal.jsonl", "wav-cache/x.wav", "logs/outbound.log",
                          "viewer/graph-data.js"):
            self.assertNotIn(forbidden, tracked)

if __name__ == "__main__":
    unittest.main(verbosity=2)
