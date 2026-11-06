# 🦾 The tool armory — setup, commands, and safety rails

The armory gives Jarvis hands. It lives in `arms.py`, is wired into the server
exactly like the voice and ear bays (soft-imported: if the file is missing the
butler simply has no arms and still boots), and every arm reports its real
status in the viewer's **ARMS** panel.

## The ten arms

| Arm | Command examples | Dependency | Where it runs |
|---|---|---|---|
| `web.fetch` | "fetch https://en.wikipedia.org/wiki/Coffee" | stdlib only | server |
| `weather` | "weather in cairo" | stdlib only (open-meteo, keyless) | server |
| `github` | "github repo acme/jarvis" · "github issues acme/jarvis" | stdlib only; your PAT optional | server |
| `eyes` | "what do you see" · "see my screen" · "screenshot" · "watch my screen" · "what happened on my screen" · "stop watching" | `pip install mss pillow` + a vision-capable brain key | server + your cloud brain |
| `hands` | "type hello and press enter" · "press ctrl+s" · "click" · "double-click" · "scroll down" · "open spotify" · "click on the subscribe button" | `pip install pyautogui` **+ gate** | your desktop |
| `browser` | "search for lofi on youtube" · "open the second video" · "open youtube.com" · "read the page" | `pip install playwright` + `playwright install chromium` | your desktop |
| `scribe` | "make a report on quarterly beans" · "make a pdf about roasting" · "make a file called shopping saying milk, eggs" | `pip install reportlab` for PDFs | server (writes to `Desktop/Jarvis/`) |
| `files` | "create a mods folder and write the main file in it" · "read my notes/todo.md" · "open my Downloads folder" — the brain answers with `[[TOOL]]` blocks and the server executes | stdlib only, always on | server (your home shelf + the Jarvis folder) |
| `skills` | "make a skill that …" · "approve skill …" · "run skill …" · "adopt skill from <url>" | stdlib only | server sandbox |
| `holocam` | "barehands" · "holocam" · "open the holocam" · "is barehands running" · "close the holocam" · "put that on the board" | stdlib only (vendored) | 127.0.0.1:8794 + your browser |

### The files arm, precisely

The brain no longer *talks about* files — it answers with tool blocks and the
server does the deed:

```
[[TOOL: {"tool": "write_file", "args": {"path": "~/projects/mods/main.java", "content": "..."}}]]
[[TOOL: {"tool": "read_file", "args": {"path": "~/notes/x.md"}}]]
[[TOOL: {"tool": "list_dir", "args": {"path": "~/Downloads"}}]]
[[TOOL: {"tool": "make_dir", "args": {"path": "~/projects"}}]]
[[TOOL: {"tool": "open_path", "args": {"path": "~/projects"}}]]
```

The server executes each block and hands the brain the receipts (up to three
tool rounds per answer); the viewer shows `HANDS wrote main.java (1.2 KB)`
style badges, and every call is appended to `logs/arms.log`. The leash:

- paths must land **inside your home folder** or the Jarvis folder (`~/Downloads`,
  `%APPDATA%\...`, `~/Documents` — all in; `/etc`, `C:\Windows` — all out)
- `config.json`, `config.template.json`, `logs/` and `.git/` are **private**:
  the brain can never read its own keys, not even by asking
- reads: files up to 2 MB, at most 60,000 chars sent to the brain; writes: up
  to 1 MB per block; listings capped at 400 entries
- no shell, no delete, no execute — exactly five verbs, all standard library

## The watcher — eyes that stay open

Say **"watch my screen"** and Jarvis glances every
`arms.eyes.watch_interval_seconds` (default 15, floor 5) seconds. Each frame
gets a coarse grayscale signature; **only a frame that actually changed** is
sent to the vision brain — an idle screen costs nothing. Changing frames are
described in one sentence and kept in a rolling journal (also appended to
`logs/arms.log`). **"what happened on my screen"** hands the recent
observations to the brain for a two-sentence answer; **"stop watching"** closes
the eyes. The watcher starts only when you ask, and stops if the capture
library reports anything odd — it never runs silently.

## The pointing finger

"click on the subscribe button" works even when there is no CSS selector to
hold: Jarvis takes a screenshot, asks your vision brain for the element's
centre as `{"x": <int>, "y": <int>}` in the image's pixel space, scales the
point back to your real screen resolution, and clicks. If the brain cannot
find the target, he says so and asks you to rephrase — he never clicks a
guess. Subject to the same hands gate as everything else.

## Browser recipes — the YouTube flow

"search for lofi on youtube" (or "search youtube for lofi") runs a real recipe:
open `youtube.com` → wait for `input#search` → fill it → Enter → wait for
`ytd-video-renderer`. Then **"open the second video"** (first..seventh, or
"result 4") clicks the nth result link and speaks its title. Google has the
same recipe (`textarea[name='q']`). The last searched site is remembered, so
"open the third video" knows where to click. Selectors live in
`arms.RECIPES` — add your own sites the same way.

## The scribe — files, reports, PDFs

"make a report on …" / "make a pdf about …" → the current brain drafts a
structured markdown report (title, sections, bullets, bottom line); it is
saved to `Desktop/Jarvis/<slug>.md` and rendered to `<slug>.pdf` with
reportlab when installed. "make a file called X saying …" writes a plain
file. The exact path is spoken back and logged. Scribe respects the
`pc_control.enabled` gate — writing to your desktop is acting on your PC.
The PDF renderer is Latin-script (Helvetica); for other scripts read the
markdown side.

## The memory journal

`memory.py` keeps `memory/journal.jsonl` — one JSON line per exchange
(timestamp, brain, question, answer), gitignored, yours. At boot the last
exchanges are loaded back into the conversation so context survives
restarts. "what did we talk about recently" · "search my memory for X"
(brain-summarised) · "how much do you remember" · "wipe my memory" (archives
the journal with a timestamp, starts fresh — never deletes).

## Enabling the hands (PC control), step by step

1. `pip install pyautogui` (Windows: nothing else; Linux Wayland users: pyautogui
   needs X11 — on Wayland the arm reports its failure honestly instead of acting).
2. In **your editor**, `config.json`: `"arms": { "pc_control": { "enabled": true } }`.
3. Click **ENABLE THE HANDS** in the ARMS panel (one confirmation dialog), or just
   say **"hands on"** — the panel button and the phrase both flip the session flag.
4. Prove it: "type hello". Watch `logs/arms.log` record it.
5. Say **"hands off"** at any moment — it always wins, even mid-task.

The gate is two-key on purpose: the config file (persisted, yours) AND the
per-session "hands on" phrase. A restart always starts with hands locked.

## The eyes, honestly

A screenshot leaves your machine **only** to the brain currently on duty (or the
next configured cloud brain if it refuses) — the same `net.py` door as every
question, logged with reason `arm:eyes`. There is no third service. If no
vision brain has a key, Jarvis keeps the screenshot on disk-less memory and
says so. Images are downscaled to 1280 px / JPEG q72 before upload.

Vision message shapes, both proven by `tests/test_arms.py` and the mock brain:
- `anthropic`: content blocks `image` (base64 source) + `text`
- `openai`-shaped (Gemini/OpenAI/OpenRouter/Ollama-vision): `image_url` data URL + `text`

## The skills contract

When you say "make a skill that …", the **current brain** writes one python file:

```python
def run(args: str) -> str:
    ...
```

The draft is saved to `skills/_pending/` and inspected by a static seatbelt:

- imports refused: `subprocess`, `socket`, `shutil`, `ctypes`, `pickle`,
  `http.server`, `socketserver`, `multiprocessing`, `signal`
- calls refused: `system`, `popen`, `remove`, `unlink`, `rmdir`, `rmtree`,
  `kill`, `terminate`, `eval`, `exec`, `compile`, `__import__`
- source may not mention `config.json`
- must define `run(args)`

**A seatbelt, not a sandbox:** an approved skill runs with your user's rights.
That is exactly why approval is yours alone — the panel shows every pending
draft with APPROVE / REJECT buttons, and the spoken route is
`approve skill <name>`. Armed skills run on a 20-second leash in a worker
thread; a hung skill is abandoned with a dry line, never a crash.

You can also adopt a skill you found yourself:
`adopt skill from https://raw.githubusercontent.com/.../echo.py` — same
inspection, same approval, nothing runs before both.

## The browser bay

One Chromium, one worker thread, driven through a queue (playwright's sync API
is single-threaded by law). `open <url>` navigates, `read the page` returns the
body text. Click/type/scroll/press ride the same queue. If chromium isn't
installed, every browser command answers with the exact install line instead
of an error.

## The holocam — barehands, the hand-tracked board

Press the **HOLOCAM** toggle, the **RAISE THE HOLOCAM** button in the armory
panel, or just say **"barehands"** / **"holocam"**. Jarvis:

1. boots `barehands/server.py` — the vendored copy of
   [jaredrhod/barehands](https://github.com/jaredrhod/barehands), stdlib Python,
   bound to 127.0.0.1:8794, logs to `logs/holocam.log`;
2. opens `http://127.0.0.1:8794/stage.html` in your browser (a blank tab is
   opened with the click and navigated once the server answers, so popup
   blockers stay polite);
3. mirrors his state onto the ring: `thinking` while the brain works, and the
   viewer reports `listening` / `speaking` / `idle` to `POST /arms/ring`, which
   lands in `barehands/state/state` — exactly the upstream protocol;
4. stages his last answer as a glass card on **"put that on the board"**
   (upstream's `present` verb, via the `/cmd` allowlist).

The Jarvis-flavoured `barehands/barehands.json` names the ring JARVIS and
blooms four orbs: **Notes** (the sample gestures course), **Galaxy** (your own
`notes/` folder — your knowledge on glass), **Manual** (these docs), **Props**
(the media airlock). Only files inside the Props folder can ever appear on the
board; that jail is upstream's and is kept intact.

**Honest network note:** the board page loads Google MediaPipe and three.js
from public CDNs on first load. That fetch is made by your *browser*, never by
Jarvis's Python; `net.py`'s log will show nothing, because the server itself
never leaves loopback. Offline after first load depends on your browser cache.

**Licence:** barehands is © Jared Rhodenizer, AGPL-3.0-or-later, vendored
intact with its `LICENSE`, README and TROUBLESHOOTING in `barehands/`.

## What the armory deliberately does NOT do

- **No reverse engineering of binaries.** Skills cannot spawn processes, so
  tools like radare2/ghidra cannot be wrapped from a skill. Jarvis will read
  what text can be read and say no further.
- **No OAuth connectors** (Gmail/Notion/Calendar style). Those hand a third
  party long-lived scopes into your accounts — the opposite of this build's
  one-door law. If you ever want one, it should be an arm you write with your
  own key, reviewed line by line.
- **No background watching.** Every arm acts only on a command you just gave.
  There is no scheduler, no daemon, no listener beyond the wake word you
  already control.

## Files

| Path | Purpose |
|---|---|
| `arms.py` | the whole armory (router, arms, seatbelt, skills) |
| `server.py` | the brain side of the files arm: `execute_tool`, `_safe_path`, the `[[TOOL]]` parser and the tool loop |
| `logs/arms.log` | every hand/skill/arm action, one JSON per line |
| `skills/` | approved skills (`.py` + `.json` meta) — gitignored, yours |
| `skills/_pending/` | drafts awaiting your approval |
| `skills/_data/` | the only folder a skill may write into |
| `viewer/index.html` | the ARMS panel + the HOLOCAM and AGENT chips (right console) |
| `barehands/` | the vendored stage: server, stage.html, media airlock, sample notes, licence |
| `barehands/barehands.json` | the Jarvis flavour: ring name + the four orbs |
| `logs/holocam.log` | the holocam server's own stdout |
