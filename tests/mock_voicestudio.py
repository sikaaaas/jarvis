#!/usr/bin/env python3
# tests/mock_voicestudio.py - stands in for the VoiceStudio engine on :3900 so
# the whole voice chain (/engines/select -> /profiles -> /v1/audio/speech ->
# cache) can be proven without the desktop app. Run:  python tests/mock_voicestudio.py

import io
import json
import math
import struct
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 3900


def tone_wav(seconds=1.2, freq=196):
    """A real, playable WAV (soft A3 hum) - starts with RIFF like the real engine."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        frames = bytearray()
        for i in range(int(22050 * seconds)):
            env = min(1, i / 800) * min(1, (22050 * seconds - i) / 800)
            frames += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * freq * i / 22050)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok": true, "engines": ["mock"]}'
            code = 200
        elif self.path == "/profiles":
            body = json.dumps([{"id": "prof_jarvis_01", "name": "Jarvis", "engine": "mock"}]).encode()
            code = 200
        else:
            body, code = b'{"error": "not found"}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/engines/select":
            body, code = b'{"ok": true}', 200
            time.sleep(0.05)
        elif self.path == "/v1/audio/speech":
            body, code = tone_wav(), 200
        else:
            body, code = b'{"error": "not found"}', 404
        self.send_response(code)
        self.send_header("Content-Type", "audio/wav" if code == 200 and self.path == "/v1/audio/speech" else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"mock VoiceStudio on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
