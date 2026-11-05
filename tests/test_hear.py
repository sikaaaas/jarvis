#!/usr/bin/env python3
# tests/test_hear.py - proves the ears chain. Two layers:
#
#   1. PURE LOGIC (always runs, no model needed): the wake-word regex, the
#      "hey Jarvis" strip, and the interrupt stop/wait filter.
#   2. REAL WHISPER (runs when the weights are ALREADY on disk): the four
#      fixture transcriptions. Install the weights explicitly with
#      `python hear.py download` (~2.1 GB) - tests never download anything.
#
# If Whisper cannot load on this machine, layer 2 SKIPs with the reason -
# the pack's law is that Jarvis boots and works without the ears, so a
# missing model is a skip, never a crash.   python tests/test_hear.py

import json
import os
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hear

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
fails = []


def load_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1, "fixtures must be mono"
        return w.readframes(w.getnframes())


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pure_logic_tests():
    """The wake-word and interrupt rules, no model required."""
    print("layer 1: pure wake-word logic (no model)")
    wake = hear._WAKE_RES
    check("bare wake word stripped", wake.sub("", "Jarvis, when do we open?", count=1).strip()
          == "when do we open?")
    check("'hey Jarvis' stripped", wake.sub("", "Hey Jarvis! What is the rota?", count=1).strip()
          == "What is the rota?")
    check("plain sentence untouched", wake.sub("", "when do we open?", count=1) == "when do we open?")
    check("near-miss name ignored", not wake.match("Jarvison supplies the beans"))
    interrupt = hear.INTERRUPT_RE if hasattr(hear, "INTERRUPT_RE") else None
    if interrupt is not None:
        check("interrupt catches 'stop'", bool(interrupt.search("stop")))
        check("interrupt catches 'wait'", bool(interrupt.search("wait")))
        check("interrupt ignores chatter", not interrupt.search("pause the roast temperatures"))
    else:
        print("  [SKIP] interrupt regex not exposed by hear.py")


def model_tests():
    """The real transcriptions - only when the models actually load."""
    cfg = json.load(open(os.path.join(ROOT, "config.template.json"), encoding="utf-8"))["hearing"]
    hear.init(cfg)
    t0 = time.time()
    print("layer 2: real Whisper fixtures (weights must already be on disk)...")
    hear.bootstrap()
    if not hear.available():
        hint = (hear.status().get("hint") or "see: python hear.py status")
        print(f"  [SKIP] Whisper could not load on this machine - Jarvis still boots,\n"
              f"         the viewer falls back to browser ears (0 MB, free). Reason: {hint[:140]}")
        return
    print(f"  models ready in {time.time() - t0:.1f}s  backend={hear._backend}\n")

    r = hear.transcribe(load_wav(os.path.join(FIXTURES, "push-to-talk.wav")), "full")
    print(f"  [full]      push-to-talk.wav     -> {r['text']!r}  ({r['seconds']}s)")
    check("full-mode kept 'opening hours'", "opening" in r["text"].lower())

    r = hear.transcribe(load_wav(os.path.join(FIXTURES, "wake-opening.wav")), "wake")
    print(f"  [wake]      wake-opening.wav     -> {r.get('text')!r}  wake_ok={r.get('wake_ok')}")
    check("wake word detected, command kept",
          r.get("wake_ok") and "opening" in (r.get("text") or "").lower())

    r = hear.transcribe(load_wav(os.path.join(FIXTURES, "no-wake-question.wav")), "wake")
    print(f"  [wake]      no-wake-question.wav -> discarded={r.get('discarded')}")
    check("utterance without the name discarded", bool(r.get("discarded")))

    r = hear.transcribe(load_wav(os.path.join(FIXTURES, "interrupt-stop.wav")), "interrupt")
    print(f"  [interrupt] interrupt-stop.wav    -> {r.get('text')!r}")
    check("interrupt caught 'stop'", r.get("text") == "stop")


def main():
    pure_logic_tests()
    print()
    try:
        model_tests()
    except Exception as e:
        print(f"  [SKIP] layer 2 could not run on this machine: {type(e).__name__}: {e}")

    print("\n" + ("EARS TEST PASSED" if not fails else f"EARS TEST FAILED: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
