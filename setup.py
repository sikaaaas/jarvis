#!/usr/bin/env python3
# setup.py - first-run setup. The ONLY step that ever creates or fetches
# anything. It never installs a big program: Ollama, VoiceStudio and Whisper
# stay optional, and this file only points at them (see README).
#
#   python setup.py                 # do whatever is missing, then print a report
#   python setup.py --fetch-vendor  # re-download the pinned 3D libraries if broken
#   python setup.py --fresh-notes   # regenerate the 30 sample notes

import json
import os
import shutil
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "viewer", "vendor")
PINNED = [
    ("three.min.js", "https://unpkg.com/three@0.149.0/build/three.min.js", 608081),
    ("3d-force-graph.min.js", "https://unpkg.com/3d-force-graph@1.73.5/dist/3d-force-graph.min.js", 1252161),
]

def step(title):
    print(f"\n--- {title} " + "-" * max(0, 52 - len(title)))

def check_python():
    step("Python")
    v = sys.version_info
    ok = v >= (3, 9)
    print(f"Python {v.major}.{v.minor}.{v.micro} {'OK' if ok else 'TOO OLD - install 3.9 or newer'}")
    return ok

def check_config():
    step("config.json")
    dst = os.path.join(ROOT, "config.json")
    src = os.path.join(ROOT, "config.template.json")
    if os.path.exists(dst):
        print("config.json exists - untouched (your keys stay yours).")
    else:
        shutil.copy(src, dst)
        print("created config.json from the template.")
    print("Cloud brains: open config.json and paste your key(s) over PUT-YOUR-KEY-HERE,")
    print("in YOUR editor. Never in a chat. Local brains work with no key at all.")
    return True

def check_notes(args):
    step("Notes")
    notes = os.path.join(ROOT, "notes")
    fresh = "--fresh-notes" in args
    if not os.path.isdir(notes) or not any(f.endswith(".md") for f in os.listdir(notes)) or fresh:
        print("No notes found - generating 30 sample notes about a small business...")
        gen = os.path.join(ROOT, "tools", "gen_sample_notes.py")
        if os.path.exists(gen):
            return subprocess_call([sys.executable, gen])
        print("sample-note generator missing (tools/gen_sample_notes.py) - add .md files to ./notes")
        return False
    count = sum(1 for r, _d, fs in os.walk(notes) for f in fs if f.endswith(".md"))
    print(f"{count} markdown notes found in ./notes - point config notes_dir elsewhere to use your own.")
    return True

def check_vendor(args):
    step("Viewer libraries (vendored, no CDN)")
    bad = [(fn, url) for fn, url, _size in PINNED
           if not os.path.exists(os.path.join(VENDOR, fn))
           or os.path.getsize(os.path.join(VENDOR, fn)) < 100_000]
    if bad and "--fetch-vendor" not in args:
        for fn, _u in bad:
            print(f"{fn} is missing or broken.")
        print("Run: python setup.py --fetch-vendor   (about 2 MB total - the only download Jarvis ever makes)")
        return False
    if bad:
        os.makedirs(VENDOR, exist_ok=True)
        for fn, url, _size in bad:
            print(f"downloading {fn} ...")
            urllib.request.urlretrieve(url, os.path.join(VENDOR, fn))
        print("done. Recorded in viewer/vendor/VERSIONS.txt.")
    sizes = [(fn, os.path.getsize(os.path.join(VENDOR, fn))) for fn, _u, _s in PINNED]
    print("vendored: " + ", ".join(f"{fn} ({sz // 1024} KB)" for fn, sz in sizes if os.path.exists(os.path.join(VENDOR, fn))))
    return True

def build_index():
    step("Indexing your notes")
    import build
    notes = os.path.join(ROOT, "notes")
    if not os.path.isdir(notes):
        print("no notes folder - skipped (add .md notes, then run again)")
        return False
    n, l = build.write_graph(build.index_notes(notes), os.path.join(ROOT, "viewer", "graph-data.js"))
    print(f"indexed {n} notes, {l} links -> viewer/graph-data.js")
    return True

def diagnose():
    step("The three engines (all optional - see README for each)")
    import importlib.util
    import urllib.parse
    cfg_path = os.path.join(ROOT, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    def probe(url, timeout=2):
        host = urllib.parse.urlparse(url).hostname or ""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    # local brains only - setup never talks to the cloud (that would be an outbound call)
    brain_urls = [b.get("base_url") for b in cfg.get("brains", {}).values()
                  if b.get("base_url") and "localhost" in b.get("base_url")]
    up = [u for u in brain_urls if probe(u.rstrip("/") + "/models")]
    print(f"[{'x' if up else ' '}] BRAIN  local brain  "
          + (f"reachable ({', '.join(u.rstrip('/') for u in up)})" if up
             else "not running   - README: 'The brain' (install: ollama.com/download)"))

    vs = probe(cfg.get("voice", {}).get("url", "http://127.0.0.1:3900") + "/health", timeout=1)
    print(f"[{'x' if vs else ' '}] VOICE  VoiceStudio on :3900  {'READY' if vs else 'not running'}"
          + ("" if vs else "   - README: 'The voice' (cloned voice; falls back to the browser's)"))

    whisper = importlib.util.find_spec("faster_whisper") or importlib.util.find_spec("mlx_whisper")
    print(f"[{'x' if whisper else ' '}] EARS   Whisper importable  {'READY' if whisper else 'not installed'}"
          + ("" if whisper else "   - README: 'The ears' (pip install faster-whisper)"))

    cloud_ready = [n for n, b in cfg.get("brains", {}).items()
                   if "localhost" not in b.get("base_url", "")
                   and b.get("api_key") not in ("", "PUT-YOUR-KEY-HERE")]
    if cloud_ready:
        print(f"Cloud brains with keys: {', '.join(cloud_ready)} - online mode is ready.")
    else:
        print("Online mode: no cloud keys set yet (optional). Local mode needs no key.")

def subprocess_call(cmd):
    import subprocess
    return subprocess.call(cmd) == 0

def main():
    args = sys.argv[1:]
    print("=" * 62)
    print("JARVIS SETUP - the only step that ever creates anything")
    print("=" * 62)
    ok = check_python()
    ok = check_config() and ok
    ok = check_notes(args) and ok
    ok = check_vendor(args) and ok
    ok = build_index() and ok
    diagnose()
    print("\nStart Jarvis with:  python main.py    (or double-click start.bat)")
    print("Then open  http://127.0.0.1:4700  - add  ?mute=1  for silent test tabs.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
