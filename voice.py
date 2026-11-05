#!/usr/bin/env python3
# voice.py - the voice bay. Python standard library only, soft-imported by
# server.py so Jarvis boots fine without VoiceStudio installed.
#
# VoiceStudio (https://voicestudio.sh per the build pack) clones a voice from a
# 10-20 second clip and speaks any text through it. The engine answers on
# http://127.0.0.1:3900 whenever the app is open. This bay pins the engine so
# it is never silently swapped for the app's default, caches every WAV on disk,
# and keeps the engine warm. If it is not answering, the server falls back to
# the browser's Web Speech API - never a crash, never a silent failure.

import base64
import hashlib
import io
import json
import os
import re
import threading
import time

URL = "http://127.0.0.1:3900"
ENGINE = "voxcpm2"            # Windows (NVIDIA): voxcpm2 | Mac (Apple Silicon): mlx-audio
MODEL_ID = "voxcpm2"          # Windows: voxcpm2 | Mac: mlx-community/csm-1b-8bit
PROFILE_NAME = "Jarvis"       # looked up BY NAME from GET /profiles
LANGUAGE = "en"

# The CLOUD TIER - the middle rung between the cloned voice and the browser
# stand-in. Uses an agent key sir ALREADY pasted (config "brains"), so the
# upgrade costs zero installs and zero megabytes. Tried only when VoiceStudio
# is not answering, and only if "cloud_fallback" is not false in the voice
# config. Every request goes through net.py, so the drill sees it - honest.
GEMINI_TTS_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]
GEMINI_TTS_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_VOICE_DEFAULT = "Charon"      # deep, dry, butler register
OPENAI_TTS_MODELS = ["gpt-4o-mini-tts", "tts-1"]
OPENAI_TTS_BASE = "https://api.openai.com/v1"
OPENAI_VOICE_DEFAULT = "onyx"        # the deep one

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wav-cache")
IDLE_WARM_SECS = 600          # every 10 idle minutes

_state = {"profile_id": None, "selected": False, "last_speak": 0.0, "healthy": False}
_lock = threading.Lock()


def init(cfg, net_module, key_lookup=None):
    global URL, ENGINE, MODEL_ID, PROFILE_NAME, LANGUAGE, net, key_for, \
        GEMINI_VOICE_DEFAULT, OPENAI_VOICE_DEFAULT, _cfg
    URL = cfg.get("url", URL).rstrip("/")
    ENGINE = cfg.get("engine", ENGINE)
    MODEL_ID = cfg.get("model_id", MODEL_ID)
    PROFILE_NAME = cfg.get("profile", PROFILE_NAME)
    LANGUAGE = cfg.get("language", LANGUAGE)
    GEMINI_VOICE_DEFAULT = cfg.get("gemini_voice", GEMINI_VOICE_DEFAULT)
    OPENAI_VOICE_DEFAULT = cfg.get("openai_voice", OPENAI_VOICE_DEFAULT)
    _cfg = cfg
    net = net_module
    key_for = key_lookup or (lambda name: None)


_cfg = {}
key_for = lambda name: None   # noqa: E731 - replaced by init()


def bootstrap():
    """Called once at boot, in a background thread: pin the engine, find the
    profile by name. Retries quietly - VoiceStudio may not be open yet."""
    while True:
        if available():
            try:
                _select_engine_once()
                _resolve_profile()
                return
            except Exception:
                pass
        time.sleep(30)


def available():
    """The law: if /health does not answer within one second, the local voice
    is 'not home' and the server tells the viewer to use the browser voice."""
    try:
        status, _ = net.request(f"{URL}/health", timeout=1, reason="voicestudio:health")
        _state["healthy"] = (status == 200)
        return _state["healthy"]
    except Exception:
        _state["healthy"] = False
        return False


_DIAG = {"ts": 0.0, "result": None}


def diagnose():
    """WHY the local voice is or is not home - one plain detail string the
    viewer can show, so 'the voice is funky' has a next step. Cheap: a /health
    probe, and a /profiles lookup only while the profile is still unresolved.
    Cached for five seconds so /health polling never hammers the engine."""
    now = time.time()
    if _DIAG["result"] is not None and now - _DIAG["ts"] < 5:
        return dict(_DIAG["result"])
    result = {"home": False, "detail":
              "VoiceStudio is not answering on its port - open the app "
              "(Windows install: irm https://voicestudio.sh/install.ps1 | iex). "
              "The browser voice is the stand-in until then."}
    try:
        if available():
            try:
                _select_engine_once()
                _resolve_profile()
                result = {"home": True, "detail": "the cloned voice is live"}
            except Exception as e:                     # noqa: BLE001 - app up, voice not cloned
                result = {"home": False, "detail":
                          "VoiceStudio is up, but the profile named 'Jarvis' is missing - "
                          "in the app: Profiles > New > Clone, feed it a 10-20 second clean clip, "
                          "name it exactly 'Jarvis' "
                          f"(the engine said: {type(e).__name__}). "
                          "The browser voice is the stand-in until then."}
    except Exception:                                  # noqa: BLE001 - never break /health over a hint
        pass
    _DIAG["ts"] = now
    _DIAG["result"] = result
    return dict(result)


def _select_engine_once():
    """POST /engines/select once per engine run, so the pinned engine is never
    silently swapped for the app's default voice pack."""
    if _state["selected"]:
        return
    body = {"family": "tts", "backend_id": ENGINE, "model_id": MODEL_ID}
    net.request(f"{URL}/engines/select", method="POST",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                timeout=10, reason="voicestudio:select-engine")
    _state["selected"] = True


def _resolve_profile():
    if _state["profile_id"]:
        return _state["profile_id"]
    status, raw = net.request(f"{URL}/profiles", timeout=5, reason="voicestudio:profiles")
    data = json.loads(raw.decode("utf-8", "replace"))
    profiles = data if isinstance(data, list) else data.get("profiles", data.get("data", []))
    for p in profiles:
        if str(p.get("name", "")).strip().lower() == PROFILE_NAME.lower():
            _state["profile_id"] = p.get("id") or p.get("profile_id") or p.get("uuid")
            break
    if not _state["profile_id"]:
        raise RuntimeError(f"voice profile '{PROFILE_NAME}' not found - clone it in VoiceStudio first")
    return _state["profile_id"]


def profile_name():
    return PROFILE_NAME


def _cache_path(text):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha1(f"{text}|{PROFILE_NAME}|{LANGUAGE}|{MODEL_ID}".encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{key}.wav")


def speak(text):
    """Returns WAV bytes for this text in the cloned voice, or None.
    Every WAV is cached on disk by a hash of text plus profile, so repeated
    lines like the greeting are instant."""
    if not text:
        return None
    path = _cache_path(text)
    if os.path.exists(path) and os.path.getsize(path) > 44:
        with open(path, "rb") as f:
            _state["last_speak"] = time.time()
            return f.read()
    try:
        _select_engine_once()
        profile_id = _resolve_profile()
        body = {"model": MODEL_ID, "input": text, "voice": profile_id,
                "response_format": "wav", "language": LANGUAGE}
        status, raw = net.request(f"{URL}/v1/audio/speech", method="POST",
                                  data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"},
                                  timeout=120, reason="voicestudio:speech")
        if status != 200 or not raw:
            return None
        with open(path, "wb") as f:
            f.write(raw)
        _state["last_speak"] = time.time()
        return raw
    except Exception:
        return None


def idle_seconds():
    return time.time() - _state["last_speak"] if _state["last_speak"] else 1e9


# ---------------------------------------------------------------------------
# The cloud tier: a proper deep male voice with zero installs, using an agent
# key that is ALREADY on the shelf. Order: Gemini TTS (the Charon voice - the
# same key the brain uses), then OpenAI TTS (onyx). Returns
# (wav_bytes, "cloud:gemini:Charon") or None so the caller falls through to
# the browser stand-in. Never raises.
# ---------------------------------------------------------------------------
def cloud_capable():
    """Honest boot report: could a cloud voice speak right now?"""
    if not _cfg.get("cloud_fallback", True):
        return False
    try:
        return bool(key_for("gemini") or key_for("openai"))
    except Exception:
        return False


def _cloud_cache_path(text, engine_tag):
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha1(f"{text}|{engine_tag}|{LANGUAGE}".encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"c{key}.wav")


def _pcm_to_wav(pcm, rate):
    """Gemini returns raw 16-bit mono PCM; wrap a proper WAV header around it."""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _gemini_tts(text, api_key):
    voice_name = str(_cfg.get("gemini_voice", GEMINI_VOICE_DEFAULT))
    path = _cloud_cache_path(text, f"gemini:{voice_name}")
    if os.path.exists(path) and os.path.getsize(path) > 44:
        with open(path, "rb") as f:
            return f.read(), f"cloud:gemini:{voice_name}"
    for model in GEMINI_TTS_MODELS:
        try:
            body = {
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name}}},
                },
            }
            status, raw = net.request(
                f"{GEMINI_TTS_BASE}/models/{model}:generateContent",
                method="POST",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": api_key},
                timeout=60, reason="voice:gemini-tts")
            if status != 200 or not raw:
                continue
            data = json.loads(raw.decode("utf-8", "replace"))
            part = (data.get("candidates") or [{}])[0].get("content", {})
            inline = (part.get("parts") or [{}])[0].get("inlineData", {})
            b64, mime = inline.get("data"), inline.get("mimeType", "")
            if not b64:
                continue
            pcm = base64.b64decode(b64)
            rate = 24000
            m = re.search(r"rate=(\d+)", mime)
            if m:
                rate = int(m.group(1))
            wav = _pcm_to_wav(pcm, rate)
            with open(path, "wb") as f:
                f.write(wav)
            return wav, f"cloud:gemini:{voice_name}"
        except Exception:
            continue
    return None


def _openai_tts(text, api_key):
    voice_name = str(_cfg.get("openai_voice", OPENAI_VOICE_DEFAULT))
    path = _cloud_cache_path(text, f"openai:{voice_name}")
    if os.path.exists(path) and os.path.getsize(path) > 44:
        with open(path, "rb") as f:
            return f.read(), f"cloud:openai:{voice_name}"
    for model in OPENAI_TTS_MODELS:
        try:
            body = {"model": model, "voice": voice_name, "input": text,
                    "response_format": "wav"}
            status, raw = net.request(
                f"{OPENAI_TTS_BASE}/audio/speech", method="POST",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                timeout=60, reason="voice:openai-tts")
            if status == 200 and raw and raw[:4] == b"RIFF":
                with open(path, "wb") as f:
                    f.write(raw)
                return raw, f"cloud:openai:{voice_name}"
        except Exception:
            continue
    return None


def speak_cloud(text):
    """The middle rung. (wav, tag) on success, None to fall through to the
    browser stand-in. Cached on disk like the local voice."""
    if not text or not _cfg.get("cloud_fallback", True):
        return None
    try:
        gem = key_for("gemini")
        if gem:
            got = _gemini_tts(text, gem)
            if got:
                _state["last_speak"] = time.time()
                return got
        oai = key_for("openai")
        if oai:
            got = _openai_tts(text, oai)
            if got:
                _state["last_speak"] = time.time()
                return got
    except Exception:
        return None
    return None
