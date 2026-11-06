#!/usr/bin/env python3
# build.py - the indexer. Python 3, standard library only.
# Scans every .md file in the notes directory and writes viewer/graph-data.js
# containing:  const GRAPH = {nodes: [...], links: [...]}
# Each node: a label from the filename, its parent folder as a group, roughly a
# 700-character excerpt. Two notes are linked when one mentions the other's
# title, or when they share [[wikilinks]]. CRITICAL: every node's numeric id
# equals its index in the nodes array - later features look nodes up by index.

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXCERPT_CHARS = 700          # roughly a 700-character excerpt, per the brief
MIN_TITLE_TOKENS = 3         # titles shorter than this are too generic to link on

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
WORD_RE = re.compile(r"[a-z0-9']{3,}")

STOPWORDS = set("""the and for that with this from have has was were are our out not you your
their there here about into over under then than them they its it's all also can will just
been being what when where which who whom why how does did done make made take took get got
note notes week weeks month months year years day days time times very much many more most
some such only same so no nor too very own one two three new now after before between during
each few other same
""".split())

def _stem(w):
    # light stemming so "saturdays" meets "saturday" and "opening" meets "open".
    # Applied to BOTH the index and the question, so plain intersections work.
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w

def tokens(text):
    out = set()
    for w in WORD_RE.findall(text.lower()):
        if w in STOPWORDS:
            continue
        out.add(w)
        s = _stem(w)
        if s and s not in STOPWORDS:
            out.add(s)
    return out

def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "note"

def read_note(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    body = raw.strip()
    # A leading markdown heading is the label; drop it from the excerpt.
    body_no_h1 = re.sub(r"^#\s+.+?\n+", "", body, count=1)
    excerpt = re.sub(r"\s+", " ", body_no_h1).strip()[:EXCERPT_CHARS]
    wikilinks = [w.strip() for w in WIKILINK_RE.findall(body)]
    return body, excerpt, wikilinks

def index_notes(notes_dir):
    """Scan every .md file. Returns {'nodes': [...], 'links': [...], 'paths': [...]}.
    Deterministic: same files -> same ids across rebuilds."""
    nodes, paths = [], []
    for dirpath, dirnames, filenames in os.walk(notes_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, notes_dir)
            paths.append((full, rel))
    paths.sort(key=lambda p: p[1].lower())

    for full, rel in paths:
        try:
            body, excerpt, wikilinks = read_note(full)
        except OSError:
            continue
        label = os.path.splitext(os.path.basename(rel))[0].replace("-", " ").replace("_", " ").strip()
        label = label[:1].upper() + label[1:]
        group = os.path.dirname(rel).split(os.sep)[0] if os.path.dirname(rel) else "notes"
        nodes.append({
            "id": len(nodes),                 # CRITICAL: id == index
            "label": label,
            "group": group,
            "file": rel.replace(os.sep, "/"),
            "excerpt": excerpt,
            "_title_toks": tokens(label),
            "_body_toks": tokens(body),
            "_body_text": body.lower(),
            "_wikilinks": [w.lower() for w in wikilinks],
        })

    links = find_links(nodes)
    return {"nodes": nodes, "links": links, "paths": [p[1] for p in paths]}

def find_links(nodes):
    """Link two notes when one mentions the other's title, or they share wikilinks.
    Used by build at index time AND by the server when a note is born live."""
    links, seen = [], set()

    def add(a, b, why):
        if a == b:
            return
        key = (min(a, b), max(a, b))
        if key in seen:
            return
        seen.add(key)
        links.append({"source": a, "target": b, "why": why})

    by_slug = {slugify(n["label"]): n["id"] for n in nodes}

    for n in nodes:
        # mentions of another note's title in this note's body, or its wikilinks
        for other in nodes:
            if other["id"] == n["id"]:
                continue
            toks = other["_title_toks"]
            if len(toks) >= MIN_TITLE_TOKENS:
                hits = len(toks & n["_body_toks"])
                if hits >= max(2, len(toks) // 2):
                    add(n["id"], other["id"], "mention")
            elif other["label"].lower() in n["_body_text"]:
                # short titles are too generic for token overlap; require exact mention
                add(n["id"], other["id"], "mention")
        for w in n["_wikilinks"]:
            wslug = slugify(w)
            if wslug in by_slug and by_slug[wslug] != n["id"]:
                add(n["id"], by_slug[wslug], "wikilink")

    return links

def strip_private(nodes, links):
    """Drop the _private scoring fields; the viewer never sees them."""
    clean = []
    for n in nodes:
        clean.append({k: v for k, v in n.items() if not k.startswith("_")})
    return clean, links

def write_graph(indexed, out_path):
    nodes, links = strip_private(indexed["nodes"], indexed["links"])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// Generated by build.py - do not edit by hand.\n")
        f.write("const GRAPH = ")
        f.write(json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False))
        f.write(";\n")
    return len(nodes), len(links)

def main(notes_dir=None, quiet=False):
    notes_dir = os.path.join(ROOT, notes_dir) if notes_dir and not os.path.isabs(notes_dir) else (notes_dir or os.path.join(ROOT, "notes"))
    if not os.path.isdir(notes_dir):
        print(f"build.py: notes folder not found at {notes_dir}", file=sys.stderr)
        return 1
    indexed = index_notes(notes_dir)
    out = os.path.join(ROOT, "viewer", "graph-data.js")
    n, l = write_graph(indexed, out)
    if not quiet:
        print(f"build.py: indexed {n} notes, {l} links -> {os.path.relpath(out, ROOT)}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
