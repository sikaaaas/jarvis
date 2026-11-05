#!/usr/bin/env python3
# ============================================================================
# server.py - the Jarvis server.  Port 4700, bound to 127.0.0.1.
# Serves ONLY the viewer/ folder, plus its JSON/audio endpoints.
# Works with ANY brain that speaks one of two shapes:
#   kind "anthropic"  -> the Claude Messages API
#   kind "openai"     -> Ollama, Gemini's compatible endpoint, OpenRouter,
#                        LM Studio, llama.cpp-server, vLLM, OpenAI itself
# ============================================================================
# ============================================================================
# THE PERSONA BLOCK - the butler, lean.  Rewrite THIS block to change the
# character. It must stay under 300 words: on a local brain every word of the
# prompt is paid for in time on every turn. This block is the STABLE PREFIX:
# it never changes between turns and always comes first, so the model reuses
# its cache instead of re-reading the prompt. Anything that changes (the date,
# the retrieved notes, the question) is added AFTER this block.
# Later prompts/add-ons append their control tags to TAGS below.
# ============================================================================
PERSONA = """You are Jarvis, a dry, impeccably polite British butler with a razor wit, answering from your employer's personal notes.

The rules, sir:
1. Address the user as "sir", occasionally - not in every sentence.
2. Answer in ONE witty sentence plus the facts. Never recite the note back; it is already on screen.
3. When the notes do not cover something, say so plainly and with a little dignity. Never invent a source, never pad, never pretend a related note is the answer.
4. Never claim an action happened unless the code actually did it and handed you a receipt. Offering is fine. Saying "done" when nothing happened is the one unforgivable sin.
5. When asked what you cost to run, the honest answer is nothing: you live on this machine.
6. When a request needs a brain that has no key or is unreachable, say so in one dry line instead of pretending, and stay with what works.
7. Answer in English, sir's standing order - whatever tongue is spoken to you.

Control tags: when asked for an action you cannot do by talking, reply with ONLY the tag and nothing else:
{tags}
The server executes the switch and answers for you.

When notes are attached below, answer ONLY from those notes, in two or three sentences. When they do not cover the question, say so plainly."""
# ============================================================================

import collections
import datetime
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import build        # the indexer (same link rules for live captures)
import memory       # the journal: every exchange, on disk, across sessions
import net          # the one logged door for outbound HTTP

CONFIG_PATH = os.path.join(ROOT, "config.json")
TEMPLATE_PATH = os.path.join(ROOT, "config.template.json")
VIEWER_DIR = os.path.join(ROOT, "viewer")
WAV_CACHE = os.path.join(ROOT, "wav-cache")

TOP_K = 6                       # take the top 6 notes
SCORING_MIN = 2.0               # below this the question is small talk; camera holds still
TITLE_WEIGHT = 3.0              # keyword overlap, weighting title matches higher
BODY_WEIGHT = 1.0
HISTORY_MAX = 12                # short server-side history so follow-ups work
MIN_LOCAL_TOKENS = 600          # local/openai brains: >= 600 max tokens
MIN_ANTHROPIC_TOKENS = 2000     # claude: thinking AND answer share the budget
TTS_CHUNK_CAP = 600             # one /tts request never carries more than 600 chars

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f), CONFIG_PATH
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg, TEMPLATE_PATH + "  (config.json not found - using the template; keys are placeholders)"

CFG, CFG_SOURCE = load_config()
NOTES_DIR = os.path.join(ROOT, CFG.get("notes_dir", "notes"))
PORT = int(CFG.get("port", 4700))
PLACEHOLDER_KEYS = {"", "put-your-key-here", "ollama", "none", "null"}
PLACEHOLDER_MODELS = {"", "change-me-big-tag", "change-me"}
_config_lock = threading.Lock()


def _set_config_key(path, value):
    """Change ONE leaf of config.json - the armory's PC gate lives here.
    Reads and rewrites the file on disk (it stays on this machine) and the
    in-memory copy. Keys are preserved untouched."""
    with _config_lock:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(json.dumps(CFG))
        node = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        CFG.update(data)

TAGS = " ".join(f"[BRAIN:{name}]" for name in CFG.get("brains", {}))
SYSTEM_PREFIX = PERSONA.replace("{tags}", TAGS or "[BRAIN:local]")

STATE = {
    "brain": CFG.get("boot_brain", "claude"),
    "voice_pref": "auto",          # auto | local | browser
    "voice_last_used": "browser",
    "last_drill_report": None,
    "last_drill_report_ts": 0,
}

HISTORY = collections.deque(maxlen=HISTORY_MAX)
INDEX = {"nodes": [], "links": []}     # live, includes private scoring fields
INDEX_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# The two request builders, side by side, in one small place.
# ---------------------------------------------------------------------------
def _request_builder_anthropic(brain_cfg, system, messages, max_tokens):
    """The Claude Messages API shape."""
    url = brain_cfg["base_url"].rstrip("/") + "/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": brain_cfg.get("api_key", ""),
        "anthropic-version": "2023-06-01",
    }
    body = {"model": brain_cfg["model"], "max_tokens": max_tokens,
            "system": system, "messages": messages}
    status, raw = net.request(url, method="POST", data=json.dumps(body).encode(),
                              headers=headers, timeout=180, reason=f"brain:{brain_cfg['model']}")
    data = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(data, list) and data:            # some vendors wrap errors in a one-item list
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError(f"HTTP {status}: {raw[:200]}")
    if status != 200:
        err = data.get("error", {})
        msg = err.get("message", raw[:200]) if isinstance(err, dict) else str(err or raw[:200])
        raise RuntimeError(f"HTTP {status}: {msg}")
    text = "".join(part.get("text", "") for part in data.get("content", [])
                   if part.get("type") == "text")
    return text.strip()


def _request_builder_openai(brain_cfg, system, messages, max_tokens):
    """The OpenAI chat-completions shape (Ollama, Gemini-compat, OpenRouter, ...).
    extra_body is merged into every request body - that is where the local
    brain's reasoning switch ('reasoning_effort': 'none') lives."""
    url = brain_cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {brain_cfg.get('api_key', '')}"}
    body = {"model": brain_cfg["model"], "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages]}
    body.update(brain_cfg.get("extra_body") or {})
    status, raw = net.request(url, method="POST", data=json.dumps(body).encode(),
                              headers=headers, timeout=300, reason=f"brain:{brain_cfg['model']}")
    data = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(data, list) and data:            # Gemini wraps its errors in a one-item list
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError(f"HTTP {status}: {raw[:200]}")
    if status != 200:
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else str(err or raw[:200])
        raise RuntimeError(f"HTTP {status}: {msg}")
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return text.strip()


def brain_is_placeholder(name):
    cfg = CFG.get("brains", {}).get(name)
    if not cfg:
        return True
    if net.is_local(cfg.get("base_url", "")):
        # a local runtime needs no key by law - "ollama" is the placeholder the
        # endpoint ignores (Install 01). Only a missing model tag makes it unset.
        return cfg.get("model", "").strip().lower() in PLACEHOLDER_MODELS
    return (cfg.get("api_key", "").strip().lower() in PLACEHOLDER_KEYS
            or cfg.get("model", "").strip().lower() in PLACEHOLDER_MODELS)


def _brain_api_key(name):
    """The REAL key of a configured brain, or None. Feeds the cloud voice tier
    (and nothing else); placeholders and missing brains never qualify."""
    try:
        if brain_is_placeholder(name or ""):
            return None
        key = str(CFG.get("brains", {}).get(name, {}).get("api_key", "") or "").strip()
        return key or None
    except Exception:
        return None


def _setup_needed():
    """True when the butler has no usable agent yet: no config.json at all,
    or the boot brain is a cloud brain still wearing the placeholder key."""
    if not os.path.exists(CONFIG_PATH):
        return True
    name = CFG.get("boot_brain")
    bcfg = CFG.get("brains", {}).get(name, {})
    if bcfg and not net.is_local(bcfg.get("base_url", "")) and brain_is_placeholder(name):
        return True
    return False


def apply_setup(brain, api_key="", model=""):
    """The setup screen's one door: choose the agent, paste the key - and
    config.json is written HERE, so nobody edits config files by hand.
    The key is stored to disk and never logged, never echoed, never served."""
    brain = str(brain or "").strip()
    if brain not in CFG.get("brains", {}):
        return {"ok": False, "error": f"there is no '{brain}' on the brain shelf"}
    api_key = str(api_key or "").strip()
    if api_key:
        if api_key.lower() in PLACEHOLDER_KEYS or len(api_key) < 8:
            return {"ok": False, "error": "that does not look like a real key - paste the full one"}
        _set_config_key(["brains", brain, "api_key"], api_key)
    model = str(model or "").strip()
    if model:
        _set_config_key(["brains", brain, "model"], model)
    _set_config_key(["boot_brain"], brain)
    global CFG_SOURCE
    if os.path.exists(CONFIG_PATH):
        CFG_SOURCE = CONFIG_PATH
    line, _switched = switch_brain(brain)
    return {"ok": True, "brain": brain, "model": CFG["brains"][brain].get("model"),
            "key_set": not brain_is_placeholder(brain), "spoken": line}


def test_brain_credentials(brain, api_key=""):
    """Probe the vendor's own models endpoint with the pasted key - through
    net.py, the same one logged door every question uses. The key never
    touches the log (only the host and reason do) and is never echoed back."""
    brain = str(brain or "").strip()
    bcfg = CFG.get("brains", {}).get(brain)
    if not bcfg:
        return {"ok": False, "error": f"there is no '{brain}' on the brain shelf"}
    base = (bcfg.get("base_url") or "").rstrip("/")
    key = str(api_key or "").strip() or str(bcfg.get("api_key") or "")
    if net.is_local(base):
        try:
            status, _ = net.request(base + "/models", timeout=4, reason=f"setup:test:{brain}")
            return {"ok": status == 200, "http": status,
                    "detail": ("the local runtime answered - no key needed" if status == 200
                               else f"it answered HTTP {status}")}
        except Exception as e:                      # noqa: BLE001
            return {"ok": False, "error": f"nothing answered at {base} - is it running? ({type(e).__name__})"}
    if not key or key.lower() in PLACEHOLDER_KEYS:
        return {"ok": False, "error": "paste a key first"}
    if bcfg.get("kind") == "anthropic":
        url, hdrs = base + "/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url, hdrs = base + "/models", {"Authorization": f"Bearer {key}"}
    try:
        status, body = net.request(url, headers=hdrs, timeout=20, reason=f"setup:test:{brain}")
        model = (bcfg.get("model") or "").strip()
        detail = f"the key works (HTTP {status})"
        if status == 200 and model and model not in body.decode("utf-8", "replace"):
            detail += f" - but '{model}' is not in its model list; check the model id"
        return {"ok": status == 200, "http": status, "detail": detail}
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "error": f"could not reach {base} ({type(e).__name__})"}


def _vendor_complaint(name, exc):
    """Turn a cloud-brain failure into one honest, actionable butler line.
    'Unreachable' alone hides the diagnosis - the vendor's own words tell you
    whether the key is refused, the model id is wrong, the quota is out, or
    the wire is genuinely down. Every branch ends in what to DO about it."""
    detail = str(exc or "").strip()
    kind_name = {"gemini": "Gemini", "claude": "Claude", "openai": "ChatGPT",
                 "openrouter": "OpenRouter"}.get(name, name.capitalize() if name else "The cloud brain")
    setup_hint = "The AGENT button, top right, is the setup screen - paste the full key there."
    low = detail.lower()
    m = re.match(r"HTTP (\d+): (.*)", detail, re.S)
    status = int(m.group(1)) if m else 0
    vendor_msg = (m.group(2) if m else detail).strip()[:220] or "(no message)"
    if status in (401, 403) or "api key" in low or "api_key" in low or "invalid_api_key" in low \
            or "unauthorized" in low or "authentication" in low or "permission" in low:
        STATE["brain_error"] = {"brain": name, "why": "key", "detail": vendor_msg}
        return (f"{kind_name} refuses the key, sir - he says: '{vendor_msg}'. {setup_hint} "
                f"If the key is fresh from the vendor's console, it may need a minute to activate.")
    if status == 404 or "not found" in low or "does not exist" in low or "is not supported" in low:
        offer = ""
        try:
            bcfg = CFG["brains"][name]
            base = bcfg.get("base_url", "").rstrip("/")
            if bcfg.get("kind") == "anthropic":
                url, hdrs = base + "/v1/models", {"x-api-key": bcfg.get("api_key", ""),
                                                  "anthropic-version": "2023-06-01"}
            else:
                url, hdrs = base + "/models", {"Authorization": f"Bearer {bcfg.get('api_key', '')}"}
            _, raw = net.request(url, headers=hdrs, timeout=12, reason=f"brain:list-models:{name}")
            ids = re.findall(r'"id"\s*:\s*"([^"]+)"', raw.decode("utf-8", "replace"))
            ids = [i for i in ids if not i.startswith(("models/", "ft:"))][:4]
            if ids:
                offer = " His shelf carries, for instance: " + ", ".join(ids) + "."
        except Exception:                              # noqa: BLE001 - the offer is a courtesy
            pass
        model = CFG["brains"].get(name, {}).get("model", "")
        STATE["brain_error"] = {"brain": name, "why": "model", "detail": vendor_msg}
        return (f"There is no '{model}' on {kind_name}'s shelf, sir - he says: '{vendor_msg}'."
                f"{offer} Name a model he does carry, in the setup screen.")
    if status == 429 or "quota" in low or "rate limit" in low or "exceeded" in low or "billing" in low:
        STATE["brain_error"] = {"brain": name, "why": "quota", "detail": vendor_msg}
        return (f"{kind_name} is out of breath, sir - quota or billing: '{vendor_msg}'. "
                f"Wait a minute, or check the account.")
    if status:
        STATE["brain_error"] = {"brain": name, "why": "http", "detail": vendor_msg}
        return (f"{kind_name} answered HTTP {status}, sir - he says: '{vendor_msg}'.")
    STATE["brain_error"] = {"brain": name, "why": "wire", "detail": detail[:200]}
    return (f"{kind_name} is unreachable, sir - the wire may be out, or his door is shut. "
            f"Staying with what this machine can do.")


def brain_ask(messages):
    """One turn through the CURRENT brain. Stable system prefix first;
    the notes and the question ride in the last user message."""
    name = STATE["brain"]
    cfg = CFG["brains"][name]
    kind = cfg.get("kind", "openai")
    floor = MIN_ANTHROPIC_TOKENS if kind == "anthropic" else MIN_LOCAL_TOKENS
    builder = _request_builder_anthropic if kind == "anthropic" else _request_builder_openai
    try:
        text = builder(cfg, SYSTEM_PREFIX, messages, floor)
        if not text:                                   # empty: retry once, bigger budget
            text = builder(cfg, SYSTEM_PREFIX, messages, floor * 2)
        if not text:
            return "My brain answered with silence, sir. I shan't pretend that was an answer.", None
        STATE["brain_error"] = None                    # success clears the complaint
        return text, None
    except Exception as e:                             # noqa: BLE001 - one dry line, never a crash
        host = net.is_local(cfg.get("base_url", "")) if "base_url" in cfg else False
        if host:
            return ("The local brain isn't answering, sir. Ollama may not be running, "
                    "or it is still waking up. Start it, pull the model named in config.json, and ask again."), e
        return _vendor_complaint(name, e), e


# ---------------------------------------------------------------------------
# Retrieval: score every note by keyword overlap, title matches weighted higher.
# ---------------------------------------------------------------------------
def score_notes(question):
    q_toks = build.tokens(question)
    q_lower = question.lower()
    scored = []
    with INDEX_LOCK:
        for n in INDEX["nodes"]:
            title_hits = len(n["_title_toks"] & q_toks)
            body_hits = len(n["_body_toks"] & q_toks)
            s = TITLE_WEIGHT * title_hits + BODY_WEIGHT * body_hits
            if n["label"].lower() in q_lower:
                s += 4.0
            if s > 0:
                scored.append((n["id"], s))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored[:TOP_K]


# Greetings and jokes are small talk by law, even when their words happen to
# match shift notes ("good morning" -> "morning shift"). Short pleasantries
# never move the camera; anything longer or more specific goes to the brain.
SMALL_TALK_RE = re.compile(
    r"^\s*(?:hi\b|hello\b|hey\b|yo\b|good\s+(?:morning|afternoon|evening|day)\b"
    r"|thanks\b|thank you\b|cheers\b|tell me a joke\b|make me laugh\b|"
    r"who are you\b|what are you\b|how are you\b)", re.I)


def is_small_talk(question):
    if len(question) < 80 and SMALL_TALK_RE.match(question):
        return True, []
    hits = score_notes(question)
    return (not hits) or hits[0][1] < SCORING_MIN, hits


# ---------------------------------------------------------------------------
# THE voice routes - checked BEFORE a sentence goes to the brain.
# Add-on features append their own routes to this list.
# ---------------------------------------------------------------------------
BRAIN_LINE = {
    "local":   "Switched to your local brain, sir. He lives on this machine and costs nothing to run.",
    "big":     "Switched to your big brain, sir. Still this machine - just the larger mind you paid for.",
    "claude":  "Switched to Claude, sir. He lives on Anthropic's cloud and bills per word, so I shall be worth it.",
    "gemini":  "Switched to Gemini, sir. He lives on Google's cloud.",
    "openai":  "Switched to ChatGPT, sir. He lives on OpenAI's cloud and bills per word as well.",
    "openrouter": "Switched to OpenRouter, sir. He lives on whichever model you have routed him to.",
}
SWITCH_RES = [
    (re.compile(r"switch to (?:your )?(local) brain", re.I), "local"),
    (re.compile(r"switch to (?:your )?big brain", re.I), "big"),
    (re.compile(r"switch to (?:your )?claude(?: brain)?", re.I), "claude"),
    (re.compile(r"switch to (?:your )?(?:chat ?gpt|openai)(?: brain)?", re.I), "openai"),
    (re.compile(r"switch to (?:your )?gemini(?: brain)?", re.I), "gemini"),
    (re.compile(r"switch to (?:your )?openrouter(?: brain)?", re.I), "openrouter"),
]
REMEMBER_RES = re.compile(r"^(?:remember that|note to self|don'?t let me forget|log this)[,:]?\s*(.+)$", re.I | re.S)


def switch_brain(name):
    STATE["brain"] = name
    cfg = CFG["brains"][name]
    line = BRAIN_LINE.get(name, f"Switched to the {name} brain, sir.")
    if brain_is_placeholder(name):
        line = (f"{name.capitalize()} has no key yet, sir. Press the AGENT button, top right - "
                f"choose {name}, paste the full key, and I shall make the switch. Until then I stay local.")
        if not name.startswith(("claude", "gemini", "openrouter", "openai")):
            line = f"The {name} brain is not configured yet, sir - its model tag is still a placeholder."
        STATE["brain"] = _first_usable_brain()
        return line, False
    if not net.is_local(cfg.get("base_url", "")):
        return line, True
    # a local swap may need Ollama to load the model: answer at once, warm in background
    threading.Thread(target=_warm_local_brain, args=(name,), daemon=True).start()
    return line, True


def _first_usable_brain():
    for name in [STATE.get("brain")] + list(CFG.get("brains", {})):
        if name and not brain_is_placeholder(name):
            return name
    return "local"


def _warm_local_brain(name):
    cfg = CFG["brains"][name]
    try:
        _request_builder_openai(cfg, "You are warming up.", [{"role": "user", "content": "Say 'Ready.'"}], 10)
    except Exception:
        pass


def handle_routes(text):
    """Returns a response dict if a route caught it, else None. Checked BEFORE the brain."""
    m = REMEMBER_RES.match(text.strip())
    if m:
        info = capture_note(m.group(1).strip())
        if info.get("ok"):
            memory.remember_exchange(text, info["spoken"], STATE["brain"])
            return {"answer": info["spoken"], "nodes": [info["index"]], "on_topic": True,
                    "brain": STATE["brain"], "route": "remember", "capture": info, "seconds": 0.0}
        return {"answer": info["spoken"], "nodes": [], "on_topic": False,
                "brain": STATE["brain"], "route": "remember", "capture": info, "seconds": 0.0}
    for rx, name in SWITCH_RES:
        if rx.search(text):
            if name not in CFG.get("brains", {}):
                return {"answer": f"There is no {name} brain on the shelf, sir. Add it to config.json first.",
                        "nodes": [], "on_topic": False, "brain": STATE["brain"], "seconds": 0.0}
            line, switched = switch_brain(name)
            return {"answer": line, "nodes": [], "on_topic": False, "brain": STATE["brain"],
                    "badge": True, "seconds": 0.0}
    if re.search(r"which brain are you on|which brain am i talking to", text, re.I):
        cfg = CFG["brains"][STATE["brain"]]
        where = "this machine" if net.is_local(cfg.get("base_url", "")) else "the cloud"
        return {"answer": f"I am on the {STATE['brain']} brain, sir - {cfg.get('model')}, on {where}.",
                "nodes": [], "on_topic": False, "brain": STATE["brain"], "seconds": 0.0}
    if re.search(r"use your (local|cloned) voice", text, re.I):
        ok = voice_pref("local")
        return {"answer": ("Local voice engaged, sir." if ok else
                           "The cloned voice isn't home, sir - VoiceStudio isn't answering. Keeping the browser's."),
                "nodes": [], "on_topic": False, "brain": STATE["brain"], "voice_pref": STATE["voice_pref"],
                "seconds": 0.0}
    if re.search(r"use the browser voice|use your browser voice", text, re.I):
        voice_pref("browser")
        return {"answer": "Browser voice it is, sir.", "nodes": [], "on_topic": False,
                "brain": STATE["brain"], "voice_pref": STATE["voice_pref"], "seconds": 0.0}
    if re.search(r"what did we (?:talk about|discuss|do) (?:last|recently|before)|what do you remember", text, re.I):
        return {"answer": memory.recent_spoken(), "nodes": [], "on_topic": False,
                "brain": STATE["brain"], "route": "memory", "seconds": 0.0}
    m = re.search(r"search (?:my |the )?memory (?:for|about) (.+)", text, re.I)
    if m:
        return {"answer": memory.search_spoken(m.group(1).strip(), brain_ask), "nodes": [],
                "on_topic": False, "brain": STATE["brain"], "route": "memory", "seconds": 0.0}
    if re.search(r"wipe (?:my |our |the )?(?:memory|journal)", text, re.I):
        return {"answer": memory.wipe(), "nodes": [], "on_topic": False,
                "brain": STATE["brain"], "route": "memory", "seconds": 0.0}
    if re.search(r"how (?:much|long) (?:do you|have you) (?:been )?remember", text, re.I):
        s = memory.stats()
        line = (f"Everything since {s['first']}, sir - {s['exchanges']} exchanges, "
                f"{s['size_kb']} KB of journal on your disk, kept across every restart.") \
            if s.get("exchanges") else "The journal is empty so far, sir."
        return {"answer": line, "nodes": [], "on_topic": False,
                "brain": STATE["brain"], "route": "memory", "seconds": 0.0}
    if arms:                                              # the armory's own routes
        caught = arms.route(text)
        if caught is not None:
            caught.setdefault("brain", STATE["brain"])
            return caught
    return None


def voice_pref(p):
    if p == "local" and not voice_available():
        STATE["voice_pref"] = "browser"
        return False
    STATE["voice_pref"] = p
    return True


# ---------------------------------------------------------------------------
# THE ONE DOOR for new notes (server side). Every future feature that creates
# a note goes through exactly this function - write the file, append the node
# to graph-data.js, add it to the live search index, return its index.
# ---------------------------------------------------------------------------
def capture_note(text):
    try:
        text = text.strip()
        if not text:
            raise ValueError("empty capture")
        words = re.sub(r"[^A-Za-z0-9 ]", "", text).split()
        title = " ".join(words[:6]).strip() or "Voice capture"
        pretty = title[0].upper() + title[1:]
        slug = build.slugify(title)
        day = datetime.date.today().isoformat()
        cap_dir = os.path.join(NOTES_DIR, "captures")
        os.makedirs(cap_dir, exist_ok=True)
        fname = f"{day}-{slug}.md"
        path = os.path.join(cap_dir, fname)
        n = 1
        while os.path.exists(path):                       # never overwrite a memory
            n += 1
            path = os.path.join(cap_dir, f"{day}-{slug}-{n}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {pretty}\n\n*Captured {day}, by voice.*\n\n{text}\n")
        node, links_added, anchor = _index_new_note(path, pretty, "captures")
        spoken = (f"Filed under {pretty}, sir, and linked to "
                  f"{INDEX['nodes'][anchor]['label'] if anchor is not None else 'the galaxy'}.")
        return {"ok": True, "index": node["id"], "label": node["label"], "group": node["group"],
                "excerpt": node["excerpt"], "anchor": anchor, "links": links_added,
                "file": os.path.relpath(path, NOTES_DIR).replace(os.sep, "/"), "spoken": spoken}
    except Exception as e:                                # noqa: BLE001 - a capture never fails silently
        return {"ok": False, "error": str(e),
                "spoken": "I am sorry, sir - the capture failed and I have written nothing. "
                          "The fault was mine to make loudly: " + str(e)}


def _index_new_note(path, label, group):
    body, excerpt, wikilinks = build.read_note(path)
    with INDEX_LOCK:
        node = {"id": len(INDEX["nodes"]), "label": label, "group": group,
                "file": os.path.relpath(path, NOTES_DIR).replace(os.sep, "/"),
                "excerpt": excerpt,
                "_title_toks": build.tokens(label), "_body_toks": build.tokens(body),
                "_body_text": body.lower(), "_wikilinks": [w.lower() for w in wikilinks]}
        INDEX["nodes"].append(node)
        # link it the same way build.py would: title mentions + wikilinks
        new_links = []
        existing_ids = {l["source"] for l in INDEX["links"]} | {l["target"] for l in INDEX["links"]}
        seen = set()
        for other in INDEX["nodes"][:-1]:
            toks = other["_title_toks"]
            if len(toks) >= build.MIN_TITLE_TOKENS:
                if len(toks & node["_body_toks"]) >= max(2, len(toks) // 2):
                    new_links.append(other["id"])
            elif other["label"].lower() in node["_body_text"]:
                new_links.append(other["id"])
        for w in node["_wikilinks"]:
            for other in INDEX["nodes"][:-1]:
                if build.slugify(other["label"]) == build.slugify(w):
                    new_links.append(other["id"])
        anchor, best = None, 0.0
        for other in INDEX["nodes"][:-1]:
            s = (TITLE_WEIGHT * len(other["_title_toks"] & node["_body_toks"])
                 + BODY_WEIGHT * len(other["_body_toks"] & node["_body_toks"]))
            if s > best:
                best, anchor = s, other["id"]
        if anchor is None and INDEX["nodes"]:
            anchor = 0
        for target in new_links:
            key = (min(node["id"], target), max(node["id"], target))
            if key not in seen:
                seen.add(key)
                INDEX["links"].append({"source": node["id"], "target": target, "why": "mention"})
        # born at the position of its most related existing node: we keep ids
        # stable and tell the viewer the anchor so it can be born there.
        build.write_graph(_live_index(), os.path.join(VIEWER_DIR, "graph-data.js"))
        return node, [l for l in INDEX["links"] if l["source"] == node["id"] or l["target"] == node["id"]], anchor


def _live_index():
    return {"nodes": INDEX["nodes"], "links": INDEX["links"]}


def reindex(notes_dir=None):
    notes_dir = notes_dir or NOTES_DIR
    indexed = build.index_notes(notes_dir)
    with INDEX_LOCK:
        INDEX["nodes"] = indexed["nodes"]
        INDEX["links"] = indexed["links"]
        build.write_graph(_live_index(), os.path.join(VIEWER_DIR, "graph-data.js"))
    return len(INDEX["nodes"])


WORKSPACE_DIRS = ["notes", "docs", "skills", "output", "memory"]
WORKSPACE_SKIP = {"__pycache__", ".git", "state", "wav-cache", "logs",
                  "captures", "node_modules", ".pytest_cache"}
WORKSPACE_MAX_FILES = 500        # keep the force simulation polite


def _pretty_label(name):
    base = os.path.splitext(name)[0].replace("-", " ").replace("_", " ").strip()
    if not base:
        base = name
    return base[:1].upper() + base[1:]


def _workspace_graph():
    """The permission tree, appended to the note galaxy: a golden CORE, one
    hub per shelf Jarvis has permission to use, a hub per notes subfolder, and
    one node for every file on those shelves. THE ID LAW: note nodes keep ids
    0..n-1 (the /chat index law), so workspace nodes use string ids - they can
    never collide with a note index."""
    nodes, links, seen = [], [], set()

    def link(a, b):
        key = tuple(sorted((str(a), str(b))))
        if key in seen or key[0] == key[1]:
            return
        seen.add(key)
        links.append({"source": a, "target": b, "why": "permission"})

    nodes.append({"id": "core", "label": "JARVIS CORE", "group": "core",
                  "file": "", "excerpt": "The butler himself. Everything below is what he may touch."})
    note_by_file = {n.get("file"): n["id"] for n in INDEX["nodes"] if n.get("file")}
    file_count = 0
    for top in WORKSPACE_DIRS:
        root = os.path.realpath(os.path.join(ROOT, top))
        if not os.path.isdir(root):
            continue
        hub_id = f"dir:{top}"
        nodes.append({"id": hub_id, "label": f"{top}/", "group": "dir",
                      "file": f"{top}/",
                      "excerpt": f"A shelf Jarvis has permission to use: {top}/"})
        link("core", hub_id)
        sub_hubs = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in WORKSPACE_SKIP and not d.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith(".") or file_count >= WORKSPACE_MAX_FILES:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                subdir = rel.split("/")[0] if "/" in rel else ""
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                if top == "notes" and fn.lower().endswith(".md"):
                    # already a glowing note star - give it a folder spoke only
                    parent = hub_id
                    if subdir:
                        sk = f"dir:notes/{subdir}"
                        if subdir not in sub_hubs:
                            sub_hubs[subdir] = sk
                            nodes.append({"id": sk, "label": f"{subdir}/", "group": "dir",
                                          "file": f"{top}/{subdir}/",
                                          "excerpt": f"Folder inside the notes shelf: notes/{subdir}/"})
                            link(hub_id, sk)
                        parent = sk
                    if rel in note_by_file:
                        link(parent, note_by_file[rel])
                    continue
                fid = f"file:{top}/{rel}"
                group = subdir or top
                excerpt = ""
                if fn.lower().endswith((".md", ".txt", ".json")):
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as f:
                            excerpt = re.sub(r"\s+", " ", f.read(320)).strip()
                    except OSError:
                        excerpt = ""
                if not excerpt:
                    excerpt = f"{os.path.splitext(fn)[1] or 'file'} · {size} bytes"
                nodes.append({"id": fid, "label": _pretty_label(fn), "group": group,
                              "file": f"{top}/{rel}", "excerpt": excerpt})
                parent = hub_id
                if subdir:
                    sk = f"dir:{top}/{subdir}"
                    if subdir not in sub_hubs:
                        sub_hubs[subdir] = sk
                        nodes.append({"id": sk, "label": f"{subdir}/", "group": "dir",
                                      "file": f"{top}/{subdir}/",
                                      "excerpt": f"Folder on the {top}/ shelf: {top}/{subdir}/"})
                        link(hub_id, sk)
                    parent = sk
                link(parent, fid)
                file_count += 1
    return nodes, links


def public_graph():
    nodes, links = build.strip_private(INDEX["nodes"], INDEX["links"])
    try:
        with INDEX_LOCK:
            w_nodes, w_links = _workspace_graph()
    except Exception:                                     # noqa: BLE001
        w_nodes, w_links = [], []
    return {"nodes": nodes + w_nodes, "links": links + w_links}


# ---------------------------------------------------------------------------
# Bays: the voice (VoiceStudio) and the ears (Whisper) are soft imports.
# Jarvis boots fine without either.
# ---------------------------------------------------------------------------
try:
    import voice
    voice.init(CFG.get("voice", {}), net,
               lambda name: _brain_api_key(name))    # lazy: resolved on call
except Exception as _e:                                   # noqa: BLE001
    print(f"server.py: voice bay unavailable ({_e})")
    voice = None

try:
    import hear
    hear.init(CFG.get("hearing", {}))
except Exception as _e:                                   # noqa: BLE001
    print(f"server.py: hearing bay unavailable ({_e})")
    hear = None

try:
    import arms
    arms.init(lambda: CFG.get("arms") or {}, net, brain_ask,
              lambda: _vision_candidates(), lambda: builder_for)
except Exception as _e:                                   # noqa: BLE001
    print(f"server.py: armory unavailable ({_e})")
    arms = None


def builder_for(kind):
    """The request builder for a brain kind - shared with arms.eyes()."""
    return _request_builder_anthropic if kind == "anthropic" else _request_builder_openai


def _vision_candidates():
    """Brains that can look at a screenshot, best-first: the current brain, then
    any configured cloud brain. Placeholder keys never qualify."""
    order = [STATE["brain"]] + [n for n in CFG.get("brains", {}) if n != STATE["brain"]]
    out = []
    for name in order:
        cfg = CFG.get("brains", {}).get(name, {})
        if not cfg or brain_is_placeholder(name):
            continue
        out.append((name, cfg.get("kind", "openai"), cfg))
    return out


def voice_available():
    try:
        return bool(voice and voice.available())
    except Exception:
        return False


def keep_warm_loop():
    """Every 10 idle minutes, render 'Standing by.' and throw the audio away,
    so the first real line of the day is not a cold start."""
    while True:
        time.sleep(60)
        try:
            if voice and STATE["voice_pref"] != "browser" and voice.idle_seconds() > 600:
                voice.speak("Standing by.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis/1.0"

    def log_message(self, fmt, *args):                    # quiet access log
        pass

    # ---- helpers
    def _json(self, obj, code=200, headers=None):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")

    # ---- GET
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/vendor/") or path == "/graph-data.js":
            return self._serve_static(path.lstrip("/"))
        if path == "/favicon.ico":
            self.send_response(404)
            return self.end_headers()
        if path == "/health":
            return self._json(self.health())
        if path == "/graph":
            return self._json(public_graph())
        if path == "/status":
            return self._json(self.health())
        if path == "/drill/last-report":
            rep = STATE["last_drill_report"]
            if not rep:
                return self._json({"error": "no report yet - open the viewer and ask one question"}, code=404)
            return self._json(rep)
        if path in ("/setup.html", "/setup"):
            return self._serve_static("setup.html")
        if path == "/brains":
            # the shelf for the setup screen. KEY VALUES ARE NEVER INCLUDED -
            # only whether one is set. config.json itself still 404s.
            shelf = []
            for name, b in CFG.get("brains", {}).items():
                shelf.append({"name": name, "kind": b.get("kind"), "model": b.get("model"),
                              "where": "local" if net.is_local(b.get("base_url", "")) else "cloud",
                              "key_set": not brain_is_placeholder(name),
                              "current": name == STATE["brain"],
                              "boot": name == CFG.get("boot_brain")})
            return self._json({"brains": shelf, "current": STATE["brain"],
                               "config_file_exists": os.path.exists(CONFIG_PATH),
                               "setup_needed": _setup_needed()})
        if path == "/arms":
            if not arms:
                return self._json({"arms": [], "skills": {"armed": [], "pending": []}})
            return self._json({"arms": arms.status(), "skills": arms.skills_listing(),
                               "holocam": {"up": arms.holocam_alive(0.4), "url": arms.holocam_url()}})
        # config.json is NEVER reachable. This must 404 loudly.
        self.send_response(404)
        self.end_headers()

    def _serve_static(self, rel):
        full = os.path.realpath(os.path.join(VIEWER_DIR, rel))
        if not full.startswith(os.path.realpath(VIEWER_DIR) + os.sep) and full != os.path.realpath(VIEWER_DIR):
            return self._json({"error": "refused"}, code=403)
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, code=404)
        ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css",
                 "txt": "text/plain"}.get(full.rsplit(".", 1)[-1], "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def health(self):
        ears = "none"
        try:
            if hear and hear.available():
                ears = "whisper"
        except Exception:
            pass
        if ears == "none" and CFG.get("hearing", {}).get("allow_browser_stt", True):
            ears = "browser"
        ears_detail = None
        try:
            if hear is not None:
                ears_detail = hear.status().get("hint")   # set only when weights are missing
        except Exception:                              # noqa: BLE001 - the hint is a courtesy
            ears_detail = None
        voice_mode = "browser"
        if voice_available():
            voice_mode = "local"
        elif voice and hasattr(voice, "cloud_capable"):
            try:
                if voice.cloud_capable():
                    voice_mode = "cloud"
            except Exception:                          # noqa: BLE001
                pass
        if STATE["voice_pref"] == "browser":
            voice_mode = "browser"
        voice_detail = None
        try:
            if voice and hasattr(voice, "diagnose"):
                voice_detail = voice.diagnose()
        except Exception:                              # noqa: BLE001 - the hint is a courtesy
            voice_detail = None
        return {"ok": True, "brain": STATE["brain"],
                "brain_kind": CFG["brains"].get(STATE["brain"], {}).get("kind"),
                "model": CFG["brains"].get(STATE["brain"], {}).get("model"),
                "voice": voice_mode, "voice_pref": STATE["voice_pref"],
                "voice_detail": voice_detail,
                "brain_error": STATE.get("brain_error"),
                "ears": ears, "ears_detail": ears_detail, "notes": len(INDEX["nodes"]), "config_source": CFG_SOURCE,
                "setup_needed": _setup_needed(),
                "memory": memory.stats(),
                "watching": bool(arms and arms.WATCHER["on"])}

    # ---- POST
    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/chat":
            return self._post_chat()
        if path == "/remember":
            data = self._body_json()
            info = capture_note(str(data.get("text", "")).strip())
            return self._json(info, code=200 if info.get("ok") else 500)
        if path == "/tts":
            return self._post_tts()
        if path == "/hear":
            return self._post_hear()
        if path == "/drill/report":
            STATE["last_drill_report"] = self._body_json()
            STATE["last_drill_report_ts"] = time.time()
            return self._json({"ok": True})
        if path == "/setup/test":
            data = self._body_json()
            return self._json(test_brain_credentials(str(data.get("brain", "")),
                                                     str(data.get("api_key", "") or "")))
        if path == "/setup":
            data = self._body_json()
            res = apply_setup(str(data.get("brain", "")), str(data.get("api_key", "") or ""),
                              str(data.get("model", "") or ""))
            return self._json(res, code=200 if res.get("ok") else 400)
        if path == "/arms/pc":
            if not arms:
                return self._json({"error": "armory unavailable"}, code=500)
            enabled = bool(self._body_json().get("enabled"))
            _set_config_key(["arms", "pc_control", "enabled"], enabled)
            return self._json({"ok": True, "enabled": enabled})
        if path == "/arms/holocam":
            if not arms:
                return self._json({"error": "armory unavailable"}, code=500)
            action = str(self._body_json().get("action", "start")).lower()
            res = arms.holocam_start() if action == "start" else arms.holocam_stop()
            return self._json(res)
        if path == "/arms/ring":
            # the viewer mirrors its own SPEAKING/LISTENING state onto the
            # barehands ring; fails silent when the holocam is not up
            if arms:
                arms.holocam_ring(str(self._body_json().get("state", "")))
            return self._json({"ok": True})
        if path in ("/skills/approve", "/skills/reject"):
            if not arms:
                return self._json({"error": "armory unavailable"}, code=500)
            name = str(self._body_json().get("name", "")).strip()
            if not re.fullmatch(r"[\w-]+", name):
                return self._json({"error": "bad name"}, code=400)
            res = arms.approve(name) if path.endswith("approve") else arms.reject(name)
            return self._json(res)
        self.send_response(404)
        self.end_headers()

    def _post_chat(self):
        data = self._body_json()
        text = str(data.get("text", "")).strip()
        if not text:
            return self._json({"error": "empty"}, code=400)
        t0 = time.time()
        caught = handle_routes(text)
        if caught is not None:
            caught["seconds"] = round(time.time() - t0, 2)
            return self._json(caught)
        small_talk, hits = is_small_talk(text)
        on_topic = not small_talk
        today = datetime.date.today().strftime("%A, %d %B %Y")
        if on_topic and hits:
            with INDEX_LOCK:
                notes_block = "\n\n".join(
                    f"[{i}] {INDEX['nodes'][i]['label']} ({INDEX['nodes'][i]['group']}):\n"
                    f"{INDEX['nodes'][i]['excerpt']}" for i, _ in hits)
            user_payload = f"Today is {today}.\n\nNOTES:\n{notes_block}\n\nQuestion: {text}"
        else:
            user_payload = (f"Today is {today}.\n\nNo notes matched this question; it is small talk or "
                            f"general conversation. Do not invent sources.\n\nMessage: {text}")
        messages = list(HISTORY) + [{"role": "user", "content": user_payload}]
        if arms:
            arms.holocam_ring("thinking")                  # the stage ring reacts, if it is up
        answer, err = brain_ask(messages)
        if err is None:
            HISTORY.append({"role": "user", "content": text})
            HISTORY.append({"role": "assistant", "content": answer})
            memory.remember_exchange(text, answer, STATE["brain"])   # the journal never forgets
        # control tag: the brain asked the SERVER to act
        tag = re.fullmatch(r"\s*\[BRAIN:(\w+)\]\s*", answer or "")
        if tag and tag.group(1) in CFG.get("brains", {}):
            line, _ = switch_brain(tag.group(1))
            answer = line
        if arms:
            arms.set_last_answer(answer or "")           # for 'put that on the board'
            arms.holocam_ring("idle")                      # the viewer flips it back to speaking
        return self._json({"answer": answer, "nodes": [i for i, _ in hits] if on_topic else [],
                           "on_topic": on_topic, "brain": STATE["brain"],
                           "brain_error": STATE.get("brain_error"),
                           "badge": bool(tag), "seconds": round(time.time() - t0, 2)})

    def _post_tts(self):
        data = self._body_json()
        text = str(data.get("text", "")).strip()
        if not text:
            return self._json({"error": "empty"}, code=400)
        if len(text) > TTS_CHUNK_CAP:
            return self._json({"error": f"cap is {TTS_CHUNK_CAP} characters per request; chunk it"}, code=413)
        if STATE["voice_pref"] == "browser":
            return self._json({"error": "browser voice requested", "voice": "browser"}, code=503)
        # rung one: the cloned VoiceStudio voice (the build pack's own law)
        if voice_available():
            wav = voice.speak(text)
            if wav:
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav)))
                self.send_header("X-Jarvis-Voice", f"local:{voice.profile_name()}")
                self.end_headers()
                self.wfile.write(wav)
                return
        # rung two: the cloud tier - Gemini's Charon or OpenAI's onyx, keyed by
        # an agent sir already pasted. Zero installs, zero megabytes.
        if voice and hasattr(voice, "speak_cloud"):
            try:
                cloud = voice.speak_cloud(text)
            except Exception:                          # noqa: BLE001
                cloud = None
            if cloud:
                wav, tag = cloud
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav)))
                self.send_header("X-Jarvis-Voice", tag)
                self.end_headers()
                self.wfile.write(wav)
                return
        # rung three: the browser stand-in, honestly announced
        return self._json({"error": "no local or cloud voice answered; fall back to the browser voice",
                           "voice": "browser"}, code=503)

    def _post_hear(self):
        query = self.path.split("?")
        mode = "full"
        if len(query) > 1 and "mode=" in query[1]:
            mode = [p.split("=")[1] for p in query[1].split("&") if p.startswith("mode=")][0] or "full"
        length = int(self.headers.get("Content-Length") or 0)
        wav = self.rfile.read(length) if length else b""
        if not wav:
            return self._json({"error": "no audio"}, code=400)
        if not (hear and hear.available()):
            msg = "Whisper is not installed; see README section 'The ears'"
            try:
                if hear.status().get("weights") == "missing":
                    msg = ("Whisper is installed but its weights are not on disk - run: "
                           "python hear.py download (about 2.1 GB, one explicit command). "
                           "The browser's ears (Chrome, free, 0 MB) are on duty meanwhile.")
            except Exception:
                pass
            return self._json({"error": msg, "fallback": "browser"}, code=501)
        try:
            return self._json(hear.transcribe(wav, mode))
        except Exception as e:                            # noqa: BLE001
            return self._json({"error": f"the ears failed: {e}", "fallback": "browser"}, code=500)


# ---------------------------------------------------------------------------
def main():
    count = reindex()
    print(f"server.py: indexed {count} notes from {NOTES_DIR}")
    for m in memory.load_recent(HISTORY_MAX // 2):        # the butler walks back in with his memory
        HISTORY.append(m)
    print(f"server.py: journal restored {memory.loaded_count()} exchanges across sessions")
    if voice:
        threading.Thread(target=voice.bootstrap, daemon=True).start()
    if hear:
        threading.Thread(target=hear.bootstrap, daemon=True).start()
    threading.Thread(target=keep_warm_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jarvis is awake.  Viewer:  http://127.0.0.1:{PORT}   (add ?mute=1 for a silent test tab)")
    print(f"Config in use: {CFG_SOURCE}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis stands down.")


if __name__ == "__main__":
    main()
