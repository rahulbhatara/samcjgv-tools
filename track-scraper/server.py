#!/usr/bin/env python3
"""
SAMC Track Scraper Server — Simple HTTP bridge.

Usage: python3 server.py [port]
Default port: 8899

  http://localhost:8899/           → Dashboard
  http://localhost:8899/client     → CEF client (load in game)
  POST /api/position              → CEF sends position data
  POST /api/command               → Dashboard sends commands
  GET  /api/state                 → Dashboard polls current state
"""

import json
import sys
import os
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
SCRIPT_DIR = Path(__file__).parent

# Shared state (thread-safe via GIL for simple reads/writes)
state = {
    "recording": False,
    "recording_type": None,    # "track" or "pit"
    "positions": [],           # latest batch of positions [{x, y, z, recording_type}]
    "command": None,           # latest command from dashboard
    "cef_alive": False,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet logging
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html_response(self, filepath):
        try:
            content = (SCRIPT_DIR / filepath).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors()
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/dashboard":
            self._html_response("dashboard.html")
        elif path == "/client":
            self._html_response("client.html")
        elif path == "/api/state":
            # Dashboard polls this — return positions + drain them
            positions = list(state["positions"])
            state["positions"] = []
            self._json_response({
                "recording": state["recording"],
                "recording_type": state["recording_type"],
                "positions": positions,
                "cef_alive": state["cef_alive"],
            })
        elif path == "/api/command":
            # CEF polls this for commands
            cmd = state["command"]
            state["command"] = None  # drain
            self._json_response({"command": cmd})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "invalid json"}, 400)
            return

        if path == "/api/position":
            # CEF sends position data here
            state["cef_alive"] = True
            state["positions"].append({
                "x": data.get("x", 0),
                "y": data.get("y", 0),
                "z": data.get("z", 0),
                "recording_type": data.get("recording_type"),
            })
            # Keep max 500 buffered positions
            if len(state["positions"]) > 500:
                state["positions"] = state["positions"][-500:]
            self._json_response({"ok": True})

        elif path == "/api/command":
            # Dashboard sends commands here (record_track, record_pit, stop)
            cmd = data.get("command")
            state["command"] = cmd
            state["recording"] = cmd in ("record_track", "record_pit")
            state["recording_type"] = cmd.replace("record_", "") if cmd and cmd.startswith("record_") else None
            self._json_response({"ok": True})

        elif path == "/api/scoreboard":
            # CEF forwards raw scoreboard data
            state["cef_alive"] = True
            self._json_response({"ok": True})

        else:
            self._json_response({"error": "not found"}, 404)


def main():
    print(f"""
╔══════════════════════════════════════════════╗
║       🏁 SAMC Track Scraper Server          ║
╠══════════════════════════════════════════════╣
║  Dashboard : http://localhost:{PORT}/          ║
║  CEF Client: http://localhost:{PORT}/client     ║
╚══════════════════════════════════════════════╝
    """)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[Server] Listening on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Stopped")


if __name__ == "__main__":
    main()
