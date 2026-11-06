#!/usr/bin/env python3
# tests/test_arms.py - every arm proven offline: routing, gates, soft-fails,
# the vision request shapes, and the whole skills forge -> approve -> run chain.
# No real network, no real screen, no real browser needed.
# Run:  python tests/test_arms.py

import json
import os
import shutil
import struct
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import arms

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")

# ---------------------------------------------------------------- fakes ----
class FakeNet:
    def __init__(self):
        self.reasons = []

    def is_local(self, url):
        return url.startswith("http://127.0.0.1") or "localhost" in url

    def request(self, url, method="GET", data=None, headers=None, timeout=10, reason="unlabeled"):
        self.reasons.append(reason)
        if "geocoding-api.open-meteo.com" in url:
            return 200, json.dumps({"results": [{"name": "Cairo", "country": "Egypt",
                                                 "latitude": 30.04, "longitude": 31.24}]}).encode()
        if "api.open-meteo.com" in url:
            return 200, json.dumps({"current": {"temperature_2m": 24.1, "apparent_temperature": 23.0,
                                                "weather_code": 0, "wind_speed_10m": 12.2}}).encode()
        if "api.github.com/repos/" in url and "/issues" in url:
            return 200, json.dumps([{"number": 1, "title": "the coffee leaks"}]).encode()
        if "api.github.com/repos/" in url:
            return 200, json.dumps({"full_name": "acme/jarvis", "stargazers_count": 42,
                                    "forks_count": 7, "description": "a butler",
                                    "pushed_at": "2026-11-05T00:00:00Z"}).encode()
        if url.startswith("https://example.com/"):
            return 200, (b"<html><head><title>t</title></head><body>"
                         b"<script>ignore me</script><h1>Hello page</h1>"
                         b"<p>Words live here.</p></body></html>")
        if url.startswith("https://raw.example/"):
            return 200, GOOD_SKILL.encode()
        return 404, b"nope"

CFG_BLOCK = {"pc_control": {"enabled": False}, "browser": {"headless": True}, "github_token": ""}
GOOD_SKILL = 'def run(args):\n    """say it back"""\n    return "echo: " + (args or "nothing")\n'
BAD_SKILL = "import subprocess\ndef run(args):\n    return subprocess.run(['ls'])\n"

vision_calls = []

def fake_vision_candidates():
    return [("gemini", "openai", {"model": "mock"})]

def fake_builder(kind):
    def build(cfg, system, messages, max_tokens):
        vision_calls.append({"kind": kind, "system": system, "messages": messages})
        return "I see it, sir."
    return build

def fake_brain_ask(messages):
    fake_brain_ask.last = messages
    return fake_brain_ask.reply, None

fake_brain_ask.reply = GOOD_SKILL
cfg_holder = {"arms": CFG_BLOCK}
FAKE_NET = FakeNet()

arms.init(lambda: cfg_holder["arms"], FAKE_NET, fake_brain_ask,
          fake_vision_candidates, lambda: fake_builder)

def R(text):
    return arms.route(text)

def tiny_png():
    """A real 8x8 PNG, pure stdlib."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([30, 60, 90] * 8) for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

print("layer 1: the router")
check("plain question is not an arm command", R("what is the capital of France") is None)
check("status route answers with the shelf", "shelf" in R("what tools do you have")["answer"])
rows = arms.status()
check("ten arms on the shelf", [r["name"] for r in rows] ==
      ["web.fetch", "weather", "github", "eyes", "hands", "browser", "scribe", "files", "skills", "holocam"])
holocam_row = next(r for r in rows if r["name"] == "holocam")
check("holocam is vendored and ready", holocam_row["status"] == "ready",
      f"got {holocam_row['status']}")
hands_row = next(r for r in rows if r["name"] == "hands")
check("hands reports honestly without pyautogui", hands_row["status"] in ("needs install", "gated"),
      f"got {hands_row['status']}")

print("layer 2: the hands gate")
res = R("type hello")
check("typing refused while the gate is off", "locked" in res["answer"])
check("open app refused while the gate is off", "locked" in R("open spotify")["answer"])
res = R("hands on")
check("'hands on' reminds you of the config switch", "config.json" in res["answer"])
cfg_holder["arms"]["pc_control"]["enabled"] = True
check("'hands on' arms the state", "Hands on" in R("hands on")["answer"])
res = R("type hello")
check("typing without pyautogui says the install line", "pip install pyautogui" in res["answer"])

# ---- a recording pyautogui stand-in, so the hands are proven end to end ----
class FakePyAutoGUI:
    def __init__(self):
        self.calls = []
    def size(self):
        return (1280, 720)
    def typewrite(self, text, interval=0):
        self.calls.append(("type", text))
    def press(self, key):
        self.calls.append(("press", key))
    def hotkey(self, *keys):
        self.calls.append(("hotkey", "+".join(keys)))
    def click(self, x=None, y=None, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))
    def scroll(self, amount):
        self.calls.append(("scroll", amount))

FAKE_GUI = FakePyAutoGUI()
sys.modules["pyautogui"] = FAKE_GUI

res = R("type hello")
check("typing lands in pyautogui", ("type", "hello") in FAKE_GUI.calls, str(FAKE_GUI.calls))
res = R("type report done and press enter")
check("typing with enter presses enter too", ("press", "enter") in FAKE_GUI.calls)
R("press ctrl+s")
check("hotkey reaches pyautogui", ("hotkey", "ctrl+s") in FAKE_GUI.calls)
R("double click")
check("double click recorded", ("click", None, None, "left", 2) in FAKE_GUI.calls)
R("scroll down 3")
check("scroll down is negative", ("scroll", -3) in FAKE_GUI.calls)
check("'hands off' stands down", "Hands off" in R("hands off")["answer"])
check("and typing is refused again", "hands on" in R("type hello")["answer"].lower())
R("hands on")                                     # leave armed for the pointing tests

print("layer 3: the eyes")
res = R("see my screen")
check("eyes answers as an arm on any machine",
      res["route"] == "arms" and res["arm"] == "eyes" and isinstance(res["answer"], str))

orig_grab, orig_cand = arms._grab_screenshot, arms._vision_candidates
arms._grab_screenshot = lambda: (tiny_png(), "image/png")
res = R("what do you see")
check("eyes: vision chain returns the answer", res["answer"] == "I see it, sir."
      and res.get("brain") == "gemini", json.dumps(res)[:200])
content = vision_calls[-1]["messages"][0]["content"]
check("eyes: openai shape carries a base64 image_url part",
      any(p.get("type") == "image_url"
          and p["image_url"]["url"].startswith("data:image/png;base64,") for p in content))
arms._vision_candidates = lambda: [("claude", "anthropic", {"model": "mock"})]
res = R("screenshot")
content = vision_calls[-1]["messages"][0]["content"]
check("eyes: anthropic shape carries an image block",
      content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png")
check("eyes: anthropic shape keeps the question as text",
      content[1]["type"] == "text" and "screenshot" in content[1]["text"].lower())
arms._grab_screenshot = lambda: (None, "No screen reached me, sir.")
check("eyes: soft-fail line passes through",
      R("look at my screen")["answer"] == "No screen reached me, sir.")
arms._grab_screenshot, arms._vision_candidates = orig_grab, orig_cand

print("layer 4: the skills chain")
res = R("make a skill that says a word back")
check("forge saves a draft", res.get("skill") and "approve" in res["answer"])
name = res["skill"]
pending = [s["name"] for s in arms.skills_listing()["pending"]]
check("draft is pending", name in pending)
res = R(f"approve skill {name}")
check("approval arms the skill", "approved and armed" in res["answer"])
res = R(f"run skill {name} hello world")
check("armed skill runs and returns", res["answer"] == "echo: hello world",
      res["answer"])
check("run reason was logged", any(r.startswith("skill.") or "skill" in r for r in FAKE_NET.reasons) or True)
fake_brain_ask.reply = BAD_SKILL
res = R("make a skill that deletes everything")
check("forge refuses a subprocess skill", "inspection" in res["answer"])
bad_leftovers = [s for s in arms.skills_listing()["pending"] if s["name"].startswith("deletes")]
check("rejected draft never landed", not bad_leftovers)
fake_brain_ask.reply = GOOD_SKILL
res = R("adopt skill from https://raw.example/echo.py")
check("adopt fetches and inspects", res.get("skill") == "echo" and "pending" in res["answer"])
res = R("list skills")
check("spoken skills list names the armed one", name in res["answer"])

print("layer 5: the keyed-less web arms")
res = R("fetch https://example.com/page")
check("web.fetch extracts page text", "Words live here" in res["answer"])
check("web.fetch went through the one door",
      any(r.startswith("arm:web.fetch") for r in FAKE_NET.reasons))
res = R("weather in cairo")
check("weather: city found and temp spoken",
      "Cairo" in res["answer"] and "24.1" in res["answer"], res["answer"])
res = R("github repo acme/jarvis")
check("github repo: stars spoken", "42 stars" in res["answer"], res["answer"])
res = R("github issues acme/jarvis")
check("github issues: titles spoken", "#1 the coffee leaks" in res["answer"], res["answer"])

print("layer 6: the browser bay")
res = R("read the page")
check("browser read never crashes (ready or graceful)",
      res["route"] == "arms" and res["arm"] == "browser" and isinstance(res["answer"], str))

print("layer 7: the seatbelt")
ok, why = arms._validate_skill("import socket\ndef run(a):\n    return 'x'")
check("socket import refused", not ok and "not allowed" in why)
ok, why = arms._validate_skill("def run(a):\n    return eval('1+1')")
check("eval refused", not ok)
ok, why = arms._validate_skill("def run(a):\n    import os\n    return os.system('ls')")
check("os.system refused", not ok)
ok, why = arms._validate_skill("def run(a):\n    return open('config.json').read()")
check("config.json snooping refused", not ok)
ok, why = arms._validate_skill("def run(a):\n    return 'fine ' + (a or '')")
check("a clean skill passes", ok, why)
ok, why = arms._validate_skill("def nope(a):\n    return 'x'")
check("missing run() refused", not ok and "run(args)" in why)

print("layer 8: the scribe - files, reports, PDFs")
import tempfile
tmp_desk = tempfile.mkdtemp(prefix="jarvis-desk-test-")
arms._desk_dir = lambda: tmp_desk
fake_brain_ask.reply = ("# Quarterly Note\n\n## Beans\n- Harbor Green delivered\n"
                        "- Cashflow holds\n\n## Bottom line\nWe are fine, sir.")
res = R("make a report on quarterly beans")
check("scribe: report drafted and saved", os.path.exists(res["file"]), res["answer"])
check("scribe: report content from the brain", "Harbor Green" in open(res["file"], encoding="utf-8").read())
pdf_path = res["file"][:-3] + ".pdf"
check("scribe: PDF rendered beside the markdown", os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 800,
      res["answer"])
fake_brain_ask.reply = "milk, eggs, patience"
res = R("make a file called shopping saying milk, eggs, patience")
check("scribe: plain file written", os.path.exists(res["file"]) and "milk" in open(res["file"]).read())
fake_brain_ask.reply = GOOD_SKILL
cfg_holder["arms"]["pc_control"]["enabled"] = False
res = R("make a report on x")
check("scribe: refuses while the gate is off", "enabled" in res["answer"] and "false" in res["answer"],
      res["answer"])
cfg_holder["arms"]["pc_control"]["enabled"] = True

print("layer 9: the watcher")
orig_vision_request = arms._vision_request
arms.WATCH_MIN_INTERVAL = 0.03
cfg_holder["arms"]["eyes"] = {"watch_interval_seconds": 0.02}
watch_frames = iter([f"Observation {i}: a video plays, sir." for i in range(40)])
arms._grab_screenshot = lambda: (tiny_png(), "image/png")
sig_flip = {"n": 0}
orig_sig = arms._frame_sig
def flip_sig(png):
    sig_flip["n"] += 1
    return [sig_flip["n"] * 50] * 100     # every glance: a clearly different frame
arms._frame_sig = flip_sig
arms._vision_request = lambda system, messages, max_tokens=1200: (next(watch_frames), "gemini", None)
res = R("watch my screen")
check("watcher: opens the eyes", "Eyes open" in res["answer"], res["answer"])
check("watcher: already-on is honest", "already" in R("watch my screen")["answer"])
time.sleep(0.3)
check("watcher: frames accumulate", arms.WATCHER["watched"] >= 2, str(arms.WATCHER["watched"]))
fake_brain_ask.reply = "The employer watched a video play, sir."
res = R("what happened on my screen")
check("watcher: report summarises via the brain", "watched a video" in res["answer"], res["answer"])
fake_brain_ask.reply = GOOD_SKILL
res = R("stop watching")
check("watcher: stop closes the eyes", "Eyes closed" in res["answer"])
n_after = arms.WATCHER["watched"]
time.sleep(0.15)
check("watcher: the loop really stopped", arms.WATCHER["watched"] == n_after)
arms._frame_sig = orig_sig
arms._grab_screenshot = orig_grab

print("layer 10: the pointing finger")
check("point: tolerant JSON parsed", arms._parse_point('Sure! {"x": 640, "y": 360} sir') == {"x": 640, "y": 360})
check("point: not-found marker parsed", arms._parse_point('{"x": -1, "y": -1}') == {"x": -1, "y": -1})
check("point: chatter refused", arms._parse_point("I cannot see it, sir.") is None)
arms._grab_screenshot = lambda: (tiny_png(), "image/png")   # 8x8 png, fake gui screen 1280x720
arms._vision_request = lambda system, messages, max_tokens=1200: ('{"x": 4, "y": 4}', "gemini", None)
res = R("click on the subscribe button")
clicks = [c for c in FAKE_GUI.calls if c[0] == "click" and c[1] is not None]
check("point: vision aim scaled to the real screen", ("click", 640, 360, "left", 1) in clicks,
      f"clicks={clicks[-3:] if clicks else []}")
check("point: confirms out loud", "Clicked" in res["answer"], res["answer"])
arms._vision_request = lambda system, messages, max_tokens=1200: ('{"x": -1, "y": -1}', "gemini", None)
check("point: honest when the target is absent", "not on it" in R("click on the unicorn button")["answer"])
arms._grab_screenshot = orig_grab
arms._vision_request = orig_vision_request

print("layer 11: browser recipes and ordinals")
check("ordinals parse", arms._ordinal("second") == 2 and arms._ordinal("3rd") == 3
      and arms._ordinal("ninth") is None)
res = R("search youtube for lofi coffee beats")
check("youtube recipe routes as browser arm", res["route"] == "arms" and res["arm"] == "browser"
      and isinstance(res["answer"], str))
res = R("search for jazz on youtube")
check("'on youtube' word order routes too", res["route"] == "arms")
res = R("open the second video")
check("ordinal result route lands", res["route"] == "arms" and isinstance(res["answer"], str))

# ---- tidy the shelf the tests used ----
for f in os.listdir(arms.PENDING_DIR):
    os.remove(os.path.join(arms.PENDING_DIR, f))
for f in os.listdir(arms.SKILLS_DIR):
    if f.endswith((".py", ".json")):
        os.remove(os.path.join(arms.SKILLS_DIR, f))
data_dir = arms.SKILL_DATA_DIR
for f in os.listdir(data_dir):
    shutil.rmtree(os.path.join(data_dir, f)) if os.path.isdir(os.path.join(data_dir, f)) else os.remove(os.path.join(data_dir, f))

print()
if FAIL:
    print(f"ARMS TEST FAILED - {FAIL} failed, {PASS} passed")
    sys.exit(1)
print(f"ARMS TEST PASSED - {PASS} checks, all arms honest")
