#!/usr/bin/env python3
# ============================================================================
# memory.py - the butler's long-term mind.
#
# Everything Jarvis hears and says is appended to memory/journal.jsonl on
# YOUR disk - plain lines, no database, no cloud. At every boot the last
# exchanges are loaded back into his conversation memory, so a restart is
# just a butler walking back into the room. "search my memory for X" reads
# the journal; "wipe my memory" archives it and starts fresh.
# ============================================================================
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
MEM_DIR = os.path.join(ROOT, "memory")
JOURNAL = os.path.join(MEM_DIR, "journal.jsonl")

_lock = threading.Lock()
_load_count = 0

def _ensure():
    os.makedirs(MEM_DIR, exist_ok=True)

def remember_exchange(question, answer, brain=""):
    """One line on disk per exchange. Never raises - memory never breaks the chat."""
    try:
        _ensure()
        entry = {"ts": round(time.time(), 3), "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "brain": brain, "q": question[:500], "a": answer[:2000]}
        with _lock:
            with open(JOURNAL, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:                                  # noqa: BLE001
        pass

def load_recent(max_exchanges=12):
    """Boot-time: rebuild the conversation memory from the journal.
    Returns a list of {"role","content"} dicts, oldest first."""
    global _load_count
    entries = _read_all()
    entries = entries[-max_exchanges:]
    out = []
    for e in entries:
        if e.get("q"):
            out.append({"role": "user", "content": e["q"]})
        if e.get("a"):
            out.append({"role": "assistant", "content": e["a"]})
    _load_count = len(entries)
    return out

def loaded_count():
    return _load_count

def _read_all():
    if not os.path.exists(JOURNAL):
        return []
    out = []
    try:
        with open(JOURNAL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return out

def search(query, limit=6):
    """Case-insensitive substring search over questions and answers."""
    q = query.lower().strip()
    if not q:
        return []
    hits = []
    for e in _read_all():
        hay = (e.get("q", "") + " \n " + e.get("a", "")).lower()
        if q in hay:
            hits.append(e)
    return hits[-limit:]

def stats():
    entries = _read_all()
    if not entries:
        return {"exchanges": 0, "file": os.path.relpath(JOURNAL, ROOT), "size_kb": 0}
    return {"exchanges": len(entries),
            "first": entries[0].get("iso", ""), "last": entries[-1].get("iso", ""),
            "file": os.path.relpath(JOURNAL, ROOT),
            "size_kb": round(os.path.getsize(JOURNAL) / 1024, 1)}

def recent_spoken(n=5):
    entries = _read_all()
    if not entries:
        return "The journal is empty, sir - every conversation before now is gone with the wind."
    tail = entries[-n:]
    topics = "; ".join(_short(e.get("q", "?")) for e in tail)
    return (f"Since {entries[0].get('iso', '?')} I have kept {len(entries)} exchanges, sir. "
            f"The latest: {topics}.")

def _short(q, n=60):
    q = re.sub(r"\s+", " ", q).strip()
    return f'"{q[:n]}..."' if len(q) > n else f'"{q}"'

def search_spoken(query, brain_ask=None):
    hits = search(query)
    if not hits:
        return (f"Nothing in the journal about \"{query}\", sir. If it was said before "
                f"this memory existed, it is gone.")
    if brain_ask:
        lines = "\n".join(f"[{e.get('iso','')}] Q: {e.get('q','')}\nA: {e.get('a','')[:400]}"
                          for e in hits)
        text, err = brain_ask([{"role": "user", "content":
                                f"These are past conversations with your employer, from your journal.\n\n"
                                f"{lines}\n\nSummarise in two sentences what was said or decided about "
                                f"'{query}', addressing them as 'sir'. If the excerpts disagree, say so."}])
        if not err and text:
            return text
    return (f"{len(hits)} entries in the journal mention \"{query}\", sir. "
            + " ".join(f"[{e.get('iso','')}] {_short(e.get('q',''), 50)}" for e in hits[-3:]))

def wipe():
    """Archive the journal (never delete history outright) and start fresh."""
    _ensure()
    if not os.path.exists(JOURNAL):
        return "The journal is already empty, sir."
    archive = os.path.join(MEM_DIR, f"journal-archive-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    os.replace(JOURNAL, archive)
    return (f"Done, sir. The journal is archived as {os.path.basename(archive)} and I begin "
            f" anew. For the record: I remember being asked to forget.")
