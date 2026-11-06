#!/usr/bin/env python3
# tests/mock_brain.py - a tiny OpenAI-compatible brain for CI / sandbox tests.
# Speaks the EXACT request shape Jarvis sends to Ollama's /v1/chat/completions
# (same body, same extra_body merge, same reply shape) so every chain can be
# proven without downloading a model. Run:  python tests/mock_brain.py

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 11500

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            body = json.dumps({"data": [{"id": "mock-9b"}, {"id": "mock-120b"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_response(404)
            return self.end_headers()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        req = json.loads(raw.decode() or "{}")
        model = req.get("model", "mock-9b")
        # verify the contract Jarvis must respect
        msgs = req.get("messages", [])
        assert msgs and msgs[0]["role"] == "system", "system prompt must come FIRST"
        q = ""
        for m in reversed(msgs):
            if m["role"] == "user":
                c = m["content"]
                if isinstance(c, list):
                    # VISION shape: text part + image part. Verify both, then answer blind.
                    parts = {p.get("type") for p in c}
                    assert "image_url" in parts, "vision message must carry an image part"
                    img = next(p for p in c if p.get("type") == "image_url")
                    url = img["image_url"]["url"]
                    assert url.startswith("data:image/") and ";base64," in url, \
                        "image must be a base64 data URL"
                    assert len(url) > 500, "image payload suspiciously small"
                    q = next((p.get("text", "") for p in c if p.get("type") == "text"), "")
                    q = re.split(r"Question: |Message: ", q)[-1].replace("\n", " ")[:200]
                    ans = ("I see a dark console full of stars, sir - the galaxy itself, "
                           "and one screenshot faithfully delivered.")
                    time.sleep(0.1)
                    body = json.dumps({
                        "id": "chatcmpl-mock", "object": "chat.completion", "model": model,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": ans}}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0}
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                q = c
                break
        # only the actual question (after the payload markers), not the notes
        q = re.split(r"Question: |Message: ", q)[-1].replace("\n", " ")[:200]
        # an answer shaped like the butler, keyed off obvious question words
        if re.search(r"open(ing)? hours|when.*(open|close)", q, re.I):
            ans = ("Monday to Friday seven to six, Saturday eight to five, Sunday nine to three, sir. "
                   "The machine is warm before the door unlocks.")
        elif re.search(r"bean|suppl|deliver", q, re.I):
            ans = ("Harbor Green Trading supplies our green beans, sir; their van lands Tuesday and "
                   "Friday mornings between eight and half nine.")
        elif re.search(r"vault|combination|preflight", q, re.I):
            ans = ("The rehearsal vault combination is 7-3-4, sir. I shall neither ask nor forget.")
        elif re.search(r"milk", q, re.I):
            ans = ("Dairy Fresh delivers Monday and Thursday at 6:15, sir - the crates are in the "
                   "fridge before the door is even unlocked.")
        elif re.search(r"joke|cost", q, re.I):
            ans = ("I cost nothing to run, sir - I live on this machine. That is the joke, and the punchline is your electricity bill.")
        else:
            ans = ("The notes speak of a small roastery run with more care than budget, sir. "
                   "Ask me about the suppliers, the staff, or the menu and I shall be precise.")
        time.sleep(0.15)  # pretend to think
        body = json.dumps({
            "id": "chatcmpl-mock", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": ans}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0}
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print(f"mock brain (OpenAI-compatible) on http://127.0.0.1:{PORT}/v1")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
