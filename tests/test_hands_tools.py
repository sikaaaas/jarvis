#!/usr/bin/env python3
# tests/test_hands_tools.py - the hands law: the brain's [[TOOL ...]] blocks
# read, write, list and open files ONLY on sir's shelves (home + the Jarvis
# folder); config.json, logs/ and .git/ are private forever; the tool loop
# carries receipts back to the brain and a trace to the viewer; a stumbling
# brain is replaced by the fallback chain (Gemini first) without sir noticing
# anything but a badge. Run:  python tests/test_hands_tools.py
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402

class SafePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis-hands-")
        self._old = server.USER_HOME
        server.USER_HOME = self.tmp

    def tearDown(self):
        server.USER_HOME = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_home_relative_and_tilde_paths_land_inside(self):
        rp = server._safe_path("~/docs/a.md")
        self.assertEqual(rp, os.path.realpath(os.path.join(self.tmp, "docs/a.md")))
        rp2 = server._safe_path("notes/x.md")           # relative -> home shelf
        self.assertTrue(rp2.startswith(os.path.realpath(self.tmp)))
        rp3 = server._safe_path(server.ROOT)             # the jarvis folder itself is legal
        self.assertEqual(rp3, os.path.realpath(server.ROOT))

    def test_outside_shelves_refused(self):
        with self.assertRaises(ValueError):
            server._safe_path("/etc/passwd")
        with self.assertRaises(ValueError):
            server._safe_path("C:\\Windows\\system32\\evil.ini" if os.name == "nt" else "/usr/bin/evil")

    def test_private_shelves_refused(self):
        with self.assertRaises(ValueError):
            server._safe_path(os.path.join(server.ROOT, "config.json"))
        with self.assertRaises(ValueError):
            server._safe_path("~/jarvis/config.json")
        with self.assertRaises(ValueError):
            server._safe_path(os.path.join(server.ROOT, "logs", "outbound.log"))
        with self.assertRaises(ValueError):
            server._safe_path(os.path.join(server.ROOT, ".git", "config"))

    def test_empty_path_refused(self):
        with self.assertRaises(ValueError):
            server._safe_path("")

class ExecuteToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis-hands-")
        self._old = server.USER_HOME
        server.USER_HOME = self.tmp

    def tearDown(self):
        server.USER_HOME = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_read_list_make_round_trip(self):
        r = server.execute_tool("write_file", {"path": "~/projects/mods/mod.java", "content": "public class Mod {}"})
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "projects", "mods", "mod.java")))
        r = server.execute_tool("read_file", {"path": "~/projects/mods/mod.java"})
        self.assertTrue(r["ok"])
        self.assertIn("public class Mod {}", r["data"])
        r = server.execute_tool("make_dir", {"path": "~/projects/mods/sub"})
        self.assertTrue(r["ok"])
        r = server.execute_tool("list_dir", {"path": "~/projects/mods"})
        self.assertTrue(r["ok"])
        self.assertIn("mod.java", r["data"])
        self.assertIn("sub/", r["data"])

    def test_write_over_a_folder_refused(self):
        os.makedirs(os.path.join(self.tmp, "afolder"))
        r = server.execute_tool("write_file", {"path": "~/afolder", "content": "no"})
        self.assertFalse(r["ok"])

    def test_private_read_refused(self):
        r = server.execute_tool("read_file", {"path": os.path.join(server.ROOT, "config.json")})
        self.assertFalse(r["ok"])
        self.assertIn("private", r["data"])

    def test_unknown_tool_named(self):
        r = server.execute_tool("format_c_drive", {})
        self.assertFalse(r["ok"])
        self.assertIn("unknown tool", r["data"])

    def test_write_cap_refused(self):
        r = server.execute_tool("write_file", {"path": "~/big.txt", "content": "x" * (server.TOOL_WRITE_CAP + 1)})
        self.assertFalse(r["ok"])

    def test_open_path_missing_refused(self):
        r = server.execute_tool("open_path", {"path": "~/definitely/not/there"})
        self.assertFalse(r["ok"])

class ParseBlocksTest(unittest.TestCase):
    def test_single_block_parsed_and_stripped(self):
        text = 'Creating it now, sir.\n[[TOOL: {"tool": "write_file", "args": {"path": "~/a.txt", "content": "line1 }} line2"}}]]\nDone.'
        calls = server.parse_tool_blocks(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "write_file")
        self.assertEqual(calls[0]["args"]["content"], "line1 }} line2")
        stripped = server.strip_tool_blocks(text)
        self.assertNotIn("[[TOOL", stripped)
        self.assertIn("Creating it now, sir.", stripped)
        self.assertIn("Done.", stripped)

    def test_multiple_blocks_in_order(self):
        text = ('[[TOOL: {"tool": "list_dir", "args": {"path": "~/"}}]] mid text '
                '[[TOOL: {"tool": "read_file", "args": {"path": "~/b.md"}}]]')
        calls = server.parse_tool_blocks(text)
        self.assertEqual([c["tool"] for c in calls], ["list_dir", "read_file"])

    def test_braces_inside_strings_do_not_fool_the_scanner(self):
        nasty = '[[TOOL: {"tool": "write_file", "args": {"path": "~/c.txt", "content": "json }} [[TOOL: fake]] trap"}}]] after'
        calls = server.parse_tool_blocks(nasty)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"]["content"], 'json }} [[TOOL: fake]] trap')
        self.assertNotIn("[[TOOL", server.strip_tool_blocks(nasty))

    def test_malformed_block_ignored(self):
        self.assertEqual(server.parse_tool_blocks('[[TOOL: not json at all]]'), [])

class ToolLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis-hands-")
        self._old_home = server.USER_HOME
        server.USER_HOME = self.tmp

    def tearDown(self):
        server.USER_HOME = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tool_block_executes_and_loop_continues(self):
        target = os.path.join(self.tmp, "hello.txt")
        replies = [
            f'One moment, sir.\n[[TOOL: {{"tool": "write_file", "args": {{"path": "{target}", "content": "hello sir"}}}}]]',
            "Written, sir - hello.txt is on your shelf.",
        ]
        calls = {"n": 0}

        def fake_named(name, messages):
            out = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return out, name

        with mock.patch.object(server, "_call_brain_named", side_effect=fake_named):
            text, err = server.brain_ask([{"role": "user", "content": "write hello.txt"}])
        self.assertIsNone(err)
        self.assertEqual(text, "Written, sir - hello.txt is on your shelf.")
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello sir")
        self.assertEqual(len(server._REQ.trace), 1)
        self.assertTrue(server._REQ.trace[0]["ok"])

    def test_failed_tool_receipt_reaches_the_brain(self):
        replies = [
            '[[TOOL: {"tool": "read_file", "args": {"path": "/etc/passwd"}}]]',
            "That shelf is private, sir.",
        ]
        seen = {"last": None}

        def fake_named(name, messages):
            if calls["n"] == 1:
                seen["last"] = messages[-1]["content"]
            calls["n"] += 1
            return replies[min(calls["n"] - 1, len(replies) - 1)], name

        calls = {"n": 0}
        with mock.patch.object(server, "_call_brain_named", side_effect=fake_named):
            text, err = server.brain_ask([{"role": "user", "content": "read it"}])
        self.assertIsNone(err)
        self.assertIn("FAILED", seen["last"] or "")
        self.assertFalse(server._REQ.trace[0]["ok"])

    def test_all_silent_returns_the_honest_line(self):
        def fake_named(name, messages):
            raise server._SilenceError("empty completion")

        with mock.patch.object(server, "_call_brain_named", side_effect=fake_named):
            text, err = server.brain_ask([{"role": "user", "content": "hi"}])
        self.assertIn("silence", text)

class FallbackChainTest(unittest.TestCase):
    def setUp(self):
        self._brains = server.CFG.get("brains", {})
        self._state_brain = server.STATE["brain"]

    def tearDown(self):
        server.CFG["brains"] = self._brains
        server.STATE["brain"] = self._state_brain

    def _install(self, brains, boot):
        server.CFG["brains"] = brains
        server.STATE["brain"] = boot

    def test_quota_failure_borrows_gemini_then_answers(self):
        brains = {
            "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                           "model": "openrouter/auto", "api_key": "sk-or-real"},
            "gemini": {"kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                       "model": "gemini-3.8-flash", "api_key": "AIza-real"},
        }
        self._install(brains, "openrouter")

        def fake_named(name, messages):
            if name == "openrouter":
                raise RuntimeError("HTTP 429: Your credit balance is too low.")
            return "Gemini at your service, sir.", name

        with mock.patch.object(server, "_call_brain_named", side_effect=fake_named):
            text, err = server.brain_ask([{"role": "user", "content": "hello"}])
        self.assertIsNone(err)
        self.assertIn("service", text)
        self.assertEqual(server._REQ.borrowed, "gemini")

    def test_placeholder_brains_never_qualify(self):
        brains = {
            "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                           "model": "openrouter/auto", "api_key": "PUT-YOUR-KEY-HERE"},
            "gemini": {"kind": "openai", "base_url": "https://x", "model": "m", "api_key": "AIza-real"},
        }
        self._install(brains, "gemini")
        order = server._fallback_order("gemini")
        self.assertNotIn("openrouter", order)          # placeholder key: off the list

    def test_model_autoheal_tries_sibling_on_404(self):
        brains = {"gemini": {"kind": "openai", "base_url": "https://x", "model": "gemini-gone",
                             "fallback_models": ["gemini-3.8-flash"], "api_key": "AIza-real"}}
        self._install(brains, "gemini")
        models_tried = []

        def fake_builder(cfg, system, messages, floor):
            models_tried.append(cfg["model"])
            if cfg["model"] == "gemini-gone":
                raise RuntimeError("HTTP 404: model not found")
            return "the sibling answers"

        with mock.patch.object(server, "_request_builder_openai", side_effect=fake_builder):
            text, brain = server._call_brain_named("gemini", [{"role": "user", "content": "q"}])
        self.assertEqual(text, "the sibling answers")
        self.assertEqual(brain, "gemini")
        self.assertEqual(models_tried, ["gemini-gone", "gemini-3.8-flash"])

class GeminiEarsTest(unittest.TestCase):
    def setUp(self):
        import hear
        self.tmp = tempfile.mkdtemp(prefix="jarvis-ears-")
        self._gem = dict(hear._gemini)          # hand the bay back as we found it

    def tearDown(self):
        import hear
        hear._gemini.clear()
        hear._gemini.update(self._gem)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gemini_ears_need_a_real_key(self):
        import hear
        hear._gemini["key_for"] = lambda name: None
        self.assertFalse(hear.gemini_ears_available())
        hear._gemini["key_for"] = lambda name: "AIza-real-key-123456"
        self.assertTrue(hear.gemini_ears_available())

    def test_wake_and_interrupt_laws_apply_to_gemini_ears(self):
        import hear
        with mock.patch.object(hear, "_gemini_transcribe", return_value="Jarvis, what is on the menu"):
            out = hear.transcribe_any(b"wav", mode="wake")
        self.assertEqual(out.get("text"), "what is on the menu")
        with mock.patch.object(hear, "_gemini_transcribe", return_value="stop it"):
            out = hear.transcribe_any(b"wav", mode="interrupt")
        self.assertEqual(out.get("text"), "stop")
        with mock.patch.object(hear, "_gemini_transcribe", return_value="nice weather"):
            out = hear.transcribe_any(b"wav", mode="wake")
        self.assertTrue(out.get("discarded"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
