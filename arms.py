#!/usr/bin/env python3
# ============================================================================
# arms.py - the tool armory: the butler's hands.
#
# The PDF built Jarvis a mind (brains), a voice (VoiceStudio) and ears
# (Whisper). The armory gives him HANDS - on command only, each one a soft
# bay that boots fine when its dependency is missing:
#
#   web.fetch   read a web page's text                (stdlib, always on)
#   weather     weather anywhere, open-meteo, no key  (stdlib, always on)
#   github      read a repo / its issues              (PAT optional)
#   eyes        see your screen (screenshot -> your cloud brain's vision)
#   hands       type / click / scroll / press / open apps   (pyautogui, gated)
#   browser     drive a real browser: open, click, type, read (playwright)
#   skills      forge / approve / run little python skills on request
#   holocam     barehands: the hand-tracked air-board - move cards on camera
#
#   The holocam is special: it is a vendored copy of github.com/jaredrhod/barehands
#   (AGPL-3.0, kept intact in ./barehands). Its server is stdlib Python on
#   127.0.0.1:8794 and its page draws notes, images and 3D models as glass
#   cards over your camera feed; you pinch, throw and claw them with bare
#   hands. Say "barehands" or "holocam" and Jarvis boots it, opens the board
#   in your browser, and mirrors his state onto the ring. Loopback only - it
#   never leaves the machine, so it does not route through net.py (that gate
#   is for outbound).
#
# THE LAW: every outbound byte still goes through net.request - the same one
# door as the brains. Screenshots leave the machine ONLY to the vision brain
# you chose, same as your questions do. Hands do nothing unless the config
# gate is on AND you asked. Every action is logged to logs/arms.log.
# ============================================================================
import ast
import base64
import importlib.util
import io
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(ROOT, "skills")
PENDING_DIR = os.path.join(SKILLS_DIR, "_pending")
SKILL_DATA_DIR = os.path.join(SKILLS_DIR, "_data")
ARMS_LOG = os.path.join(ROOT, "logs", "arms.log")
RUN_TIMEOUT = 20          # seconds a skill gets before the butler gives up on it

net = None                # injected by init()
_brain_ask = None         # injected: server's brain_ask(messages) -> (text, err)
_vision_candidates = None # injected: fn() -> [(name, kind, cfg)] best-first
_arms_cfg = None          # injected: fn() -> the arms block of config.json
_builder_getter = None    # injected: fn(kind) -> the HTTP request builder

STATE = {"hands_unlocked": False,   # momentary stop: "hands off" until "hands on"
         "holocam_running": False}  # set once the holocam has been started and seen alive

_last_answer = ""        # the previous reply, for "put that on the board"


def set_last_answer(text):
    """server._post_chat calls this after every reply, so 'put that on the
    board' always has something concrete to put on the glass."""
    global _last_answer
    _last_answer = str(text or "")[:1500]


def init(cfg_getter, net_module, brain_ask, vision_candidates, builder_getter):
    global net, _brain_ask, _vision_candidates, _arms_cfg, _builder_getter
    net = net_module
    _brain_ask = brain_ask
    _vision_candidates = vision_candidates
    _arms_cfg = cfg_getter
    _builder_getter = builder_getter
    os.makedirs(PENDING_DIR, exist_ok=True)
    os.makedirs(SKILL_DATA_DIR, exist_ok=True)


def _log(action, detail=""):
    os.makedirs(os.path.dirname(ARMS_LOG), exist_ok=True)
    with open(ARMS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(time.time(), 3), "action": action,
                            "detail": detail[:300]}, ensure_ascii=False) + "\n")


def _cfg():
    """The arms block of config.json, with sane defaults."""
    return (_arms_cfg() or {})


def pc_enabled():
    return bool(_cfg().get("pc_control", {}).get("enabled")) and STATE["hands_unlocked"]


# ---------------------------------------------------------------------------
# Status - what the armory panel and "what tools do you have" both show
# ---------------------------------------------------------------------------
def _dep_ok(module):
    try:
        __import__(module)
        return True
    except Exception:                          # noqa: BLE001
        return False


def status():
    arms_cfg = _cfg()
    pc = arms_cfg.get("pc_control", {})
    has_eye = _dep_ok("mss") or _dep_ok("PIL")
    has_hands = _dep_ok("pyautogui")
    eye_row = ({"name": "eyes", "desc": "see your screen (screenshot -> vision brain)",
                "status": "ready" if (_vision_candidates() and has_eye) else
                          ("needs key" if has_eye else "needs install"),
                "how": ("" if (_vision_candidates() and has_eye) else
                        ("pip install mss pillow" if not has_eye
                         else "paste a Gemini/Claude key in config.json"))})
    rows = [
        {"name": "web.fetch", "desc": "read a web page's text",
         "status": "ready", "how": ""},
        {"name": "weather", "desc": "weather anywhere (open-meteo, no key)",
         "status": "ready", "how": ""},
        {"name": "github", "desc": "read a repo / its issues (your token optional)",
         "status": "ready", "how": ""},
        eye_row,
        {"name": "hands", "desc": "type / click / scroll / open apps, on command",
         "status": ("ready" if (has_hands and pc.get("enabled")) else
                    ("gated" if has_hands else "needs install")),
         "how": ("" if (has_hands and pc.get("enabled")) else
                 ("turn pc_control.enabled on in config.json" if has_hands
                  else "pip install pyautogui mss pillow"))},
        {"name": "browser", "desc": "drive a real browser: search youtube, open results, read",
         "status": "ready" if _dep_ok("playwright") else "needs install",
         "how": "" if _dep_ok("playwright") else "pip install playwright && playwright install chromium"},
        {"name": "scribe", "desc": "files, PDFs and reports written to your desktop",
         "status": "ready" if _dep_ok("reportlab") else "ready (md only)",
         "how": "" if _dep_ok("reportlab") else "pip install reportlab for PDFs"},
        {"name": "skills", "desc": "forge, approve and run small python skills",
         "status": "ready", "how": ""},
        {"name": "holocam", "desc": "barehands: move cards with your bare hands on camera (localhost:8794)",
         "status": ("ready" if os.path.isfile(os.path.join(ROOT, "barehands", "server.py"))
                    else "needs setup"),
         "how": ("" if os.path.isfile(os.path.join(ROOT, "barehands", "server.py"))
                 else "the barehands folder is missing - re-clone the repo")},
    ]
    return rows


def summary_spoken():
    ready = [r["name"] for r in status() if r["status"] == "ready"]
    gated = [r["name"] for r in status() if r["status"] in ("gated", "needs install", "needs key")]
    line = "On the shelf, sir: " + ", ".join(ready) + "."
    if gated:
        line += " Not yet armed: " + ", ".join(gated) + " - the armory panel shows what each one needs."
    return line


# ---------------------------------------------------------------------------
# THE ROUTER - server.handle_routes calls this after the voice routes.
# Returns a response dict, or None if this was not an arm command.
# ---------------------------------------------------------------------------
def route(text):
    try:
        return _route(text.strip())
    except Exception as e:                     # noqa: BLE001 - an arm never crashes the butler
        _log("error", f"{type(e).__name__}: {e}")
        return {"answer": f"The armory jammed, sir - {type(e).__name__}: {e}. "
                          f"Everything else still works.", "nodes": [], "on_topic": False,
                "route": "arms", "arm": "error"}


def _route(t):
    low = t.lower()
    if re.fullmatch(r"what (tools|arms) do you have|list (your )?arms|open the armory", low):
        return {"answer": summary_spoken(), "nodes": [], "on_topic": False,
                "route": "arms", "arm": "status"}
    if re.fullmatch(r"hands off|stand down (your )?hands", low):
        STATE["hands_unlocked"] = False
        _log("hands off")
        return {"answer": "Hands off, sir. I shall keep talking, but touch nothing until you say hands on.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
    if re.fullmatch(r"hands on", low):
        if not _cfg().get("pc_control", {}).get("enabled"):
            return {"answer": "The hands are locked in config.json, sir - pc_control.enabled is false. "
                              "Flip it and I shall take the gloves off.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
        STATE["hands_unlocked"] = True
        _log("hands on")
        return {"answer": "Hands on, sir. Type, click, scroll, open - the moment you ask for it.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
    # the holocam (barehands). Loopback-only, so no gate: the worst it can do is
    # open a browser tab and start a localhost server you asked for.
    if re.fullmatch(r"(?:open|start|launch|show|raise)(?: the| your)? (?:bare ?hands|holo ?cam)"
                    r"(?: mode| board| cam| camera)?|(?:bare ?hands|holo ?cam)", low):
        return holocam_start()
    if re.fullmatch(r"(?:close|stop|shut|lower)(?: the| your)? (?:bare ?hands|holo ?cam)", low):
        return holocam_stop()
    if re.fullmatch(r"(?:is )?(?:the )?(?:bare ?hands|holo ?cam)(?: up| on| running| status)\??"
                    r"|(?:bare ?hands|holo ?cam) status", low):
        return holocam_status()
    m = re.fullmatch(r"(?:put|throw|place) (?:that|this|it)(?: answer)? (?:up )?on (?:the )?(?:board|holo ?cam)"
                     r"|(?:holo ?cam|bare ?hands) (?:that|this|it)(?: answer)?", low)
    if m:
        return holocam_last_answer()
    if re.search(r"^(?:see|look at|check)\s+(?:my\s+)?screen|^what do you see|^screenshot|^take a look", low):
        return eyes(t)
    m = re.fullmatch(r"type (.+)", t, re.I | re.S)
    if m:
        return hands_type(m.group(1))
    m = re.fullmatch(r"press ((?:ctrl|alt|shift|cmd|win)\+[a-z0-9]+|[a-z0-9]+)", low)
    if m:
        return hands_press(m.group(1))
    m = re.fullmatch(r"(double )?click(?: (left|right))?", low)
    if m:
        return hands_click(bool(m.group(1)), m.group(2) or "left")
    m = re.fullmatch(r"scroll (up|down)(?: (\d+))?", low)
    if m:
        return hands_scroll(m.group(1), int(m.group(2) or 5))
    m = re.fullmatch(r"search (?:youtube|on youtube) for (.+)", t, re.I)
    if m:
        return browser_search_site("youtube", m.group(1))
    m = re.fullmatch(r"search for (.+?) on (youtube|google)", t, re.I)
    if m:
        return browser_search_site(m.group(2).lower(), m.group(1))
    m = re.fullmatch(r"(?:open|click|play) (?:the )?(first|second|third|fourth|fifth|sixth|seventh|\d+(?:st|nd|rd|th)) (?:video|result)", t, re.I)
    if m:
        return browser_open_result(m.group(1))
    m = re.fullmatch(r"(?:make|write) (?:me )?a (pdf|report) (?:about|on|for) (.+)", t, re.I)
    if m:
        return make_report(m.group(2).strip(), "report" if m.group(1).lower() == "report" else "report")
    m = re.fullmatch(r"(?:make|create|write) (?:me )?a (?:file|note) (?:called|named) ([\w \-]+?)(?: (?:saying|with|containing|about) (.+))?$", t, re.I | re.S)
    if m:
        return make_file(m.group(1).strip(), m.group(2) or "")
    m = re.fullmatch(r"click on (?:the )?(.+)", t, re.I)
    if m:
        return point_click(m.group(1).strip())
    if re.fullmatch(r"watch (?:my |the )?screen|keep an eye on (?:my )?screen|start watching", low):
        return watch_start()
    if re.fullmatch(r"stop watching|close your eyes", low):
        return watch_stop()
    if re.fullmatch(r"what happened (?:on my screen|while you watched)?|what did i do", low):
        return watch_report()
    m = re.fullmatch(r"search (?:google |the web )?for (.+)", t, re.I)
    if m:
        return browser_open("https://www.google.com/search?q=" + urllib.parse.quote(m.group(1)))
    m = re.fullmatch(r"read (?:the )?page", low)
    if m:
        return browser_read()
    m = re.fullmatch(r"open (.+)", t, re.I)
    if m:
        return open_target(m.group(1).strip())
    m = re.fullmatch(r"weather (?:in |at |for )?(.+)", t, re.I)
    if m:
        return weather(m.group(1).strip())
    m = re.fullmatch(r"(?:fetch|read|open) (https?://\S+)", t, re.I)
    if m:
        return web_fetch(m.group(1))
    m = re.fullmatch(r"github (repo|issues) ([\w.\-]+)/([\w.\-]+)", low)
    if m:
        return github(m.group(1), m.group(2), m.group(3))
    m = re.match(r"(?:make|forge) (?:me )?a skill(?: that|:| which)? (.+)|learn (?:a )?skill: ?(.+)", t, re.I)
    if m:
        return forge(m.group(1) or m.group(2))
    m = re.fullmatch(r"adopt skill (?:from )?(https?://\S+)", t, re.I)
    if m:
        return adopt(m.group(1))
    m = re.fullmatch(r"approve skill ([\w-]+)", low)
    if m:
        return approve(m.group(1))
    m = re.fullmatch(r"reject skill ([\w-]+)", low)
    if m:
        return reject(m.group(1))
    if re.fullmatch(r"list skills|what skills do you have", low):
        return {"answer": skills_spoken(), "nodes": [], "on_topic": False,
                "route": "arms", "arm": "skills"}
    m = re.fullmatch(r"run skill ([\w-]+)(?: (?:with |on )?(.+))?", t, re.I | re.S)
    if m:
        return run_skill(m.group(1), (m.group(2) or "").strip())
    return None


# ---------------------------------------------------------------------------
# web.fetch - read a page's text. stdlib only.
# ---------------------------------------------------------------------------
def web_fetch(url):
    if not re.match(r"^https?://", url):
        url = "https://" + url
    status, raw = net.request(url, timeout=15, reason="arm:web.fetch")
    if status != 200:
        return {"answer": f"The page refused me, sir - HTTP {status}.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "web.fetch"}
    text = _html_text(raw.decode("utf-8", "replace"))
    snippet = text[:600].strip()
    _log("web.fetch", url)
    return {"answer": f"I have read {url}, sir. It opens: \"{snippet}...\"" if snippet
                      else f"I fetched {url}, sir, but it is all markup and no words.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "web.fetch", "url": url}


def _html_text(html):
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;?", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;|&#60;", "<", html)
    html = re.sub(r"&gt;|&#62;", ">", html)
    return re.sub(r"\s+", " ", html).strip()


# ---------------------------------------------------------------------------
# weather - open-meteo, free, keyless. Geocoding by the same service.
# ---------------------------------------------------------------------------
def weather(city):
    q = urllib.parse.quote(city)
    s, raw = net.request(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1",
                         timeout=10, reason="arm:weather:geocode")
    if s != 200:
        return {"answer": f"The map service is not answering, sir (HTTP {s}).", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "weather"}
    hits = (json.loads(raw.decode("utf-8", "replace")).get("results") or [])
    if not hits:
        return {"answer": f"I find no {city} on the map, sir - is it spelled as you said?",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "weather"}
    g = hits[0]
    s, raw = net.request(
        f"https://api.open-meteo.com/v1/forecast?latitude={g['latitude']}&longitude={g['longitude']}"
        f"&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m&timezone=auto",
        timeout=10, reason="arm:weather:forecast")
    if s != 200:
        return {"answer": f"The sky service is not answering, sir (HTTP {s}).", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "weather"}
    cur = json.loads(raw.decode("utf-8", "replace")).get("current", {})
    desc = WEATHER_CODES.get(cur.get("weather_code"), "unclear skies")
    _log("weather", g.get("name", city))
    return {"answer": (f"{g.get('name')}, {g.get('country','')} right now: {cur.get('temperature_2m')}C, "
                       f"{desc}, feels like {cur.get('apparent_temperature')}C, "
                       f"wind {cur.get('wind_speed_10m')} km/h."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "weather"}


WEATHER_CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog",
                 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
                 61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
                 75: "heavy snow", 80: "rain showers", 81: "heavy showers", 82: "violent showers",
                 95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a violent thunderstorm"}


# ---------------------------------------------------------------------------
# github - read a repo / its issues. Your PAT is optional (rate limits only).
# ---------------------------------------------------------------------------
def github(kind, owner, repo):
    headers = {"User-Agent": "jarvis-arms"}
    tok = _cfg().get("github_token")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    if kind == "repo":
        s, raw = net.request(f"https://api.github.com/repos/{owner}/{repo}",
                             headers=headers, timeout=15, reason="arm:github")
        if s != 200:
            return {"answer": f"GitHub said {s}, sir - the repo may be private or misspelled.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": "github"}
        d = json.loads(raw.decode("utf-8", "replace"))
        _log("github.repo", f"{owner}/{repo}")
        return {"answer": (f"{d.get('full_name')}: {d.get('stars', d.get('stargazers_count'))} stars, "
                           f"{d.get('forks', d.get('forks_count'))} forks. "
                           f"{(d.get('description') or 'No description.')}"
                           f" Last push {str(d.get('pushed_at',''))[:10]}."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "github"}
    s, raw = net.request(f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&per_page=5",
                         headers=headers, timeout=15, reason="arm:github")
    if s != 200:
        return {"answer": f"GitHub said {s}, sir.", "nodes": [], "on_topic": False,
                "route": "arms", "arm": "github"}
    issues = json.loads(raw.decode("utf-8", "replace"))
    if not issues:
        return {"answer": f"No open issues on {owner}/{repo}, sir - suspiciously tidy.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "github"}
    lines = "; ".join(f"#{i.get('number')} {i.get('title')}" for i in issues[:5])
    _log("github.issues", f"{owner}/{repo}")
    return {"answer": f"Top open issues on {owner}/{repo}, sir: {lines}.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "github"}


# ---------------------------------------------------------------------------
# eyes - screenshot -> the vision brain you chose
# ---------------------------------------------------------------------------
def _grab_screenshot():
    """Returns (image_bytes, media_type) or (None, reason_line)."""
    try:                                              # mss: fastest, all platforms
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            from PIL import Image
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return _shrink(img)
    except ImportError:
        pass
    except Exception:                                # noqa: BLE001 - headless / locked screen
        pass
    try:                                              # PIL's own grabber (Windows / mac)
        from PIL import ImageGrab
        img = ImageGrab.grab()
        return _shrink(img)
    except Exception:                                # noqa: BLE001
        pass
    return None, ("No screen reached me, sir. Install the capture eye with "
                  "'pip install mss pillow' and try again.")


def _shrink(img):
    """Downscale to 1280 wide and encode JPEG - kind to upload limits."""
    from PIL import Image
    if img.width > 1280:
        img = img.resize((1280, int(img.height * 1280 / img.width)))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue(), "image/jpeg"


VISION_SYSTEM = ("You are Jarvis, a dry, impeccably polite British butler with a razor wit. "
                 "You are being shown a screenshot of your employer's screen. "
                 "Answer in one or two sentences, address them as 'sir' once, and describe "
                 "what you see or answer the question about it. If the screen is unreadable, say so.")


def _vision_request(system, messages, max_tokens=1200):
    """One vision turn through the candidate brains, best-first.
    Returns (text, brain_name, last_err)."""
    last_err = None
    for name, kind, cfg in (_vision_candidates() or []):
        try:
            text = _builder_getter()(kind)(cfg, system, messages, max_tokens)
            if text:
                return text, name, None
        except Exception as e:                       # noqa: BLE001 - try the next pair of eyes
            last_err = e
    return None, None, last_err


def _vision_message(question, img_bytes, media, kind):
    """Wrap an image + question in the right shape for the brain kind."""
    b64 = base64.b64encode(img_bytes).decode()
    if kind == "anthropic":
        return [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
            {"type": "text", "text": question}]}]
    return [{"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": f"data:{media};base64,{b64}"}}]}]


def eyes(question):
    png, media = _grab_screenshot()
    if png is None:
        return {"answer": media, "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    cand = _vision_candidates()
    if not cand:
        return {"answer": ("I have the screenshot, sir, but no brain that can look at it - "
                           "paste a Gemini or Claude key into config.json and I shall see."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    q = question if re.search(r"what do you see|screenshot|take a look|look at|see my screen|check my screen", question, re.I) \
        else f"Look at this screenshot. {question}"
    last_err = None
    for name, kind, cfg in cand:
        messages = _vision_message(q, png, media, kind)
        text, _name, err = _vision_request(VISION_SYSTEM, messages)
        if text:
            _log("eyes", f"via {name}")
            return {"answer": text, "nodes": [], "on_topic": False,
                    "route": "arms", "arm": "eyes", "brain": name}
        last_err = err
    return {"answer": (f"None of the vision brains would look, sir - last fault: {last_err}. "
                       f"The screenshot never left your machine."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}


# ---------------------------------------------------------------------------
# hands - pyautogui, gated by config + "hands on"
# ---------------------------------------------------------------------------
def _hands_guard(arm):
    if not _cfg().get("pc_control", {}).get("enabled"):
        return {"answer": "The hands are locked, sir - pc_control.enabled is false in config.json. "
                          "That switch is yours to flip, in your editor.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": arm}
    if not STATE["hands_unlocked"]:
        if _cfg().get("pc_control", {}).get("auto_hands_on"):
            STATE["hands_unlocked"] = True
        else:
            return {"answer": "Say 'hands on' first, sir - I do not touch anything until you arm me.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": arm}
    try:
        import pyautogui                          # noqa: F401
    except ImportError:
        return {"answer": "The hands are not installed, sir: 'pip install pyautogui' and arm me again.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": arm}
    return None


def hands_type(text):
    guard = _hands_guard("hands")
    if guard:
        return guard
    press_enter = False
    m = re.fullmatch(r"(.*?)\s+and (?:then )?press enter", text, re.I)
    if m:
        text, press_enter = m.group(1), True
    import pyautogui
    pyautogui.typewrite(text, interval=0.02)
    if press_enter:
        pyautogui.press("enter")
    _log("hands.type", text)
    return {"answer": f"Typed, sir{' and pressed enter' if press_enter else ''}.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}


def hands_press(key):
    guard = _hands_guard("hands")
    if guard:
        return guard
    import pyautogui
    if "+" in key:
        pyautogui.hotkey(*key.split("+"))
    else:
        pyautogui.press(key)
    _log("hands.press", key)
    return {"answer": f"Pressed {key}, sir.", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "hands"}


def hands_click(double, button):
    guard = _hands_guard("hands")
    if guard:
        return guard
    import pyautogui
    pyautogui.click(button=button, clicks=2 if double else 1)
    _log("hands.click", f"{'double ' if double else ''}{button}")
    return {"answer": f"{'Double-clicked' if double else 'Clicked'}, sir.", "nodes": [],
            "on_topic": False, "route": "arms", "arm": "hands"}


def hands_scroll(direction, amount):
    guard = _hands_guard("hands")
    if guard:
        return guard
    import pyautogui
    pyautogui.scroll(amount if direction == "up" else -amount)
    _log("hands.scroll", direction)
    return {"answer": f"Scrolled {direction}, sir.", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "hands"}


def open_target(target):
    """'open youtube.com' -> browser; 'open spotify' -> the OS launcher."""
    if re.match(r"^(https?://|[\w-]+\.[a-z]{2,})(/\S*)?$", target, re.I) and " " not in target:
        url = target if target.startswith("http") else "https://" + target
        return browser_open(url)
    return hands_open_app(target)


def hands_open_app(target):
    guard = _hands_guard("hands")
    if guard:
        return guard
    try:
        if os.name == "nt":
            os.startfile(target)                  # noqa: S606 - the user asked, by name
        elif os.uname().sysname == "Darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as e:                      # noqa: BLE001
        return {"answer": f"'{target}' would not open, sir - {e}.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "hands"}
    _log("hands.open", target)
    return {"answer": f"Opening {target}, sir.", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "hands"}


# ---------------------------------------------------------------------------
# browser - one persistent playwright page, driven from ONE worker thread
# (the sync API refuses to be poked from several threads; a queue keeps it law-abiding)
# ---------------------------------------------------------------------------
_browser = {"q": None, "err": None, "started": False}
_browser_lock = threading.Lock()


def _browser_worker(cfg):
    q = queue.Queue()
    _browser["q"] = q
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=bool(cfg.get("headless", False)))
        page = browser.new_page()
        while True:
            action, arg, out = q.get()
            try:
                if action == "open":
                    page.goto(arg, timeout=30000, wait_until="domcontentloaded")
                    out["ok"] = True
                elif action == "click":
                    page.get_by_text(arg, exact=False).first.click(timeout=8000)
                    out["ok"] = True
                elif action == "type":
                    page.keyboard.type(arg, delay=25)
                    out["ok"] = True
                elif action == "press":
                    page.keyboard.press(arg)
                    out["ok"] = True
                elif action == "scroll":
                    page.mouse.wheel(0, arg)
                    out["ok"] = True
                elif action == "fill_enter":
                    page.wait_for_selector(arg["sel"], timeout=15000)
                    page.fill(arg["sel"], arg["text"])
                    page.keyboard.press("Enter")
                    out["ok"] = True
                elif action == "wait":
                    page.wait_for_selector(arg, timeout=15000)
                    out["ok"] = True
                elif action == "click_nth":
                    els = page.query_selector_all(arg["sel"])
                    if not els:
                        raise RuntimeError("no results on the page")
                    if len(els) < arg["n"]:
                        raise RuntimeError(f"only {len(els)} results on the page")
                    el = els[arg["n"] - 1]
                    out["text"] = (el.inner_text() or "").strip()[:120]
                    el.click()
                elif action == "read":
                    out["text"] = page.inner_text("body")[:800]
                elif action == "url":
                    out["text"] = page.url
            except Exception as e:                # noqa: BLE001
                out["err"] = str(e).split("\n")[0][:200]
            out["done"] = True
    except Exception as e:                        # noqa: BLE001
        _browser["err"] = str(e).split("\n")[0][:300]
        # fail every future request fast, with the reason
        while True:
            action, arg, out = q.get()
            out["err"] = _browser["err"]
            out["done"] = True


def _browser_ask(action, arg=None, timeout=45):
    with _browser_lock:
        if not _browser["started"]:
            _browser["started"] = True
            threading.Thread(target=_browser_worker, args=(_cfg(),), daemon=True).start()
        if _browser["err"] or _browser["q"] is None:
            reason = _browser["err"] or "the browser never woke up"
            how = ("'pip install playwright' then 'playwright install chromium'"
                   if "playwright" in reason.lower() or "executable" in reason.lower() else "")
            return None, reason, how
    out = {}
    _browser["q"].put((action, arg, out))
    t0 = time.time()
    while not out.get("done") and time.time() - t0 < timeout:
        time.sleep(0.05)
    if not out.get("done"):
        return None, "the browser did not answer in time", ""
    if out.get("err"):
        return None, out["err"], ""
    return out, None, None


def browser_open(url):
    if not re.match(r"^https?://", url):
        url = "https://" + url
    out, err, _ = _browser_ask("open", url)
    _log("browser.open", url)
    if err:
        return {"answer": f"The browser balked at {url}, sir - {err}.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    title = ""
    return {"answer": f"Open, sir: {url}", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "browser", "url": url}


def browser_read():
    out, err, _ = _browser_ask("read")
    if err:
        return {"answer": f"I cannot read the page, sir - {err}.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    text = re.sub(r"\s+", " ", out.get("text", "")).strip()
    return {"answer": f"The page says: \"{text[:500]}{'...' if len(text) > 500 else ''}\"",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "browser"}


# ---------------------------------------------------------------------------
# skills - the butler learns. Draft -> YOUR approval -> armed.
# ---------------------------------------------------------------------------
FORBIDDEN_IMPORTS = {"subprocess", "socket", "shutil", "ctypes", "pickle",
                     "http.server", "socketserver", "multiprocessing", "signal"}
FORBIDDEN_CALLS = {"system", "popen", "remove", "unlink", "rmdir", "rmtree",
                   "kill", "terminate", "eval", "exec", "compile", "__import__"}


def _validate_skill(src):
    """Compile + AST seatbelt. Returns (ok, reason). A seatbelt, not a sandbox:
    an approved skill runs with your user's rights - that is why YOU approve."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return False, f"syntax error line {e.lineno}: {e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    return False, f"import of '{a.name}' is not allowed in skills"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
                return False, f"import of '{node.module}' is not allowed in skills"
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALLS:
                return False, f"'{fn.id}()' is not allowed in skills"
            if isinstance(fn, ast.Attribute) and fn.attr in FORBIDDEN_CALLS:
                if isinstance(fn.value, ast.Name) and fn.value.id == "os":
                    return False, f"os.{fn.attr}() is not allowed in skills"
    if not re.search(r"^def run\(", src, re.M):
        return False, "the file must define run(args)"
    if "config.json" in src:
        return False, "skills may not read config.json"
    return True, ""


SKILL_CONTRACT = """Write ONE small python file implementing a Jarvis skill.

Contract, non-negotiable:
- define exactly one entry point:  def run(args: str) -> str
  args is whatever the user typed after the skill's name; return a SHORT human-readable string.
- standard library only. No subprocess, no socket, no os.system, no eval/exec.
- No reading or writing of config.json or anything outside skills/_data/.
- Keep it under 60 lines. Handle bad input with a graceful sentence, not an exception.

The skill should: {desc}

Output ONLY the python source. No markdown fences, no commentary."""


def forge(description):
    if not _brain_ask:
        return {"answer": "No brain on duty to forge with, sir.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    src, err = _brain_ask([{"role": "user", "content": SKILL_CONTRACT.format(desc=description.strip())}])
    if err or not src:
        return {"answer": "The forge came back cold, sir - the brain would not write the file.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}
    src = re.sub(r"^```(?:python)?\s*|\s*```$", "", src.strip(), flags=re.S)
    ok, reason = _validate_skill(src)
    if not ok:
        return {"answer": (f"The draft failed inspection, sir - {reason}. I shall not "
                           f"put a crooked tool on the shelf. Try phrasing the skill differently."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower())[:24].strip("-") or "skill"
    py_path = os.path.join(PENDING_DIR, slug + ".py")
    n = 1
    while os.path.exists(py_path):
        n += 1
        py_path = os.path.join(PENDING_DIR, f"{slug}-{n}.py")
    name = os.path.basename(py_path)[:-3]
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(src)
    with open(os.path.join(PENDING_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"description": description.strip(), "forged": time.strftime("%Y-%m-%d")}, f)
    _log("skill.forge", name)
    return {"answer": (f"I have drafted a skill '{name}', sir - it awaits your approval in the "
                       f"armory panel, or say 'approve skill {name}'. Nothing runs until you do."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "skills", "skill": name}


def adopt(url):
    """Fetch a skill's source from a raw URL the USER chose, same inspection as forge."""
    s, raw = net.request(url, timeout=15, reason="arm:skills:adopt")
    if s != 200:
        return {"answer": f"That address said HTTP {s}, sir - I fetched nothing.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    src = raw.decode("utf-8", "replace")
    ok, reason = _validate_skill(src)
    if not ok:
        return {"answer": f"I fetched it, sir, but it failed inspection: {reason}. Rejected.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}
    base = os.path.basename(urllib.parse.urlparse(url).path)
    if base.endswith(".py"):
        base = base[:-3]
    name = (re.sub(r"[^a-z0-9-]", "-", base.lower())[:24].strip("-") or "skill")
    with open(os.path.join(PENDING_DIR, name + ".py"), "w", encoding="utf-8") as f:
        f.write(src)
    with open(os.path.join(PENDING_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"description": f"adopted from {url}", "forged": time.strftime("%Y-%m-%d")}, f)
    _log("skill.adopt", name)
    return {"answer": (f"Fetched and inspected, sir - '{name}' passed. It is pending your "
                       f"approval in the armory panel. Nothing runs until you approve."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "skills", "skill": name}


def _skill_path(name, pending):
    base = PENDING_DIR if pending else SKILLS_DIR
    p = os.path.join(base, name + ".py")
    return p if os.path.isfile(p) else None


def approve(name):
    src = _skill_path(name, pending=True)
    if not src:
        return {"answer": f"No draft called '{name}' is pending, sir.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    code = open(src, encoding="utf-8").read()
    ok, reason = _validate_skill(code)
    if not ok:
        return {"answer": f"'{name}' failed re-inspection ({reason}) and stays unpromoted, sir.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}
    os.replace(src, os.path.join(SKILLS_DIR, name + ".py"))
    meta = os.path.join(PENDING_DIR, name + ".json")
    if os.path.exists(meta):
        os.replace(meta, os.path.join(SKILLS_DIR, name + ".json"))
    _log("skill.approve", name)
    return {"answer": (f"'{name}' is approved and armed, sir. Say 'run skill {name}' and a word of "
                       f"what you want. Remember: it runs with your rights - you approved it."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}


def reject(name):
    src = _skill_path(name, pending=True)
    if not src:
        return {"answer": f"No draft called '{name}' is pending, sir.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    os.remove(src)
    meta = os.path.join(PENDING_DIR, name + ".json")
    if os.path.exists(meta):
        os.remove(meta)
    _log("skill.reject", name)
    return {"answer": f"'{name}' is struck from the shelf, sir.", "nodes": [],
            "on_topic": False, "route": "arms", "arm": "skills"}


def run_skill(name, args):
    path = _skill_path(name, pending=False)
    if not path:
        pending = [f[:-3] for f in os.listdir(PENDING_DIR) if f.endswith(".py")]
        hint = f" A draft called '{name}' awaits your approval." if name in pending else ""
        return {"answer": f"I have no skill called '{name}', sir.{hint}", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    out = {}
    def _work():
        try:
            spec = importlib.util.spec_from_file_location("jarvis_skill_" + name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out["text"] = str(mod.run(args))[:1500]
        except Exception as e:                    # noqa: BLE001
            out["err"] = f"{type(e).__name__}: {e}"
    th = threading.Thread(target=_work, daemon=True)
    th.start()
    th.join(RUN_TIMEOUT)
    if th.is_alive():
        return {"answer": f"'{name}' outstayed its welcome, sir - {RUN_TIMEOUT}s and I gave up on it.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "skills"}
    if out.get("err"):
        return {"answer": f"'{name}' stumbled, sir - {out['err']}.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "skills"}
    _log("skill.run", name)
    return {"answer": out.get("text", f"'{name}' ran and said nothing, sir."), "nodes": [],
            "on_topic": False, "route": "arms", "arm": "skills"}


def skills_listing():
    def _dir(base):
        out = []
        for f in sorted(os.listdir(base)):
            if not f.endswith(".py"):
                continue
            name = f[:-3]
            meta = {}
            mp = os.path.join(base, name + ".json")
            if os.path.exists(mp):
                try:
                    meta = json.load(open(mp, encoding="utf-8"))
                except Exception:              # noqa: BLE001
                    meta = {}
            out.append({"name": name, "description": meta.get("description", ""),
                        "forged": meta.get("forged", "")})
        return out
    return {"armed": _dir(SKILLS_DIR), "pending": _dir(PENDING_DIR)}


def skills_spoken():
    lst = skills_listing()
    armed = [s["name"] for s in lst["armed"]]
    pending = [s["name"] for s in lst["pending"]]
    line = (f"On the skills shelf, sir: {', '.join(armed)}." if armed
            else "The skills shelf is empty, sir.")
    if pending:
        line += f" Awaiting your approval: {', '.join(pending)}."
    return line


# ---------------------------------------------------------------------------
# scribe - files, PDFs and reports, written where you can find them
# ---------------------------------------------------------------------------
REPORT_SYSTEM = ("You are Jarvis, a dry, impeccably polite British butler. Write a short, "
                 "well-structured report in markdown: one '# title', two to four '## sections', "
                 "short paragraphs, bullets where they earn their place, and a closing "
                 "'## Bottom line'. Facts only; if you must estimate, mark it as an estimate. "
                 "No preamble, no sign-off - output the markdown only.")


def _desk_dir():
    """Desktop/Jarvis on the user's machine; output/ as the honest fallback."""
    base = os.path.join(os.path.expanduser("~"), "Desktop", "Jarvis")
    if not os.path.isdir(os.path.join(os.path.expanduser("~"), "Desktop")):
        base = os.path.join(ROOT, "output")
    os.makedirs(base, exist_ok=True)
    return base


def _md_to_pdf(md_text, pdf_path):
    """A clean, plain PDF from the markdown the brain wrote. Latin script."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListItem, ListFlowable
    styles = getSampleStyleSheet()
    story = []
    bullets = []

    def inline(s):                                   # **bold** and *italic* -> tags
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
        return s

    def flush():
        nonlocal bullets
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(inline(b), styles["BodyText"]))
                                       for b in bullets], bulletType="bullet", start="•"))
            bullets = []

    for line in md_text.splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            flush()
            story.append(Paragraph(inline(s[3:]), styles["Heading2"]))
        elif s.startswith("# "):
            flush()
            story.append(Paragraph(inline(s[2:]), styles["Title"]))
        elif s.startswith("- ") or s.startswith("* "):
            bullets.append(s[2:])
        elif s.strip():
            flush()
            story.append(Paragraph(inline(s.strip()), styles["BodyText"]))
            story.append(Spacer(1, 0.25 * cm))
    flush()
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, title="Jarvis report",
                            leftMargin=2 * cm, rightMargin=2 * cm)
    doc.build(story)


def make_report(topic, kind="report"):
    """Draft with the current brain, save .md, and render a PDF when reportlab exists."""
    if not _cfg().get("pc_control", {}).get("enabled"):
        return {"answer": ("Writing to your desktop is a hands job, sir - pc_control.enabled "
                           "is false in config.json. Flip it and I shall write."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "scribe"}
    if not _brain_ask:
        return {"answer": "No brain on duty to draft with, sir.", "nodes": [], "on_topic": False,
                "route": "arms", "arm": "scribe"}
    md, err = _brain_ask([{"role": "user", "content":
                           f"Write a {kind} about: {topic}. Today is {time.strftime('%d %B %Y')}."}])
    if err or not md:
        return {"answer": "The draft came back cold, sir - the brain would not write.", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "scribe"}
    md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md.strip(), flags=re.S)
    desk = _desk_dir()
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:40].strip("-") or "report"
    md_path = os.path.join(desk, slug + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    _log("scribe.md", md_path)
    lines = [f"The {kind} is drafted and saved at {md_path}, sir."]
    try:
        pdf_path = os.path.join(desk, slug + ".pdf")
        _md_to_pdf(md, pdf_path)
        size_kb = round(os.path.getsize(pdf_path) / 1024, 1)
        lines.append(f"The PDF is rendered beside it - {size_kb} KB.")
        _log("scribe.pdf", pdf_path)
    except ImportError:
        lines.append("For a PDF, sir: 'pip install reportlab' and ask me again - "
                     "the markdown is already on your desk.")
    except Exception as e:                            # noqa: BLE001
        lines.append(f"The PDF press jammed - {e}. The markdown is safe.")
    return {"answer": " ".join(lines), "nodes": [], "on_topic": False,
            "route": "arms", "arm": "scribe", "file": md_path}


def make_file(name, content):
    if not _cfg().get("pc_control", {}).get("enabled"):
        return {"answer": ("Writing to your desktop is a hands job, sir - pc_control.enabled "
                           "is false in config.json."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "scribe"}
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip() or "note"
    ext = ".md" if content.lstrip().startswith("#") else ".txt"
    path = os.path.join(_desk_dir(), safe + ext)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(os.path.dirname(path), f"{safe}-{n}{ext}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    _log("scribe.file", path)
    return {"answer": f"Written to {path}, sir.", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "scribe", "file": path}


# ---------------------------------------------------------------------------
# the watcher - eyes that stay open. Change-detected frames, rolling memory.
# ---------------------------------------------------------------------------
WATCHER = {"on": False, "frames": None, "last_sig": None, "started": 0,
           "interval": 15, "reason": "", "watched": 0}
WATCH_MIN_INTERVAL = 5          # seconds between glances, floor; tests patch this
_watch_lock = threading.Lock()


def _frame_sig(png_bytes):
    """A coarse grayscale signature of a frame - equal signatures mean 'nothing moved'."""
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((32, 18))
    return list(img.getdata())


def _frames_differ(a, b, tol=10):
    if a is None or b is None or len(a) != len(b):
        return True
    drift = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return drift > tol


def _watch_loop():
    while WATCHER["on"]:
        time.sleep(max(WATCH_MIN_INTERVAL, WATCHER["interval"]))
        if not WATCHER["on"]:
            break
        try:
            png, media = _grab_screenshot()
            if png is None:
                WATCHER["reason"] = media
                WATCHER["on"] = False
                break
            sig = _frame_sig(png)
            if WATCHER["last_sig"] is not None and not _frames_differ(sig, WATCHER["last_sig"]):
                continue                                # nothing moved - no brain spend
            WATCHER["last_sig"] = sig
            desc, _name, err = _vision_request(
                VISION_SYSTEM,
                _vision_message("One short sentence: what is on this screen right now?", png, media,
                                _vision_candidates()[0][1] if _vision_candidates() else "openai"),
                300)
            WATCHER["watched"] += 1
            WATCHER["frames"].append({"ts": time.strftime("%H:%M:%S"), "desc": (desc or f"(eyes fault: {err})")})
            _log("eyes.watch", (desc or "")[:120])
        except Exception as e:                        # noqa: BLE001 - the watcher never crashes out
            WATCHER["reason"] = f"{type(e).__name__}: {e}"
            WATCHER["on"] = False
            break


def watch_start():
    if WATCHER["on"]:
        return {"answer": "My eyes are already open, sir - I have been watching since I was asked.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    if not _vision_candidates():
        return {"answer": ("Watching needs a brain that can see, sir - paste a Gemini or Claude key "
                           "into config.json first."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    png, media = _grab_screenshot()
    if png is None:
        return {"answer": media, "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    interval = float(_cfg().get("eyes", {}).get("watch_interval_seconds") or 15)
    with _watch_lock:
        WATCHER.update({"on": True, "frames": [], "last_sig": None, "started": time.time(),
                        "interval": max(WATCH_MIN_INTERVAL, interval), "reason": "", "watched": 0})
        threading.Thread(target=_watch_loop, daemon=True).start()
    _log("eyes.watch", f"started, every {WATCHER['interval']}s")
    return {"answer": (f"Eyes open, sir. I shall glance every {WATCHER['interval']} seconds, note only what "
                       f"changes, and keep the last dozen observations. Ask 'what happened on my screen' "
                       f"whenever you like - or say 'stop watching'."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}


def watch_stop():
    if not WATCHER["on"]:
        return {"answer": "My eyes were already closed, sir.", "nodes": [], "on_topic": False,
                "route": "arms", "arm": "eyes"}
    WATCHER["on"] = False
    n = WATCHER["watched"]
    _log("eyes.watch", "stopped")
    return {"answer": f"Eyes closed, sir. I took {n} changing screen{'s' if n != 1 else ''} into the journal.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}


def watch_report():
    frames = WATCHER["frames"] or []
    if not WATCHER["on"] and not frames:
        return {"answer": ("I have not been watching, sir. Say 'watch my screen' and then ask me "
                           "what happened."), "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    if not frames:
        return {"answer": ("Nothing has changed on your screen since my eyes opened, sir. "
                           "Not one flicker worth a look."), "nodes": [], "on_topic": False,
                "route": "arms", "arm": "eyes"}
    tail = frames[-6:]
    summary = " ".join(f"[{f['ts']}] {f['desc']}" for f in tail)
    if _brain_ask:
        text, err = _brain_ask([{"role": "user", "content":
                                 f"These are my observations of your screen, newest last:\n{summary}\n\n"
                                 f"Answer as the butler: in two sentences, what has the employer been doing? "
                                 f"Address them as 'sir'."}])
        if not err and text:
            return {"answer": text, "nodes": [], "on_topic": False, "route": "arms", "arm": "eyes"}
    return {"answer": f"While watching, sir, I noted: {summary}", "nodes": [], "on_topic": False,
            "route": "arms", "arm": "eyes"}


# ---------------------------------------------------------------------------
# the pointing finger - "click on the subscribe button": see, aim, click
# ---------------------------------------------------------------------------
def _parse_point(text):
    """Tolerant JSON extraction: {'x': int, 'y': int} from a chatty brain reply."""
    if not text:
        return None
    m = re.search(r"\{\s*['\"]?x['\"]?\s*:\s*(-?\d+)\s*,\s*['\"]?y['\"]?\s*:\s*(-?\d+)\s*\}",
                  text, re.I)
    if not m:
        return None
    return {"x": int(m.group(1)), "y": int(m.group(2))}


def point_click(target):
    guard = _hands_guard("hands")
    if guard:
        return guard
    png, media = _grab_screenshot()
    if png is None:
        return {"answer": media, "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
    from PIL import Image as _Image
    img_w, img_h = _Image.open(io.BytesIO(png)).size
    prompt = (f"This screenshot is {img_w}x{img_h} pixels. Find the clickable element: "
              f"'{target}'. Reply with ONLY a JSON object of its centre in pixels: "
              f'{{"x": <int>, "y": <int>}}. If it is not visible, reply {{"x": -1, "y": -1}}. '
              f"No other words.")
    reply, _name, err = _vision_request(
        VISION_SYSTEM, _vision_message(prompt, png, media,
                                       _vision_candidates()[0][1] if _vision_candidates() else "openai"),
        200)
    pt = _parse_point(reply)
    if not pt or pt["x"] < 0 or pt["y"] < 0:
        return {"answer": (f"I studied the screen, sir, and '{target}' is not on it. "
                           f"Say it differently and I shall look again."),
                "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
    try:
        import pyautogui
        real_w, real_h = pyautogui.size()
    except ImportError:
        return {"answer": "The hands are not installed, sir: 'pip install pyautogui'.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}
    scale_x = real_w / img_w if img_w and real_w else 1.0
    scale_y = real_h / img_h if img_h and real_h else scale_x
    x, y = int(pt["x"] * scale_x), int(pt["y"] * scale_y)
    pyautogui.click(x, y)
    _log("hands.point_click", f"{target} @ {x},{y}")
    return {"answer": f"Clicked '{target}', sir - I aimed at pixel {x}, {y}.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "hands"}


# ---------------------------------------------------------------------------
# browser recipes - "search X on youtube", "open the second video"
# ---------------------------------------------------------------------------
RECIPES = {
    "youtube": {"url": "https://www.youtube.com",
                "input": "input#search",
                "results": "ytd-video-renderer a#video-title"},
    "google": {"url": "https://www.google.com",
               "input": "textarea[name='q']",
               "results": "#search a h3"},
}
ORDINALS = {"first": 1, "one": 1, "1st": 1, "second": 2, "two": 2, "2nd": 2,
            "third": 3, "three": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "7th": 7}
LAST_SEARCH = {"site": "youtube"}


def _ordinal(word):
    w = word.lower().strip(".")
    if w in ORDINALS:
        return ORDINALS[w]
    m = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", w)
    return int(m.group(1)) if m else None


def browser_search_site(site, query):
    rec = RECIPES[site]
    LAST_SEARCH["site"] = site
    out, err, _how = _browser_ask("open", rec["url"])
    if err:
        return {"answer": f"The browser balked at {site}, sir - {err}", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    out, err, _how = _browser_ask("fill_enter", {"sel": rec["input"], "text": query})
    if err:
        return {"answer": f"I could not reach {site}'s search box, sir - {err}", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    _browser_ask("wait", rec["results"])
    _log("browser.search", f"{site}: {query}")
    return {"answer": (f"Searched {site} for \"{query}\", sir - the results are on your screen. "
                       f"Say 'open the second video' or 'open the third result' and I shall click it."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "browser"}


def browser_open_result(word):
    n = _ordinal(word)
    if not n:
        return {"answer": "Which one, sir - first to seventh, or 'result 4'?", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    site = LAST_SEARCH["site"]
    sel = RECIPES[site]["results"]
    out, err, _how = _browser_ask("click_nth", {"sel": sel, "n": n})
    _log("browser.open_result", f"{site} #{n}")
    if err:
        return {"answer": f"The click did not land, sir - {err}", "nodes": [],
                "on_topic": False, "route": "arms", "arm": "browser"}
    what = (out.get("text") or "").strip() or f"result {n}"
    return {"answer": f"Opened {site} result {n}, sir - \"{what}\".", "nodes": [],
            "on_topic": False, "route": "arms", "arm": "browser"}


# ---------------------------------------------------------------------------
# THE HOLOCAM - barehands, vendored intact under ./barehands (AGPL-3.0).
# "Move things on your screen with your bare hands." The butler boots its
# stdlib server on 127.0.0.1:8794, opens the board page in your browser,
# mirrors his state onto the ring, and can put his answers on the glass as cards.
# EVERY byte here stays on loopback - this arm never touches net.py, because
# net.py is the door OUT of the house, and the holocam never leaves the house.
# ---------------------------------------------------------------------------
BH_DIR = os.path.join(ROOT, "barehands")
_holocam = {"proc": None, "log": None}


def _holocam_port():
    try:
        with open(os.path.join(BH_DIR, "barehands.json"), encoding="utf-8") as f:
            return int(json.load(f).get("port", 8794))
    except Exception:                                  # noqa: BLE001
        return 8794


def holocam_url():
    return "http://127.0.0.1:%d/stage.html" % _holocam_port()


def _py_bin():
    """A python interpreter that actually RUNS - board.sh's hunt, in Python.
    On a clean Windows 11, 'python' can be a Microsoft Store placeholder that
    answers to `which` and refuses to run, so each candidate is executed
    before it is trusted."""
    for c in ("python3", "python", "py"):
        if shutil.which(c):
            try:
                subprocess.run([c, "-c", "pass"], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return c
            except Exception:                          # noqa: BLE001
                continue
    return None


def holocam_alive(timeout=1.5):
    """True if the barehands server answers /orb on loopback."""
    try:
        u = holocam_url().replace("/stage.html", "/orb")
        with urllib.request.urlopen(u, timeout=timeout) as r:
            return r.status == 200
    except Exception:                                  # noqa: BLE001
        return False


def holocam_start():
    if not os.path.isfile(os.path.join(BH_DIR, "server.py")):
        return {"answer": "The holocam is not aboard, sir - the barehands folder is missing "
                          "from the installation. Re-clone the repo and it will be.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    already = holocam_alive()
    if not already:
        py = _py_bin()
        if not py:
            return {"answer": "I cannot find a working Python to raise the holocam with, sir - "
                              "which is absurd, as I am running on one. Install python.org's "
                              "real interpreter and I shall manage.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        try:
            logf = open(os.path.join(ROOT, "logs", "holocam.log"), "ab")
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen([py, "server.py"], cwd=BH_DIR,
                                    stdout=logf, stderr=subprocess.STDOUT, **kwargs)
            _holocam["proc"] = proc
            _holocam["log"] = logf
        except Exception as e:                         # noqa: BLE001
            return {"answer": f"The holocam refused to rise, sir - {type(e).__name__}: {e}.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
        up = False
        for _ in range(40):                            # 40 x 0.25 s = 10 s to live
            if holocam_alive(timeout=0.6):
                up = True
                break
            if _holocam["proc"].poll() is not None:
                break
            time.sleep(0.25)
        if not up:
            dead = "it exited at once" if _holocam["proc"].poll() is not None else "it never answered"
            return {"answer": f"The holocam server did not come up, sir - {dead}. "
                              "logs/holocam.log holds the autopsy. A second Jarvis may be squatting on port 8794.",
                    "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    STATE["holocam_running"] = True
    _log("holocam.start", holocam_url())
    try:
        webbrowser.open(holocam_url())                   # draw the curtain
    except Exception:                                  # noqa: BLE001 - headless boxes
        pass
    if already and _holocam["proc"] is None:
        spoken = ("The holocam was already up and someone else raised it, sir - I have merely "
                  "opened it in your browser. Allow the camera and wave.")
    else:
        spoken = ("The holocam is live, sir - barehands is up and open in your browser now. Allow the camera, then "
                  "pinch, throw and claw; the sample notes teach every gesture from inside "
                  "the board. Say 'put that on the board' whenever you want my last answer "
                  "up in glass.")
    return {"answer": spoken, "nodes": [], "on_topic": False, "route": "arms",
            "arm": "holocam", "url": holocam_url()}


def holocam_stop():
    proc = _holocam.get("proc")
    stopped_ours = False
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:                              # noqa: BLE001
            proc.kill()
        stopped_ours = True
    _holocam["proc"] = None
    STATE["holocam_running"] = False
    _log("holocam.stop")
    if stopped_ours:
        return {"answer": "The holocam is down, sir. The server on 8794 is struck; the browser "
                          "tab you may close yourself.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    if holocam_alive(timeout=0.8):
        return {"answer": "A holocam server is still answering on 8794, sir - but I did not raise "
                          "it this session, so I shall not shoot a process I cannot vouch for. "
                          "Close its own window, or say the word and I shall.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    return {"answer": "The holocam was already dark, sir.",
            "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}


_RING_WORDS = ("idle", "listening", "thinking", "speaking")


def holocam_ring(state):
    """Mirror Jarvis's state onto the ring: write one word to barehands/state/state.
    Fails silent - the ring is decoration, never a dependency."""
    try:
        state = str(state or "").strip().lower()
        if state not in _RING_WORDS:
            return False
        if not STATE["holocam_running"] and not holocam_alive(timeout=0.8):
            return False
        sdir = os.path.join(BH_DIR, "state")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "state"), "w", encoding="utf-8") as f:
            f.write(state)
        return True
    except Exception:                                  # noqa: BLE001
        return False


def holocam_board(cmd, timeout=4):
    """One command to the board's /cmd channel. The barehands server enforces
    its own action allowlist and the media airlock jail, so this passthrough
    is exactly as safe as upstream designed it to be. Loopback only."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/cmd" % _holocam_port(),
            data=json.dumps(cmd).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception as e:                             # noqa: BLE001
        return getattr(e, "code", 0)


def holocam_last_answer():
    if not (_last_answer or "").strip():
        return {"answer": "Nothing to put up yet, sir - ask me something first, then say "
                          "'put that on the board'.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    words = [w for w in re.sub(r"[^\w ]", " ", _last_answer).split() if w][:5]
    title = (" ".join(words) or "JARVIS").upper()
    code = holocam_board({"a": "present", "title": title[:40], "body": _last_answer})
    _log("holocam.present", title)
    if code == 204:
        return {"answer": "On the board, sir - centre stage, spotlit. Mind the claws.",
                "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
    return {"answer": (f"The board refused the card, sir (HTTP {code or 'no answer'}) - "
                       "is the holocam running? Say 'holocam' first and I shall try again."),
            "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}


def holocam_status():
    up = holocam_alive()
    ours = bool(_holocam.get("proc") and _holocam["proc"].poll() is None)
    if up:
        whose = " and it is mine" if ours else " (raised outside this session)"
        line = f"The holocam is live, sir - {holocam_url()}{whose}. Say 'put that on the board' to glass my last answer."
    else:
        line = "The holocam is dark, sir. Say 'holocam' or 'barehands' and I shall raise it, ring and all."
    return {"answer": line, "nodes": [], "on_topic": False, "route": "arms", "arm": "holocam"}
