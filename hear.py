#!/usr/bin/env python3
# hear.py - the ears bay. Soft-imported by server.py so Jarvis boots fine
# without Whisper installed. No browser speech recognition on this path:
# the audio never leaves this machine.
#
#   Apple Silicon -> mlx-whisper   (fastest on the Mac's own chip)
#   Windows/Linux -> faster-whisper (uses the NVIDIA card when there is one,
#                                    the CPU otherwise)
#
# THE DOWNLOAD LAW - read this twice:
#   Jarvis NEVER downloads a big file on his own. Not on boot, not in the
#   background, not "helpfully". The browser's own ears (Chrome/Edge, 0 MB,
#   free) are the default way he hears you. If you later want ears that
#   never leave the room, Whisper is installed in two EXPLICIT steps:
#
#       pip install faster-whisper      (Windows/Linux)  or  mlx-whisper (Mac)
#       python hear.py download         (~1.6 GB + ~0.5 GB, announced, your yes)
#
#   A missing weight file never triggers a download - this module refuses,
#   prints that one command, and lets the browser ears carry on.
#
# Two models, loaded ONCE and kept in memory:
#   hearing.model       ("large-v3-turbo") for real commands
#   hearing.wake_model  ("small")          for the wake word and interrupts
# Each is warmed with one second of silence so the first real transcription
# is not a cold start.

import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import time

MODEL = "large-v3-turbo"
WAKE_MODEL = "small"

# THE GEMINI EARS - the middle rung of the hearing ladder. Firefox, Zen and
# friends have no built-in speech recognition; when Whisper's weights are not
# on disk, a recorded WAV can still be transcribed by the Gemini brain whose
# key sir already pasted (free tier, inline audio, one request per utterance).
# Still not a boot download: zero bytes fetched to offer this.
GEMINI_EARS_MODEL = "gemini-3.8-flash"
GEMINI_EARS_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_EARS_PROMPT = ("Transcribe this recording exactly, word for word. "
                      "Output ONLY the words spoken - no labels, no punctuation commentary.")
# the free tier's two kinds of tired: a burst limit (rest a minute) and the
# day's allowance spent (rest until Google resets it, midnight US Pacific)
MINUTE_QUOTA_REST = 70          # seconds
DAY_QUOTA_REST = 1800           # half an hour, then one honest knock every so often
DAY_REMEDY = ("The local Whisper ears never have a quota: "
              "python hear.py download    (about 1.6 GB, once, on purpose)")

_backend = None          # "ct2" | "mlx" | None
_ready = False
_weights_missing = False
_models = {}
_lock = threading.Lock()
_gemini = {"net": None, "key_for": None, "down_until": 0.0, "down_reason": None}

MLX_REPOS = {"large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
             "small": "whisper-small-mlx"}

WEIGHTS_HINT = ("Whisper is installed, but the model weights are not on this machine "
                "(about 1.6 GB + 0.5 GB). Nothing was downloaded - Jarvis never pulls a "
                "big file on his own. To install the local ears on purpose, run: "
                "python hear.py download    (or keep the browser's ears: 0 MB, free).")

class _WeightsMissing(Exception):
    pass

def init(cfg, net_module=None, key_lookup=None):
    global MODEL, WAKE_MODEL, _backend
    MODEL = cfg.get("model", MODEL)
    WAKE_MODEL = cfg.get("wake_model", WAKE_MODEL)
    _gemini["net"] = net_module
    _gemini["key_for"] = key_lookup or (lambda name: None)
    _gemini["model"] = cfg.get("gemini_model", GEMINI_EARS_MODEL)
    try:
        import faster_whisper                       # noqa: F401
        _backend = "ct2"
    except Exception:
        try:
            import mlx_whisper                      # noqa: F401
            _backend = "mlx"
        except Exception:
            _backend = None

def gemini_ears_available():
    """True when a real Gemini key is on the shelf - the cloud rung of the
    ears exists whether or not Whisper was ever installed."""
    try:
        key = _gemini["key_for"]("gemini")
        return bool(key) and str(key).lower() not in ("put-your-key-here", "")
    except Exception:
        return False

def _gemini_transcribe(wav_bytes):
    """One WAV -> text through the Gemini brain (inline audio, free tier).
    Raises on refusal; a quota-aware cooldown after failure keeps a dead key
    from being hammered on every utterance (a spent free day rests much
    longer than a per-minute burst, and says so)."""
    if time.time() < _gemini["down_until"]:
        if _gemini.get("down_reason") == "day":
            raise RuntimeError("the Gemini ears are out for today - the free day is spent. "
                               "They rest until Google resets the free tier. " + DAY_REMEDY)
        raise RuntimeError("the Gemini ears are cooling down - try again in a minute")
    key = _gemini["key_for"]("gemini")
    if not key:
        raise RuntimeError("no Gemini key on the shelf")
    body = {
        "contents": [{"parts": [
            {"text": GEMINI_EARS_PROMPT},
            {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav_bytes).decode()}},
        ]}],
        "generationConfig": {"maxOutputTokens": 512},
    }
    model = _gemini.get("model", GEMINI_EARS_MODEL)
    status, raw = _gemini["net"].request(
        f"{GEMINI_EARS_BASE}/models/{model}:generateContent",
        method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        timeout=30, reason="hear:gemini-ears")
    data = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(data, list) and data:
        data = data[0]
    if status != 200 or not isinstance(data, dict) or data.get("error"):
        err = (data.get("error") or {}).get("message", raw[:160]) if isinstance(data, dict) else str(raw[:160])
        _rest_after(raw.decode("utf-8", "replace"), status)
        if _gemini.get("down_reason") == "day":
            raise RuntimeError(f"the Gemini ears refused: {err} - the free day is spent, sir. "
                               "They rest until Google resets the free tier (midnight, US Pacific). "
                               + DAY_REMEDY)
        if _gemini.get("down_reason") == "minute":
            raise RuntimeError(f"the Gemini ears refused: {err} - the free tier's minute is spent; "
                               "try again in about a minute.")
        raise RuntimeError(f"the Gemini ears refused: {err}")
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        _gemini["down_until"] = time.time() + 60
        _gemini["down_reason"] = "silence"
        raise RuntimeError("the Gemini ears heard nothing")
    return text

def _rest_after(raw_text, status):
    """Decide how long the ears must rest, from the vendor's own refusal.
    Google marks the two kinds of tired differently: a per-minute burst
    (quotaId/quota_limit carrying 'PerMinute') and the free day spent
    ('PerDay').  The day is the one that used to lie: a flat 60 s cooldown
    kept promising 'a minute' while the whole allowance was gone until
    midnight US Pacific."""
    if status == 429 or "RESOURCE_EXHAUSTED" in raw_text:
        day = "PerDay" in raw_text or "per day" in raw_text.lower()
        _gemini["down_reason"] = "day" if day else "minute"
        _gemini["down_until"] = time.time() + (DAY_QUOTA_REST if day else MINUTE_QUOTA_REST)
    else:
        _gemini["down_reason"] = "error"
        _gemini["down_until"] = time.time() + 60

def ears_note():
    """None when the Gemini ears are fit, else one honest sentence for
    /health and the viewer's status strip."""
    if time.time() >= _gemini["down_until"]:
        return None
    reason = _gemini.get("down_reason")
    if reason == "day":
        return ("the Gemini ears are out for today - the free day is spent. "
                "They rest until Google resets the free tier (midnight, US Pacific). "
                "Until then, type to Jarvis - or " + DAY_REMEDY)
    if reason == "minute":
        return ("the Gemini ears are out of breath - the free tier allows only a few "
                "words per minute. Try again in about a minute, or " + DAY_REMEDY)
    if reason == "silence":
        return "the Gemini ears heard nothing usable on the last try - try again."
    return "the Gemini ears stumbled on the last try - they rest a minute before trying again."

def transcribe_any(wav_bytes, mode="full"):
    """The full hearing ladder for one utterance: local Whisper when its
    weights are home, else the Gemini ears. mode 'wake'/'interrupt' laws are
    applied to either backend identically, so the viewer never needs to know
    which ear did the hearing."""
    t0 = time.time()
    if available():
        return transcribe(wav_bytes, mode)
    text = _gemini_transcribe(wav_bytes)
    if mode == "interrupt":
        m = INTERRUPT_RE.search((text or "").lower())
        return {"text": m.group(1) if m else None, "via": "gemini",
                "seconds": round(time.time() - t0, 2)}
    if mode == "wake":
        if not _WAKE_RES.match(text or ""):
            return {"text": None, "discarded": True, "via": "gemini",
                    "seconds": round(time.time() - t0, 2)}
        rest = _WAKE_RES.sub("", text, count=1).strip()
        if not rest:
            return {"wake_only": True, "text": None, "via": "gemini",
                    "seconds": round(time.time() - t0, 2)}
        return {"text": rest, "wake_ok": True, "via": "gemini",
                "seconds": round(time.time() - t0, 2)}
    return {"text": text, "via": "gemini", "seconds": round(time.time() - t0, 2)}

def available():
    return _backend is not None and _ready

def status():
    """Honest state of the ears, for /health, preflight and the console.
    The cache check never downloads and never loads the model."""
    if _backend is None:
        weights = "not installed"
    elif _ready:
        weights = "on disk"
    elif _weights_missing or not weights_on_disk():
        weights = "missing"
    else:
        weights = "on disk"
    return {"backend": _backend, "ready": _ready, "weights": weights,
            "model": MODEL, "wake_model": WAKE_MODEL,
            "hint": WEIGHTS_HINT if weights == "missing" else None}

def weights_on_disk():
    """True when BOTH models' weights are already in the local cache.
    Cache-only: this can never download anything."""
    if _backend is None:
        return False
    return _model_on_disk(MODEL) and _model_on_disk(WAKE_MODEL)

def _model_on_disk(name):
    if _backend == "ct2":
        try:
            from faster_whisper.utils import _MODELS
            repo = _MODELS.get(name)
        except Exception:
            repo = None
        if repo:
            try:
                import huggingface_hub
                huggingface_hub.snapshot_download(repo_id=repo, local_files_only=True)
                return True
            except Exception:
                return False
        return _hf_cache_contains(name)
    try:
        import huggingface_hub
        huggingface_hub.snapshot_download(repo_id=MLX_REPOS.get(name, name),
                                          local_files_only=True)
        return True
    except Exception:
        return False

def _hf_cache_contains(needle):
    """Last-resort fuzzy look in the Hugging Face cache folder."""
    try:
        import huggingface_hub
        hub = huggingface_hub.constants.HF_HUB_CACHE
    except Exception:
        hub = os.path.expanduser("~/.cache/huggingface/hub")
    try:
        low = needle.lower().replace("_", "-").split("/")[-1]
        for entry in os.listdir(hub):
            if low in entry.lower():
                return True
    except Exception:
        return False
    return False

def bootstrap():
    """Load both models ONCE - ONLY when their weights are already on disk.
    Missing weights are never fetched here (THE DOWNLOAD LAW); the browser's
    ears take over and the console prints the one command that installs."""
    global _ready
    if _backend is None:
        return
    t0 = time.time()
    try:
        _ensure_weights(local_only=True)
        silence = _one_second_of_silence()
        _run(_models[MODEL], silence)
        _run(_models[WAKE_MODEL], silence)
        _ready = True
        print(f"hear.py: ears ready ({_backend}, {MODEL} + {WAKE_MODEL}) in {time.time() - t0:.1f}s")
    except _WeightsMissing:
        print(f"hear.py: {WEIGHTS_HINT}")
    except Exception as e:                          # noqa: BLE001
        print(f"hear.py: could not load Whisper ({e}). Jarvis will boot without ears; "
              f"see README section 'The ears'.")

def download(auto_yes=False):
    """The ONE path that fetches weights - explicit, announced, never on boot.
    This is what `python hear.py download` runs."""
    global _ready, _weights_missing
    if _backend is None:
        print("hear.py: no Whisper engine installed yet. First the engine, yourself:")
        print("  pip install faster-whisper   (Windows/Linux)")
        print("  pip install mlx-whisper      (Mac)")
        print("...then run: python hear.py download")
        return False
    print("hear.py: about to download the ears, on purpose:")
    print(f"  - {MODEL}   (~1.6 GB)  the ears for real commands")
    print(f"  - {WAKE_MODEL}   (~0.5 GB)  the small model for the wake word + interrupts")
    print("  They land in the Hugging Face cache. Nothing else is fetched, ever.")
    if not auto_yes:
        try:
            ans = input("Type yes to download (anything else cancels): ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("hear.py: cancelled - the browser's ears stay on duty (0 MB, free).")
            return False
    t0 = time.time()
    _ensure_weights(local_only=False)               # the deliberate fetch, with your yes
    silence = _one_second_of_silence()
    _run(_models[MODEL], silence)
    _run(_models[WAKE_MODEL], silence)
    _ready = True
    _weights_missing = False
    print(f"hear.py: ears ready ({_backend}, {MODEL} + {WAKE_MODEL}) in {time.time() - t0:.1f}s")
    return True

def _ensure_weights(local_only):
    """Touch both models. With local_only=True this can NEVER download:
    a missing weight raises _WeightsMissing instead of fetching."""
    global _weights_missing
    for name in (MODEL, WAKE_MODEL):
        if _backend == "ct2":
            try:
                _get_model(name, local_only=local_only)
            except TypeError:
                _get_model(name, local_only=False)  # very old faster-whisper: no opt-out kwarg
            except Exception as e:
                if not local_only:
                    raise
                _weights_missing = True
                raise _WeightsMissing(str(e)) from e
        else:
            if local_only and not _model_on_disk(name):
                _weights_missing = True
                raise _WeightsMissing(name)

def _get_model(name, local_only=True):
    with _lock:
        if name in _models:
            return _models[name]
        if _backend == "ct2":
            from faster_whisper import WhisperModel
            try:
                m = WhisperModel(name, device="auto", compute_type="auto",
                                 local_files_only=local_only)
            except TypeError:                       # very old faster-whisper, no opt-out kwarg
                m = WhisperModel(name, device="auto", compute_type="auto")
        else:
            m = MLX_REPOS.get(name, name)           # mlx_whisper takes a repo id
        _models[name] = m
        return m

def _one_second_of_silence():
    import struct
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * 16000)
    return buf.getvalue()

def _run(model, wav_bytes):
    if _backend == "ct2":
        segments, _info = model.transcribe(io.BytesIO(wav_bytes), beam_size=1)
        return " ".join(s.text for s in segments).strip()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    try:
        import mlx_whisper
        return mlx_whisper.transcribe(path, path_or_hf_repo=model)["text"].strip()
    finally:
        os.unlink(path)

_WAKE_RES = re.compile(r"^(?:hey\s+)?jarvis\b[\s,.!:]*", re.I)
_WORD_RES = re.compile(r"[a-z']+")
# while Jarvis is speaking, ONLY these words count - nothing else interrupts
INTERRUPT_RE = re.compile(r"\b(stop|wait)\b", re.I)

def transcribe(wav_bytes, mode="full"):
    """mode 'full'      -> the large model transcribes the command.
    mode 'wake'     -> the SMALL model listens first; only utterances that
                       start with 'Jarvis' (or 'hey Jarvis') go further. If the
                       name is alone, the viewer says 'Yes, sir?' and treats the
                       NEXT utterance as the command. Anything without the name
                       is discarded: never logged, never sent anywhere.
    mode 'interrupt' -> while Jarvis is speaking, only 'stop' and 'wait' count."""
    t0 = time.time()
    if mode == "wake":
        small_text = _run(_get_model(WAKE_MODEL), wav_bytes)
        if not _WAKE_RES.match(small_text or ""):
            return {"text": None, "discarded": True, "seconds": round(time.time() - t0, 2)}
        rest = _WAKE_RES.sub("", small_text, count=1).strip()
        if not rest:
            return {"wake_only": True, "text": None, "seconds": round(time.time() - t0, 2)}
        big_text = _run(_get_model(MODEL), wav_bytes)
        rest = _WAKE_RES.sub("", big_text or "", count=1).strip() or rest
        return {"text": rest, "wake_ok": True, "seconds": round(time.time() - t0, 2)}
    if mode == "interrupt":
        small_text = _run(_get_model(WAKE_MODEL), wav_bytes)
        m = INTERRUPT_RE.search((small_text or "").lower())
        hit = m.group(1) if m else None
        return {"text": hit, "seconds": round(time.time() - t0, 2)}
    text = _run(_get_model(MODEL), wav_bytes)
    return {"text": text, "seconds": round(time.time() - t0, 2)}

# ---------------------------------------------------------------------------
# Command line:  python hear.py status    honest state, no downloads
#                python hear.py download  the ONE explicit fetch (~2.1 GB)
# ---------------------------------------------------------------------------
def _cli():
    cfg = {}
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = (json.load(f) or {}).get("hearing", {})
    except Exception:
        pass
    init(cfg)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "download":
        ok = download(auto_yes="--yes" in sys.argv)
        raise SystemExit(0 if ok else 1)
    print(json.dumps(status(), indent=2))
    if status().get("hint"):
        print(WEIGHTS_HINT)

if __name__ == "__main__":
    _cli()
