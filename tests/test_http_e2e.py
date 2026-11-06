#!/usr/bin/env python3
# tests/test_http_e2e.py - THE FULL QA PASS, part 2: the live server.
#
# Boots the REAL stdlib server on loopback (isolated shelves: config, notes,
# home, journal all in a temp dir) and exercises EVERY route from the outside,
# exactly like a browser would:
#
#   GET   /  /setup.html  /graph-data.js  /vendor/*  /health  /status  /graph
#         /brains  /arms  /drill/last-report  - and the refusals:
#         /config.json  /config.template.json  /favicon.ico  /viewer/jarvis.png
#         path traversal
#   POST  /chat (real mock brain over HTTP: small talk, on-topic, THE PC TASK
#         with [[TOOL]] receipts; routes: remember / switch / which-brain /
#         memory),  /remember  /tts (the whole voice ladder)  /hear (the whole
#         ears ladder)  /setup  /setup/test  /desktop-shortcut  /arms/pc
#         /arms/ring  /skills/approve|reject  /drill/report
#
# No real key is ever used; no packet leaves loopback.  Run:
#   python -m unittest tests.test_http_e2e -v

import base64
import io
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import server   # noqa: E402
import memory   # noqa: E402
import hear     # noqa: E402
import voice    # noqa: E402
import net      # noqa: E402
import mock_brain  # the OpenAI-shaped brain from tests/

def _get(path, timeout=8):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def _post(path, obj=None, raw=None, headers=None, timeout=15):
    body = raw if raw is not None else json.dumps(obj or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body,
                                 headers=headers or {"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)

def _jget(path):
    code, body = _get(path)
    return code, json.loads(body.decode())

def _jpost(path, obj=None, raw=None, headers=None, timeout=15):
    code, body, hdrs = _post(path, obj, raw, headers, timeout=timeout)
    try:
        return code, json.loads(body.decode()), hdrs
    except Exception:
        return code, body, hdrs

class TestLiveServer(unittest.TestCase):
    httpd = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jarvis-e2e-")
        cls._old = {k: getattr(server, k) for k in
                    ("CONFIG_PATH", "NOTES_DIR", "USER_HOME", "CFG_SOURCE")}
        cls._old_cfg = json.loads(json.dumps(server.CFG))
        cls._old_state = dict(server.STATE)
        cls._old_journal = (memory.MEM_DIR, memory.JOURNAL)
        cls._old_hear = (hear._backend, hear._ready, hear._weights_missing, dict(hear._gemini))
        cls._old_voice = dict(voice._state)
        # isolated shelves
        server.CONFIG_PATH = os.path.join(cls.tmp, "config.json")   # none written yet
        server.NOTES_DIR = os.path.join(cls.tmp, "notes")
        server.USER_HOME = cls.tmp
        server.CFG_SOURCE = server.CONFIG_PATH + "  (e2e)"
        os.makedirs(server.NOTES_DIR, exist_ok=True)
        memory.MEM_DIR = os.path.join(cls.tmp, "memory")
        memory.JOURNAL = os.path.join(memory.MEM_DIR, "journal.jsonl")
        server.HISTORY.clear()
        # a quiet shelf: no cloud keys anywhere
        for name, b in server.CFG.get("brains", {}).items():
            b["api_key"] = "PUT-YOUR-KEY-HERE"
        # two note nodes so the galaxy and scoring have something to chew
        with server.INDEX_LOCK:
            server.INDEX["nodes"] = [
                {"id": 0, "label": "Roastery suppliers", "group": "notes",
                 "file": "suppliers.md", "excerpt": "Harbor Green Trading supplies the beans.",
                 "_title_toks": {"roastery", "suppliers"},
                 "_body_toks": {"harbor", "green", "trading", "beans", "tuesday"},
                 "_body_text": "harbor green trading supplies the beans on tuesdays.",
                 "_wikilinks": []},
                {"id": 1, "label": "Opening hours", "group": "notes",
                 "file": "hours.md", "excerpt": "Monday to Friday seven to six.",
                 "_title_toks": {"opening", "hours"},
                 "_body_toks": {"monday", "friday", "seven", "six", "saturday"},
                 "_body_text": "monday to friday seven to six, saturday eight to five.",
                 "_wikilinks": []},
            ]
            server.INDEX["links"] = [{"source": 0, "target": 1, "why": "mention"}]
        # the mock brain, live on loopback, wired as the "local" shelf
        cls.brain_httpd = ThreadingHTTPServer(("127.0.0.1", 0), mock_brain.Handler)
        bport = cls.brain_httpd.server_address[1]
        threading.Thread(target=cls.brain_httpd.serve_forever, daemon=True).start()
        server.CFG["brains"]["local"] = {"kind": "openai", "model": "mock-9b",
                                         "base_url": f"http://127.0.0.1:{bport}/v1",
                                         "api_key": "ollama"}
        server.STATE["brain"] = "local"
        # the real server under test
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        global PORT
        PORT = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.brain_httpd.shutdown()
        cls.brain_httpd.server_close()
        for k, v in cls._old.items():
            setattr(server, k, v)
        server.CFG.clear()
        server.CFG.update(cls._old_cfg)
        server.STATE.clear()
        server.STATE.update(cls._old_state)
        memory.MEM_DIR, memory.JOURNAL = cls._old_journal
        hear._backend, hear._ready, hear._weights_missing = cls._old_hear[:3]
        hear._gemini.clear()
        hear._gemini.update(cls._old_hear[3])
        voice._state.clear()
        voice._state.update(cls._old_voice)
        server.HISTORY.clear()
        with server.INDEX_LOCK:
            server.INDEX["nodes"] = []
            server.INDEX["links"] = []
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # every test starts from a no-config, no-key shelf
        if os.path.exists(server.CONFIG_PATH):
            os.unlink(server.CONFIG_PATH)
        server.STATE["brain"] = "local"
        server.STATE["voice_pref"] = "auto"
        for name, b in server.CFG.get("brains", {}).items():
            if name != "local":
                b["api_key"] = "PUT-YOUR-KEY-HERE"
        # the ears start keyless, whatever any other suite left behind
        hear._gemini["key_for"] = lambda n: None
        hear._gemini["down_until"] = 0.0

    # ------------------------------------------------------------------ GET
    def test_10_pages_served(self):
        for path, marker in (("/", "jarvis"), ("/setup.html", "setup"),
                             ("/graph-data.js", "nodes"), ("/health", "ok"),
                             ("/status", "ok"), ("/brains", "brains")):
            code, body = _get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(marker.encode(), body.lower(), path)

    def test_11_vendor_served_and_index_carries_the_chips(self):
        code, body = _get("/vendor/three.min.js")
        self.assertEqual(code, 200)
        self.assertGreater(len(body), 500_000)
        code, body = _get("/vendor/3d-force-graph.min.js")
        self.assertEqual(code, 200)
        code, _ = _get("/")
        self.assertTrue(code)
        code, body = _get("/")
        html = body.decode()
        for chip in ("tglDesk", "tglAgent", "micBtn", "wakeBtn", "tglHolocam", "tglArms"):
            self.assertIn(chip, html, f"viewer chip missing: {chip}")

    def test_12_the_refusals(self):
        """config.json NEVER leaves the machine; traversal never escapes."""
        for path, why in (("/config.json", "the key shelf"),
                          ("/config.template.json", "the template"),
                          ("/favicon.ico", "no favicon route"),
                          ("/viewer/jarvis.png", "static serves viewer pages only"),
                          ("/no-such-page", "unknown"),
                          ("/../server.py", "traversal"),
                          ("/../../etc/passwd", "traversal"),
                          ("/vendor/../server.py", "vendor jail")):
            code, _ = _get(path)
            self.assertIn(code, (400, 403, 404), f"{path} must be refused, got {code} ({why})")

    def test_13_health_shape(self):
        code, h = _jget("/health")
        self.assertEqual(code, 200)
        for key in ("ok", "brain", "brain_kind", "model", "voice", "voice_pref", "voice_detail",
                    "ears", "ears_detail", "notes", "config_source", "setup_needed", "memory",
                    "brain_error", "watching"):
            self.assertIn(key, h)
        self.assertTrue(h["ok"])
        self.assertEqual(h["brain"], "local")

    def test_14_brains_shelf_never_leaks_keys(self):
        code, data = _jget("/brains")
        self.assertEqual(code, 200)
        for b in data["brains"]:
            self.assertNotIn("api_key", b, "no key field may exist in the shelf payload")
            self.assertIsInstance(b["key_set"], bool)

    def test_15_graph_merges_notes_and_workspace(self):
        code, g = _jget("/graph")
        self.assertEqual(code, 200)
        ids = {str(n["id"]) for n in g["nodes"]}
        self.assertIn("0", ids)
        self.assertIn("1", ids)
        self.assertIn("core", ids, "the permission tree (JARVIS CORE) rides along")
        for n in g["nodes"]:
            for k in n:
                self.assertFalse(k.startswith("_"), f"private scoring field {k} leaked")

    def test_16_drill_report_roundtrip(self):
        code, _ = _jget("/drill/last-report")
        self.assertEqual(code, 404, "no report before any question")
        code, body, _ = _jpost("/drill/report", {"entries": [{"name": "x", "size": 1}]})
        self.assertEqual(code, 200)
        code, rep = _jget("/drill/last-report")
        self.assertEqual(code, 200)
        self.assertEqual(rep["entries"][0]["name"], "x")

    # ------------------------------------------------------------- /chat
    def test_20_chat_small_talk_through_the_real_mock_brain(self):
        code, data, _ = _jpost("/chat", {"text": "hello there"})
        self.assertEqual(code, 200)
        self.assertIn("answer", data)
        self.assertTrue(data["answer"].strip())
        self.assertEqual(data["brain"], "local")
        self.assertEqual(data["brain_used"], "local")
        self.assertFalse(data.get("fallback"))
        self.assertIn("seconds", data)

    def test_21_chat_on_topic_answer(self):
        # "opening hours" overlaps the note's TITLE tokens -> on-topic with a source
        code, data, _ = _jpost("/chat", {"text": "what are the opening hours"})
        self.assertEqual(code, 200)
        self.assertIn("seven", data["answer"].lower(), "the mock brain answers the hours")
        self.assertTrue(data["on_topic"])
        self.assertIn(1, data["nodes"], "the Opening-hours note is the source")
        # a question with NO keyword overlap is small talk: the brain still answers
        code, data, _ = _jpost("/chat", {"text": "when do you open the shop"})
        self.assertEqual(code, 200)
        self.assertIn("seven", data["answer"].lower())
        self.assertFalse(data["on_topic"])

    def test_22_chat_empty_body_refused(self):
        code, data, _ = _jpost("/chat", {"text": "  "})
        self.assertEqual(code, 400)

    def test_23_the_pc_task_end_to_end(self):
        """THE AGENT LAW: ask Jarvis to touch the PC -> the brain answers with
        a [[TOOL]] block -> the server EXECUTES it -> receipts go back ->
        the final answer arrives clean, the file exists, the trace shows."""
        calls = {"n": 0}

        def scripted(name, messages):
            calls["n"] += 1
            if calls["n"] == 1:
                return ('At once, sir. [[TOOL: {"tool": "write_file", "args": '
                        '{"path": "~/Desktop/jarvis_e2e_task.txt", "content": "PC task complete"}}]]', name)
            joined = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
            self.assertIn("TOOL RESULTS", joined, "the receipts must reach the brain")
            return ("Done, sir - the file sits on your desktop.", name)

        target = os.path.join(server.USER_HOME, "Desktop", "jarvis_e2e_task.txt")
        if os.path.exists(target):
            os.unlink(target)
        with mock.patch.object(server, "_call_brain_named", side_effect=scripted):
            code, data, _ = _jpost("/chat", {"text": "write a small file on my desktop"},
                                   timeout=20)
        self.assertEqual(code, 200)
        self.assertTrue(data["tools"], "the viewer must see the receipts")
        self.assertTrue(data["tools"][0]["ok"])
        self.assertIn("jarvis_e2e_task", data["tools"][0]["detail"])
        self.assertIn("desktop", data["answer"].lower())
        self.assertNotIn("[[TOOL", data["answer"], "no raw block may reach the glass")
        self.assertTrue(os.path.exists(target), "the file must REALLY be on the desktop")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "PC task complete")
        self.assertGreaterEqual(calls["n"], 2, "the loop must hand receipts back")

    def test_24_remember_route_captures_a_note(self):
        code, data, _ = _jpost("/chat", {"text": "remember that the vault code is 7-3-4"})
        self.assertEqual(code, 200)
        self.assertEqual(data.get("route"), "remember")
        self.assertTrue(data["capture"]["ok"])
        self.assertIn("vault", data["answer"].lower())
        path = os.path.join(server.NOTES_DIR, data["capture"]["file"])
        self.assertTrue(os.path.exists(path), "the note file must exist on disk")
        with open(path, encoding="utf-8") as f:
            self.assertIn("7-3-4", f.read())
        code, g = _jget("/graph")
        self.assertTrue(any("vault" in n["label"].lower() for n in g["nodes"]),
                        "the new star must be in the galaxy")

    def test_25_brain_switch_route_is_honest_about_placeholders(self):
        code, data, _ = _jpost("/chat", {"text": "switch to gemini"})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("badge"))
        self.assertIn("key", data["answer"].lower(), "the placeholder line points at setup")
        self.assertEqual(data["brain"], "local", "no key -> the butler stays local")

    def test_26_which_brain_route(self):
        code, data, _ = _jpost("/chat", {"text": "which brain are you on"})
        self.assertEqual(code, 200)
        self.assertIn("local", data["answer"].lower())
        self.assertIn("mock-9b", data["answer"])

    def test_27_memory_routes(self):
        code, data, _ = _jpost("/chat", {"text": "hello"})
        code, data, _ = _jpost("/chat", {"text": "what do you remember"})
        self.assertEqual(code, 200)
        self.assertIn("exchanges", data["answer"].lower() + data["answer"])
        code, data, _ = _jpost("/chat", {"text": "how long have you been remembering"})
        self.assertEqual(code, 200)
        self.assertIn("journal", data["answer"].lower())

    def test_28_chat_writes_the_journal_on_this_disk(self):
        code, data, _ = _jpost("/chat", {"text": "a question for the journal 42"})
        self.assertTrue(os.path.exists(memory.JOURNAL))
        tail = open(memory.JOURNAL, encoding="utf-8").read().strip().splitlines()[-1]
        self.assertIn("journal 42", tail)

    # ---------------------------------------------------------- /remember
    def test_30_remember_endpoint(self):
        code, data, _ = _jpost("/remember", {"text": "a captured thought by hand"})
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(os.path.exists(os.path.join(server.NOTES_DIR, data["file"])))
        code, data, _ = _jpost("/remember", {"text": ""})
        self.assertEqual(code, 500)
        self.assertFalse(data["ok"])

    # ---------------------------------------------------------- /tts
    def test_40_tts_ladder(self):
        wav = _wav()
        # 1. browser preference -> 503 with the browser voice named
        server.STATE["voice_pref"] = "browser"
        code, data, _ = _jpost("/tts", {"text": "hello"})
        self.assertEqual(code, 503)
        self.assertEqual(data["voice"], "browser")
        # 2. local voice answers -> 200 audio/wav with the voice tag
        server.STATE["voice_pref"] = "auto"
        with mock.patch.object(voice, "speak", return_value=wav), \
             mock.patch.object(voice, "available", return_value=True):
            code, body, hdrs = _post("/tts", {"text": "hello"})
            self.assertEqual(code, 200)
            self.assertEqual(hdrs.get("Content-Type"), "audio/wav")
            self.assertIn("local", hdrs.get("X-Jarvis-Voice", ""))
            self.assertEqual(body, wav)
        # 3. local dark, cloud tier answers -> 200 with the cloud tag
        with mock.patch.object(voice, "speak", return_value=None), \
             mock.patch.object(voice, "available", return_value=False), \
             mock.patch.object(voice, "speak_cloud", return_value=(wav, "cloud:gemini:Charon")):
            code, body, hdrs = _post("/tts", {"text": "hello"})
            self.assertEqual(code, 200)
            self.assertEqual(hdrs.get("X-Jarvis-Voice"), "cloud:gemini:Charon")
        # 4. nothing answers -> honest 503 to the browser stand-in
        with mock.patch.object(voice, "speak", return_value=None), \
             mock.patch.object(voice, "available", return_value=False), \
             mock.patch.object(voice, "speak_cloud", return_value=None):
            code, data, _ = _jpost("/tts", {"text": "hello"})
            self.assertEqual(code, 503)
            self.assertEqual(data["voice"], "browser")
        # 5. the cap and the empty law
        code, data, _ = _jpost("/tts", {"text": "x" * (server.TTS_CHUNK_CAP + 1)})
        self.assertEqual(code, 413)
        code, data, _ = _jpost("/tts", {"text": ""})
        self.assertEqual(code, 400)

    # ---------------------------------------------------------- /hear
    def test_50_hear_ladder(self):
        wav = _wav()
        # 1. no audio -> 400
        code, data, _ = _jpost("/hear", raw=b"", headers={"Content-Type": "audio/wav"})
        self.assertEqual(code, 400)
        # 2. no ears at all -> honest 501 naming every fix
        code, data, _ = _jpost("/hear", raw=wav, headers={"Content-Type": "audio/wav"})
        self.assertEqual(code, 501)
        msg = data["error"]
        self.assertIn("AGENT", msg)
        self.assertIn("Gemini", msg)
        self.assertIn("hear.py download", msg)
        self.assertEqual(data["fallback"], "browser")
        # 3. a (fake) gemini key present -> the cloud ears get the knock
        hear._gemini["key_for"] = lambda n: "AIza-fake"
        with mock.patch.object(hear, "_gemini_transcribe",
                               side_effect=RuntimeError("the Gemini ears refused: bad key")):
            code, data, _ = _jpost("/hear", raw=wav, headers={"Content-Type": "audio/wav"})
            self.assertEqual(code, 503)
            self.assertEqual(data["fallback"], "browser")
        # 4. the ears hear -> 200 with text
        with mock.patch.object(hear, "_gemini_transcribe", return_value="open the pod bay"):
            code, data, _ = _jpost("/hear", raw=wav, headers={"Content-Type": "audio/wav"})
            self.assertEqual(code, 200)
            self.assertEqual(data["text"], "open the pod bay")
            self.assertEqual(data["via"], "gemini")
        # 5. local whisper hears first when ready
        with mock.patch.object(hear, "available", return_value=True), \
             mock.patch.object(hear, "transcribe", return_value={"text": "whisper heard", "seconds": 0.1}):
            code, data, _ = _jpost("/hear", raw=wav, headers={"Content-Type": "audio/wav"})
            self.assertEqual(code, 200)
            self.assertEqual(data["text"], "whisper heard")
        hear._gemini["key_for"] = lambda n: None

    # ------------------------------------------------- /setup, /setup/test
    def test_60_setup_writes_config_and_never_echoes_the_key(self):
        # test first: the fake vendor refuses the bad key, accepts the good one.
        # The vendor sits on loopback, but it plays a CLOUD brain - so is_local
        # is taught to treat this one port as off-machine (the real cloud law
        # for a 127.0.0.1 base is already proven in test_15/local shelf laws).
        vendor = _FakeVendor()
        port = vendor.start()
        server.CFG["brains"]["openrouter"]["base_url"] = f"http://127.0.0.1:{port}"
        real_is_local = net.is_local
        net.is_local = lambda url: False if f":{port}" in url else real_is_local(url)
        self.addCleanup(lambda: setattr(net, "is_local", real_is_local))
        code, res, _ = _jpost("/setup/test", {"brain": "openrouter", "api_key": "bad-key"})
        self.assertFalse(res["ok"], "the fake vendor refuses bad-key with a quote")
        self.assertIn("refused", res.get("detail", "").lower())
        code, res, _ = _jpost("/setup/test", {"brain": "openrouter", "api_key": "good-key-12345"})
        self.assertTrue(res["ok"])
        # then the real setup door
        code, res, _ = _jpost("/setup", {"brain": "openrouter", "api_key": "good-key-12345"})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertTrue(res["key_set"])
        self.assertTrue(os.path.exists(server.CONFIG_PATH), "config.json written by the server")
        with open(server.CONFIG_PATH, encoding="utf-8") as f:
            on_disk = f.read()
        self.assertIn("good-key-12345", on_disk, "the key lives in the local config only")
        # ...and NEVER comes back out through any route
        for path in ("/brains", "/health", "/"):
            code, body = _get(path)
            self.assertNotIn("good-key-12345", body.decode(), f"the key leaked via {path}")
        code, data = _jget("/brains")
        shelf = {b["name"]: b for b in data["brains"]}
        self.assertTrue(shelf["openrouter"]["key_set"])
        self.assertEqual(data["current"], "openrouter", "the setup door switches the brain")

    # ------------------------------------------------ desktop-shortcut
    @unittest.skipUnless(sys.platform.startswith("linux"), "writes the Linux launcher")
    def test_70_desktop_shortcut_writes_the_launcher(self):
        desk = os.path.join(server.USER_HOME, "Desktop")
        os.makedirs(desk, exist_ok=True)
        code, res, _ = _jpost("/desktop-shortcut", {})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "linux")
        self.assertTrue(os.path.exists(res["path"]))
        with open(res["path"], encoding="utf-8") as f:
            body = f.read()
        self.assertIn("[Desktop Entry]", body)
        self.assertIn("start.sh", body)

    # ------------------------------------------------------- the armory
    def test_80_arms_routes(self):
        code, data = _jget("/arms")
        self.assertEqual(code, 200)
        self.assertIn("arms", data)
        self.assertIn("skills", data)
        # the PC gate writes the config
        code, res, _ = _jpost("/arms/pc", {"enabled": True})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        with open(server.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertTrue(cfg["arms"]["pc_control"]["enabled"],
                        "the hands gate must land in the local config")
        code, res, _ = _jpost("/arms/pc", {"enabled": False})
        with open(server.CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertFalse(cfg["arms"]["pc_control"]["enabled"])
        # the ring mirror answers even when the holocam is dark
        code, res, _ = _jpost("/arms/ring", {"state": "thinking"})
        self.assertEqual(code, 200)
        # skill approval: a bad name is refused, a shape-checking pass runs
        code, res, _ = _jpost("/skills/approve", {"name": "bad name!"})
        self.assertEqual(code, 400)
        code, res, _ = _jpost("/skills/approve", {"name": "no-such-skill"})
        self.assertEqual(code, 200)
        code, res, _ = _jpost("/skills/reject", {"name": "no-such-skill"})
        self.assertEqual(code, 200)

def _wav(rate=16000, ms=200):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * ms / 1000))
    return buf.getvalue()

class _FakeVendor:
    """A cloud brain on loopback: bad-key -> 400 with a vendor quote,
    good-key -> 200 model list. Mirrors tests/test_setup.py's fake."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler

        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                code = 400 if key == "bad-key" else 200
                body = json.dumps({"data": [{"id": "mock-model"}]} if code == 200
                                  else {"error": {"message": "Please pass a valid API key"}}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.cls = H
        self.httpd = None

    def start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.cls)
        port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return port

PORT = 0

if __name__ == "__main__":
    unittest.main(verbosity=2)
