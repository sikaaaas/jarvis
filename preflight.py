#!/usr/bin/env python3
# preflight.py - THE PREFLIGHT. Runs every LIVE chain end to end and prints a
# verdict. Not unit tests, not mocks: real calls against the running system.
# "Done" means preflight passed. Exit non-zero on any failure.
#
# Run me while Jarvis is up:   python preflight.py

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import net

PLACEHOLDER = "PUT-YOUR-KEY-HERE"
results = []

def record(name, ok, note="", warn=False):
    state = "pass" if ok else ("warn" if warn else "fail")
    results.append(state)
    mark = state.upper()
    print(f"[{mark}] {name}" + (f" - {note}" if note else ""))

def _port_cfg():
    p = os.path.join(ROOT, "config.json" if os.path.exists(os.path.join(ROOT, "config.json"))
                     else "config.template.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)

cfg = _port_cfg()
PORT = cfg.get("port", 4700)
BASE = f"http://127.0.0.1:{PORT}"

def get(path, timeout=10):
    status, raw = net.request(BASE + path, timeout=timeout, reason="preflight:loopback")
    return status, raw

def main():
    print("=" * 62)
    print("PREFLIGHT - every live chain, real calls, no mocks")
    print("=" * 62)

    # 1. the server is up and serving the viewer
    try:
        status, raw = get("/")
        ok = status == 200 and b"ForceGraph" in raw
        record("server is up and serving the viewer", ok)
    except Exception as e:                        # noqa: BLE001
        record("server is up and serving the viewer", False, f"start it first: python main.py ({e})")
        return summary()

    # 2. the graph data loads and the node count is greater than zero
    status, raw = get("/graph")
    graph = json.loads(raw.decode()) if status == 200 else {"nodes": []}
    record("graph data loads, node count > 0", status == 200 and len(graph["nodes"]) > 0,
           f"{len(graph['nodes'])} notes, {len(graph['links'])} links")

    # 3. /chat returns a well-formed answer to a real question, with a nodes array
    status, raw = net.request(BASE + "/chat", method="POST",
                              data=json.dumps({"text": "What are our opening hours?"}).encode(),
                              headers={"Content-Type": "application/json"}, timeout=300,
                              reason="preflight:loopback")
    chat = json.loads(raw.decode()) if raw else {}
    well_formed = status == 200 and isinstance(chat.get("answer"), str) and chat["answer"] and \
        isinstance(chat.get("nodes"), list)
    record("/chat returns a well-formed answer with a nodes array", well_formed,
           f"brain={chat.get('brain')}, {chat.get('seconds')}s"
           + (" (placeholder-key dry line - add a key or use a local brain)"
              if well_formed and "no key" in chat.get("answer", "").lower() else ""))

    # 4+5. the key is valid and the model is reachable - only for a configured cloud brain
    current = chat.get("brain") or cfg.get("boot_brain")
    bcfg = cfg.get("brains", {}).get(current, {})
    cloud = not net.is_local(bcfg.get("base_url", ""))
    has_key = bcfg.get("api_key", "") not in ("", PLACEHOLDER)
    if cloud and has_key:
        try:
            url = bcfg["base_url"].rstrip("/")
            if bcfg.get("kind") == "anthropic":
                url += "/v1/models"
                hdrs = {"x-api-key": bcfg["api_key"], "anthropic-version": "2023-06-01"}
            else:
                url += "/models"
                hdrs = {"Authorization": f"Bearer {bcfg['api_key']}"}
            status, raw = net.request(url, headers=hdrs, timeout=30,
                                      reason=f"preflight:models:{current}")
            body = raw.decode("utf-8", "replace")
            reachable = status == 200 and bcfg.get("model", "") in body
            record(f"API key in config is valid ({current})", status == 200,
                   "" if status == 200 else f"HTTP {status}")
            record(f"model '{bcfg.get('model')}' is one the key can reach", reachable,
                   "" if reachable else f"the Models API did not list it - check the id")
        except Exception as e:                    # noqa: BLE001
            record(f"API key / model check ({current})", False, str(e))
    else:
        record("API key validity check", True,
               "skipped: cloud brain has no key yet, or the current brain is local"
               + (" - add your key in config.json to run the real check" if cloud else "(local brain)"),
               warn=True)

    # 6. /remember writes a real file and the note is retrievable, then clean up
    test_text = "preflight test: the rehearsal vault combination is 7-3-4"
    status, raw = net.request(BASE + "/remember", method="POST",
                              data=json.dumps({"text": test_text}).encode(),
                              headers={"Content-Type": "application/json"}, timeout=30,
                              reason="preflight:loopback")
    cap = json.loads(raw.decode()) if raw else {}
    wrote = status == 200 and cap.get("ok") and os.path.exists(
        os.path.join(ROOT, cfg.get("notes_dir", "notes"), cap.get("file", "_none_")))
    record("/remember writes a real markdown file", wrote,
           cap.get("file", "") if wrote else str(cap))
    if wrote:
        status2, raw2 = net.request(BASE + "/chat", method="POST",
                                    data=json.dumps({"text": "What is the rehearsal vault combination?"}).encode(),
                                    headers={"Content-Type": "application/json"}, timeout=300,
                                    reason="preflight:loopback")
        ans = json.loads(raw2.decode()).get("answer", "") if status2 == 200 else ""
        got = "7-3-4" in ans or "734" in ans.replace("-", "")
        record("the new note is retrievable by /chat immediately", got,
               "brain answered from the fresh capture" if got else f"answer was: {ans[:100]}")
        # cleanup: remove the test note and reindex
        try:
            os.remove(os.path.join(ROOT, cfg.get("notes_dir", "notes"), cap.get("file", "")))
            import server
            server.reindex()
        except Exception as e:                    # noqa: BLE001
            record("test note cleanup", False, str(e))

    # 7. the files the browser is SERVED match the files on disk
    mismatch = []
    for rel in ("index.html", "graph-data.js"):
        status, raw = get(f"/{rel}")
        disk = os.path.join(ROOT, "viewer", rel)
        if os.path.exists(disk):
            served = hashlib.sha1(raw).hexdigest()
            local = hashlib.sha1(open(disk, "rb").read()).hexdigest()
            if served != local:
                mismatch.append(rel)
        else:
            mismatch.append(rel + " (missing on disk)")
    record("served files match the files on disk", not mismatch,
           "a stale served file is the most common cause of 'I fixed it but nothing changed'"
           if mismatch else "index.html + graph-data.js are live")

    # 8. config.json is NOT reachable from the browser - this one must fail loudly
    status, _raw = get("/config.json")
    record("config.json is NOT reachable from the browser", status == 404,
           f"GET /config.json returned {status} (must be 404)")

    # 9. the ?mute=1 law: the one speak function refuses when the flag is set
    _, raw = get("/index.html")
    src = raw.decode("utf-8", "replace")
    has_gate = "MUTE" in src and ("mute=1" in src or "mute" in src) and "function speak" in src
    record("?mute=1 tab really is silent (static check: one speak function with the gate)",
           has_gate, "the check lives inside the single speak() in viewer/index.html",
           warn=not has_gate)

    # 10. engine diagnostics - Jarvis may run cloud-only, local-only, or both
    ollama_up = False
    for name, b in cfg.get("brains", {}).items():
        if net.is_local(b.get("base_url", "")):
            try:
                s, _ = net.request(b["base_url"].rstrip("/") + "/models", timeout=2,
                                   reason=f"preflight:ollama:{name}")
                ollama_up = s == 200
            except Exception:
                pass
            break
    record("Ollama (local brain) reachable on its port", ollama_up,
           "start the Ollama app, then: ollama pull <tag from README>" if not ollama_up else "ready",
           warn=not ollama_up)

    vs = False
    try:
        s, _ = net.request(cfg.get("voice", {}).get("url", "http://127.0.0.1:3900") + "/health",
                           timeout=1, reason="preflight:voicestudio")
        vs = s == 200
    except Exception:
        pass
    record("VoiceStudio (local voice) reachable on its port", vs,
           "open the VoiceStudio app to enable the cloned voice" if not vs else "ready",
           warn=not vs)

    whisper = importlib.util.find_spec("faster_whisper") is not None or \
        importlib.util.find_spec("mlx_whisper") is not None
    record("Whisper (local ears) importable", whisper,
           "pip install faster-whisper" if not whisper else "ready",
           warn=not whisper)

    if whisper:
        try:
            import hear as _hear
            _hear.init(cfg.get("hearing", {}))
            _on = _hear.status().get("weights") == "on disk"
            record("Whisper weights on disk (boot never fetches them)", _on,
                   "optional: python hear.py download (~2.1 GB, one explicit command) "
                   "- the browser's ears work without it" if not _on else "ready",
                   warn=not _on)
        except Exception:
            pass

    # 11. the holocam (barehands): vendored, wired, and raisable on command
    bh = os.path.join(ROOT, "barehands", "server.py")
    if not os.path.isfile(bh):
        record("the holocam (barehands) is vendored", False,
               "the barehands folder is missing - re-clone the repo", warn=True)
    else:
        stage_up = False
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8794/orb", timeout=0.8) as r:
                stage_up = r.status == 200
        except Exception:                     # noqa: BLE001
            pass
        record("the holocam (barehands) is vendored and its port is free", not stage_up,
               "already running on 8794 - fine, Jarvis will just open it" if stage_up else
               "dark now; say 'holocam' or 'barehands', or press the HOLOCAM button",
               warn=stage_up)
        status, raw = get("/arms")
        arms_payload = json.loads(raw.decode()) if status == 200 and raw else {}
        row = next((a for a in arms_payload.get("arms", []) if a.get("name") == "holocam"), {})
        record("the holocam arm is wired into the armory", row.get("status") == "ready",
               f"armory says: {row.get('status', 'no row')}")

    if not ollama_up and not (cloud and has_key):
        record("at least one usable brain", False,
               "no cloud key set AND Ollama unreachable - Jarvis has no brain to think with")
    else:
        record("at least one usable brain", True,
               f"currently: {current} ({'cloud' if cloud else 'local'})")

    return summary()

def summary():
    p, w, f = results.count("pass"), results.count("warn"), results.count("fail")
    print(f"\n{p} pass, {f} fail, {w} warn")
    print("Done means preflight passed.")
    return 0 if f == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
