#!/usr/bin/env python3
# net.py - the one door for outbound HTTP.
# Every connection Jarvis's server opens goes through request() and is logged
# with the host and the reason. drill.py reads that log and passes only when
# every host is localhost. Nothing else in this project opens a socket.

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "outbound.log")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}

_lock = threading.Lock()

def _log(entry):
    os.makedirs(LOG_DIR, exist_ok=True)
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def is_local(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return host.lower() in LOCAL_HOSTS

def request(url, method="GET", data=None, headers=None, timeout=10, reason="unlabeled"):
    """One logged HTTP request. Returns (status:int, body:bytes).
    Raises urllib.error.URLError / OSError on connection problems."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "?"
    _log({"ts": round(time.time(), 3), "host": host, "port": parsed.port,
          "reason": reason, "method": method, "path": parsed.path})
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:      # a real HTTP answer is still an answer
        return e.code, e.read()

def new_entries_since(offset):
    """New log lines written after byte offset `offset`. Returns (entries, new_offset)."""
    if not os.path.exists(LOG_PATH):
        return [], os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    with open(LOG_PATH, "rb") as f:
        f.seek(offset)
        chunk = f.read().decode("utf-8", "replace")
    entries = [json.loads(line) for line in chunk.splitlines() if line.strip().startswith("{")]
    return entries, os.path.getsize(LOG_PATH)
