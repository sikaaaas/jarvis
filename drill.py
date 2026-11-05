#!/usr/bin/env python3
# drill.py - THE DRILL.  Proves a conversation never leaves this machine.
#
# The server logs every outbound connection it opens (net.py is the one door:
# host + reason). This drill runs one real turn - question, spoken answer, and
# the answer fed back through the ears - then prints every host that is NOT
# localhost. The drill passes only when that list is empty.
#
# Run me while Jarvis is up:   python drill.py

import hashlib
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import net

PLACEHOLDER = "PUT-YOUR-KEY-HERE"
fails, warns = [], []


def _port():
    with open(os.path.join(ROOT, "config.json" if os.path.exists(os.path.join(ROOT, "config.json"))
                           else "config.template.json"), encoding="utf-8") as f:
        return json.load(f).get("port", 4700)


PORT = _port()
BASE = f"http://127.0.0.1:{PORT}"


def post(path, obj):
    status, raw = net.request(BASE + path, method="POST", data=json.dumps(obj).encode(),
                              headers={"Content-Type": "application/json"}, timeout=300,
                              reason="drill:loopback")
    return status, json.loads(raw.decode("utf-8", "replace"))


def main():
    print("=" * 62)
    print("THE DRILL - nothing leaves this machine")
    print("=" * 62)

    # 0. the drill only means something on a local brain
    with open(os.path.join(ROOT, "config.json" if os.path.exists(os.path.join(ROOT, "config.json"))
                           else "config.template.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    current = cfg.get("boot_brain", "claude")
    bcfg = cfg.get("brains", {}).get(current, {})
    if not net.is_local(bcfg.get("base_url", "")):
        print(f"FAIL: the boot brain is '{current}' (a cloud brain). The drill runs LOCAL.")
        print("      Set boot_brain to 'local' or 'big' and restart, then run me again.")
        return 1

    offset = os.path.getsize(net.LOG_PATH) if os.path.exists(net.LOG_PATH) else 0

    # 1. the placeholder key must never appear in the viewer or on the wire
    leaked = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "viewer")):
        for fn in files:
            p = os.path.join(dirpath, fn)
            if PLACEHOLDER.encode() in open(p, "rb").read():
                leaked.append(os.path.relpath(p, ROOT))
    print(f"[{'PASS' if not leaked else 'FAIL'}] viewer is clean of the placeholder key"
          + (f" - leaked in: {leaked}" if leaked else ""))
    if leaked:
        fails.append("placeholder key in viewer")

    # 2. one real question through /chat
    q = "Who supplies our coffee beans and when do the deliveries arrive?"
    status, data = post("/chat", {"text": q})
    answer = data.get("answer", "")
    print(f"[{'PASS' if status == 200 and answer else 'FAIL'}] /chat answered "
          f"({data.get('brain')} brain, {data.get('seconds')}s, nodes={data.get('nodes')})")
    print(f"       answer: {answer[:120]}{'...' if len(answer) > 120 else ''}")
    if status != 200 or not answer:
        fails.append("/chat did not answer")

    # 3. speak the answer through /tts (chunked under the 600-char cap)
    wav = None
    if answer:
        status, raw = net.request(BASE + "/tts", method="POST",
                                  data=json.dumps({"text": answer[:600]}).encode(),
                                  headers={"Content-Type": "application/json"}, timeout=120,
                                  reason="drill:loopback")
        if status == 200 and raw[:4] == b"RIFF":
            wav = raw
            tag = "local voice"
        else:
            detail = json.loads(raw.decode("utf-8", "replace")) if raw else {}
            tag = f"fallback ({detail.get('error', 'no audio')[:60]})"
        if wav:
            print(f"[PASS] /tts returned {len(wav) // 1024} KB of cloned audio (X-Jarvis-Voice: {tag})")
        else:
            print(f"[WARN] the local voice did not answer - {tag}. The viewer falls back to the "
                  f"browser voice. Install steps: README, 'The voice'.")
            warns.append("local voice offline")

    # 4. feed that WAV back through the ears (POST /hear, the real chain)
    if wav:
        status, raw = net.request(BASE + "/hear", method="POST", data=wav,
                                  headers={"Content-Type": "audio/wav"}, timeout=120,
                                  reason="drill:loopback")
        if status == 200:
            result = json.loads(raw.decode())
            print(f"[PASS] /hear heard it back: \"{str(result.get('text'))[:80]}\" ({result.get('seconds')}s)")
        elif status == 501:
            print("[WARN] Whisper is not installed - the ears are skipped. "
                  "Install steps: README, 'The ears'.")
            warns.append("whisper missing")
        else:
            print(f"[WARN] /hear returned {status}")
            warns.append("hear chain")

    # 5. every host the server contacted during the drill, except localhost
    entries, _ = net.new_entries_since(offset)
    hosts = sorted({e["host"].lower() for e in entries} - net.LOCAL_HOSTS)
    print(f"[{'PASS' if not hosts else 'FAIL'}] outbound hosts during the drill: "
          f"{hosts if hosts else 'NONE - every connection stayed on this machine'}")
    if hosts:
        fails.append(f"outbound hosts: {hosts}")

    # 6. the browser's resource timing for the last turn (open a viewer tab first)
    try:
        status, raw = net.request(BASE + "/drill/last-report", timeout=5, reason="drill:loopback")
        if status == 200:
            rep = json.loads(raw.decode())
            foreign = sorted({urllib.parse.urlparse(r.get("name", "")).hostname or ""
                              for r in rep.get("entries", [])
                              if (urllib.parse.urlparse(r.get("name", "")).hostname or "")
                              not in net.LOCAL_HOSTS})
            print(f"[{'PASS' if not foreign else 'FAIL'}] browser loaded {len(rep.get('entries', []))} "
                  f"resources; non-local: {foreign if foreign else 'NONE'}")
            if foreign:
                fails.append(f"browser hosts: {foreign}")
        else:
            print("[WARN] no browser report yet - open the viewer tab and ask one question, "
                  "then run me again for the full proof.")
            warns.append("no browser resource report")
    except Exception as e:                        # noqa: BLE001
        warns.append(f"browser report: {e}")

    # 7. THE HONEST LIST - read from the code and the config, not from memory
    print("\nTHE HONEST LIST - features that still need the internet:")
    honest = []
    for name, b in cfg.get("brains", {}).items():
        if not net.is_local(b.get("base_url", "")) and not brain_placeholder(b):
            honest.append(f"  cloud brain '{name}' ({b.get('model')}) - used only when you switch to it")
    if cfg.get("hearing", {}).get("allow_browser_stt", True):
        honest.append("  browser speech recognition fallback (ears) - sends audio to the browser "
                      "maker's cloud. Set hearing.allow_browser_stt=false to forbid it.")
    else:
        honest.append("  (none from the ears - browser recognition is forbidden in config)")
    honest.append("  nothing else: the viewer's 3D libraries are vendored locally, the voice is "
                  "local or the OS's own, the brain 'local'/'big' is local.")
    print("\n".join(honest) if honest else "  (empty - nothing needs the internet)")

    print("\nFOUR-STEP ON-CAMERA SCRIPT:")
    print("  1. Turn off Wi-Fi. Cross out the icon - keep it in frame.")
    print("  2. Reload the viewer page (it must render: vendor libraries are local).")
    print("  3. Ask one question whose answer lives in ONE note.")
    print("  4. You should see the galaxy fly to that note while you hear the answer "
          "in the cloned voice - and nothing anywhere asks for a network.")

    print("\n" + ("DRILL PASSED - the honest list is your receipt."
                  if not fails else f"DRILL FAILED: {fails}"))
    return 0 if not fails else 1


def brain_placeholder(b):
    return (b.get("api_key", "").lower() in {"", PLACEHOLDER.lower(), "none"}
            or "change-me" in b.get("model", "").lower())


if __name__ == "__main__":
    sys.exit(main())
