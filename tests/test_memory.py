#!/usr/bin/env python3
# tests/test_memory.py - the journal: written, reloaded, searchable, wipable.
# Run:  python tests/test_memory.py

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import memory

PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")

# sandbox the journal into a temp dir for the test
import tempfile
tmp = tempfile.mkdtemp(prefix="jarvis-mem-test-")
_ORIG_MEM_DIR, _ORIG_JOURNAL = memory.MEM_DIR, memory.JOURNAL
memory.MEM_DIR = os.path.join(tmp, "memory")
memory.JOURNAL = os.path.join(memory.MEM_DIR, "journal.jsonl")

print("layer 1: writing")
memory.remember_exchange("what are the opening hours", "Seven to six, sir.", "local")
memory.remember_exchange("remember that the beans arrived", "Filed under Beans, sir.", "local")
memory.remember_exchange("weather in cairo", "Cairo is 24C, sir.", "local")
check("three exchanges on disk", len(memory._read_all()) == 3)
check("journal survives as jsonl lines",
      open(memory.JOURNAL, encoding="utf-8").read().count("\n") == 3)

print("layer 2: boot reload")
recent = memory.load_recent(2)
check("reload returns oldest-first roles", [r["role"] for r in recent] == ["user", "assistant"] * 2)
check("reload carries the question text", recent[0]["content"] == "remember that the beans arrived")
check("loaded_count reports", memory.loaded_count() == 2)

print("layer 3: search")
hits = memory.search("cairo")
check("search finds cairo", len(hits) == 1 and "24C" in hits[0]["a"])
check("search is case-insensitive", len(memory.search("CAIRO")) == 1)
check("search misses cleanly", memory.search("unicorns") == [])
spoken = memory.search_spoken("cairo", brain_ask=None)
check("spoken search names the hits", "cairo" in spoken.lower() and "entries" in spoken.lower())

print("layer 4: stats + wipe")
s = memory.stats()
check("stats counts exchanges", s["exchanges"] == 3 and s["size_kb"] > 0)
line = memory.wipe()
check("wipe archives, not deletes", "archived" in line and "journal-archive-" in line)
check("journal starts fresh", memory._read_all() == [])
archives = [f for f in os.listdir(memory.MEM_DIR) if f.startswith("journal-archive-")]
check("the archive exists", len(archives) == 1)

import shutil
shutil.rmtree(tmp)
# hand the module back the way we found it - other suites import this one
memory.MEM_DIR, memory.JOURNAL = _ORIG_MEM_DIR, _ORIG_JOURNAL
print()
if FAIL:
    print(f"MEMORY TEST FAILED - {FAIL} failed, {PASS} passed")
    sys.exit(1)
print(f"MEMORY TEST PASSED - {PASS} checks")
