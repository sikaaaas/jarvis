# 🔌 The three engines — what they do, what they cost, how to connect them

Jarvis's core (this repo) is Python standard library only and boots with **none** of
these installed. Each engine is optional; each one Jarvis is missing is announced on
the badge with exactly what to install. **This repo never installs them for you** —
you download each from its own official source, so you always know what landed on
your machine.

---

## 🧠 The brain — Ollama (local mode)

| | |
|---|---|
| What it gives Jarvis | a brain that runs on this machine: no bill, no account, no data leaving the house |
| Get it | https://ollama.com/download — installers for Windows, macOS, Linux |
| Download | ~0.6–1 GB installer |
| Disk | ~2–4 GB for the app **before models**; models live in `~/.ollama` (Windows: `C:\Users\you\.ollama`) |
| Models | `ollama pull qwen3.5:9b` ≈ 5–6 GB · a 7–8B 4-bit model ≈ 4–5 GB · a 3–4B ≈ 2–3 GB |

**Connect it:** put the tag on the `local` shelf entry in `config.json`:

```json
"local": { "kind": "openai", "base_url": "http://localhost:11434/v1",
           "model": "qwen3.5:9b", "api_key": "ollama",
           "extra_body": { "reasoning_effort": "none" } }
```

That's the *same* request builder the cloud brains use — which is the whole trick of
the shelf. Other OpenAI-compatible runtimes (LM Studio, llama.cpp-server, vLLM) work
by changing only `base_url`, `model` and `api_key`.

**Picking a tag for your machine** (the honest way, per the pack): run your usual
question, note the seconds printed, swap the tag, run it again. Your notes, your
question, your machine — that benchmark beats any spec sheet.

Rules already handled for you: the system prompt is identical every turn and comes
first (so the model's cache skips re-reading it), no thinking-disabled flag is sent
(`extra_body` does that job), and an empty answer retries once with a bigger budget.

---

## 🎙 The voice — VoiceStudio (cloned voice)

| | |
|---|---|
| What it gives Jarvis | every answer spoken in **your cloned voice**, chunked so the first words land fast and playback never gaps mid-sentence |
| Get it | the VoiceStudio app from its own project page; clone a voice profile |
| Disk | engine-dependent, typically ~1–3 GB with its TTS engine (VoxCPM2 on Windows/NVIDIA, mlx-audio / CSM on Apple Silicon) |

**Connect it:** in `config.json`:

```json
"voice": { "url": "http://127.0.0.1:3900", "engine": "voxcpm2",
           "model_id": "voxcpm2", "profile": "Jarvis", "language": "en" }
```

- `profile` = the **name of the profile you cloned** in VoiceStudio. Name yours `Jarvis`
  or change this line to match.
- Windows + NVIDIA (e.g. an RTX 3050 Ti): `voxcpm2`. Mac: `mlx-audio` with
  `model_id: mlx-community/csm-1b-8bit`.
- Jarvis speaks to it at `POST /v1/audio/speech` (OpenAI shape, WAV back), caches
  rendered audio on disk (`wav-cache/`, keyed by text+profile), and caps requests at
  600 characters — long answers are chunked by sentence, never cut mid-sentence.
- While he speaks he keeps a "keep-warm" line alive so the engine never dozes.

**If it's not running:** the browser's own voice takes over and the badge says
`VOICE browser (fallback)` for 5 seconds. Nothing crashes, nothing pretends.

---

## 👂 The ears — Whisper (local hearing)

| | |
|---|---|
| What it gives Jarvis | hands-free "Jarvis, …" wake word, real transcription on this machine, instant "stop"/"wait" interrupts |
| Get it | `pip install faster-whisper` (Windows/Linux) or `pip install mlx-whisper` (Mac) |
| Download | ~130–200 MB of pip packages |
| Weights | **never fetched on boot.** They land only when you run `python hear.py download`: `large-v3-turbo` ≈ 1.6 GB (the ears) + `small` ≈ 0.5 GB (the wake gate), into the Hugging Face cache. Smaller ears: `medium` ≈ 1.5 GB, `base` ≈ 0.15 GB, `tiny` ≈ 0.08 GB |

**Connect it:** already wired — `hear.py` soft-imports the library and, when the weights
are already on disk, loads BOTH models once at boot and warms each with one second of
silence so the first real command
isn't a cold start. `config.json`:

```json
"hearing": { "model": "large-v3-turbo", "wake_model": "small", "allow_browser_stt": true }
```

How a utterance is judged (all local):

1. Loudness crosses a threshold → recording starts; 700 ms of quiet ends it (`SILENCE_MS`).
2. **Wake mode:** the SMALL model listens first. Utterance must start with "Jarvis" /
   "hey Jarvis" or it is discarded — never logged, never sent anywhere. Name alone →
   "Yes, sir?" and the next utterance is the command.
3. The transcript buffers for 900 ms (`FINISH_MS`) because you pause mid-sentence;
   more speech appends and restarts the timer.
4. While Jarvis speaks, only "stop" / "wait" count, and the mic threshold rises so his
   own voice through your speakers doesn't wake him. Headphones still recommended.

`allow_browser_stt: false` forbids the browser-recogniton fallback entirely — that's
the fully-offline law; with Whisper installed nothing audio-shaped ever leaves the
machine either way.

---

## Optional next step — X-ray vision (REA)

The second pack (*Give Your Jarvis X-Ray Vision*) bolts on website teardowns with the
open-source **REA** kit (`npm install rea-agents`, MIT, needs Node 22.19+ and Chrome,
installed OUTSIDE this project in `~/.jarvis-tools/rea`). It is deliberately **not**
part of this repo yet — its rules (observes, never clicks; clean browser profile per
job, deleted after) deserve their own careful build. Say the word and it can be added
as `teardown.py` following the same soft-fail bay pattern.
