#!/usr/bin/env python3
# main.py - start Jarvis. Re-indexes your notes (so edits and captures are
# always fresh), then serves the viewer + brain + voice + ears on 127.0.0.1.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server

if __name__ == "__main__":
    server.main()
