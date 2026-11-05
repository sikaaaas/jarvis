# Skills — the butler's learned hands

This folder holds skills Jarvis forged on request **after you approved them**.

- `skills/_pending/` — drafts. Nothing here runs. Approve in the ARMS panel
  or say "approve skill <name>".
- `skills/<name>.py` + `<name>.json` — armed skills. Run with
  "run skill <name> [args]". Each one is a single `run(args) -> str` function,
  stdlib-only, seatbelt-inspected, 20-second leash.
- `skills/_data/` — the only folder a skill may write into.

Everything in here except this README is gitignored — skills are yours, not
the repo's.
