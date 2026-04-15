#!/usr/bin/env python3
"""
SAMC Track Scraper Server — WebSocket Edition.

Usage: python3 server.py [ws_port] [http_port]
Default: ws_port=8900, http_port=8899

  http://localhost:8899/           → Dashboard
  http://localhost:8899/client     → CEF client (load in game)
  ws://localhost:8900              → WebSocket (all real-time comms)
"""

import asyncio
import json
import sys
import os
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

try:
    import websockets
except ImportError:
    print("❌ websockets not installed. Run: pip install websockets")
    sys.exit(1)

WS_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
HTTP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
SCRIPT_DIR = Path(__file__).parent

# ============================================================
#  SHARED STATE — Server is the single source of truth
# ============================================================
state = {
    "recording": False,
    "recording_type": None,       # "track" or "pit"
    "track_points": [],           # [{x, y, z, dist}] — persistent!
    "pit_points": [],             # [{x, y, z}]
    "total_length": 0.0,
    "last_point": None,           # {x, y} for distance filtering
    "track_info": {               # metadata for export
        "track_id": "custom_track",
        "track_name": "Custom Track",
        "total_laps": 0,
    },
}

MIN_DIST = 2.0  # minimum distance filter

# Connected WebSocket clients (role-tagged)
clients = {
    "dashboard": set(),
    "cef": set(),
}


# ============================================================
#  MATH UTILS
# ============================================================
def distance_2d(a, b):
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5


# ============================================================
#  BROADCAST TO CLIENTS
# ============================================================
async def broadcast(msg, role=None):
    """Broadcast JSON message to all clients, or only to a specific role."""
    payload = json.dumps(msg)
    targets = set()
    if role:
        targets = clients.get(role, set()).copy()
    else:
        for s in clients.values():
            targets |= s
    
    dead = set()
    for ws in targets:
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            dead.add(ws)
    
    # Clean up
    for ws in dead:
        for s in clients.values():
            s.discard(ws)


async def send_full_state(ws):
    """Send the complete current state to a newly connected client."""
    await ws.send(json.dumps({
        "type": "full_state",
        "recording": state["recording"],
        "recording_type": state["recording_type"],
        "track_points": state["track_points"],
        "pit_points": state["pit_points"],
        "total_length": state["total_length"],
        "track_info": state["track_info"],
        "cef_alive": len(clients["cef"]) > 0,
    }))


# ============================================================
#  MESSAGE HANDLERS
# ============================================================
async def handle_position(ws, data):
    """CEF sends position data. Server processes, stores, and broadcasts."""
    x = data.get("x", 0)
    y = data.get("y", 0)
    z = data.get("z", 0)
    rec_type = data.get("recording_type")

    point = {"x": x, "y": y}

    # Noise filter
    if state["last_point"] and distance_2d(state["last_point"], point) < MIN_DIST:
        # Still broadcast live position for display
        await broadcast({
            "type": "live_position",
            "x": x, "y": y, "z": z,
        }, role="dashboard")
        return

    if rec_type == "track":
        dist = (state["total_length"] + distance_2d(state["last_point"], point)) if state["last_point"] else 0
        state["total_length"] = dist
        entry = {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2), "dist": round(dist, 2)}
        state["track_points"].append(entry)
        state["last_point"] = point

        await broadcast({
            "type": "track_point",
            "point": entry,
            "total_length": round(dist, 2),
            "count": len(state["track_points"]),
        }, role="dashboard")

    elif rec_type == "pit":
        entry = {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)}
        state["pit_points"].append(entry)
        state["last_point"] = point

        await broadcast({
            "type": "pit_point",
            "point": entry,
            "count": len(state["pit_points"]),
        }, role="dashboard")

    # Always broadcast live position
    await broadcast({
        "type": "live_position",
        "x": x, "y": y, "z": z,
    }, role="dashboard")


async def handle_command(ws, data):
    """Dashboard or CEF sends a command (record_track, record_pit, stop, clear)."""
    cmd = data.get("command")

    if cmd == "record_track":
        state["recording"] = True
        state["recording_type"] = "track"
        state["track_points"] = []
        state["total_length"] = 0.0
        state["last_point"] = None
        print(f"[WS] 🔴 Recording TRACK started")
    
    elif cmd == "record_pit":
        state["recording"] = True
        state["recording_type"] = "pit"
        state["pit_points"] = []
        state["last_point"] = None
        print(f"[WS] 🔴 Recording PIT started")

    elif cmd == "stop":
        state["recording"] = False
        state["recording_type"] = None
        pts = len(state["track_points"])
        pit = len(state["pit_points"])
        print(f"[WS] ⏹ Recording stopped — Track: {pts} pts, Pit: {pit} pts")

    elif cmd == "clear":
        state["recording"] = False
        state["recording_type"] = None
        state["track_points"] = []
        state["pit_points"] = []
        state["total_length"] = 0.0
        state["last_point"] = None
        print(f"[WS] 🗑 Data cleared")

    # Broadcast state change to ALL clients
    await broadcast({
        "type": "state_change",
        "command": cmd,
        "recording": state["recording"],
        "recording_type": state["recording_type"],
        "track_count": len(state["track_points"]),
        "pit_count": len(state["pit_points"]),
        "total_length": round(state["total_length"], 2),
    })


async def handle_track_info(ws, data):
    """Dashboard updates track metadata."""
    info = data.get("info", {})
    state["track_info"].update(info)
    print(f"[WS] ℹ Track info updated: {state['track_info']}")


async def handle_export_request(ws, data):
    """Dashboard requests export — server builds .trackdef from stored data and sends it."""
    tp = state["track_points"]
    pp = state["pit_points"]
    tl = state["total_length"]
    info = state["track_info"]

    if len(tp) < 10:
        await ws.send(json.dumps({
            "type": "export_error",
            "error": "Not enough track points (need at least 10)",
        }))
        return

    # Build bounding box
    xs = [p["x"] for p in tp]
    ys = [p["y"] for p in tp]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    px = (max_x - min_x) * 0.06 or 10
    py = (max_y - min_y) * 0.06 or 10

    # SVG paths
    svg = f'M {tp[0]["x"]:.2f} {tp[0]["y"]:.2f}'
    for p in tp[1:]:
        svg += f' L {p["x"]:.2f} {p["y"]:.2f}'
    svg += ' Z'

    pit_svg = ''
    if pp:
        pit_svg = f'M {pp[0]["x"]:.2f} {pp[0]["y"]:.2f}'
        for p in pp[1:]:
            pit_svg += f' L {p["x"]:.2f} {p["y"]:.2f}'

    sl = tl / 3

    trackdef = {
        "format_version": 1,
        "track_id": info.get("track_id", "custom_track"),
        "track_name": info.get("track_name", "Custom Track"),
        "total_length_m": round(tl, 2),
        "total_laps": info.get("total_laps", 0),
        "bounding_box": {
            "min_x": round(min_x - px, 2),
            "max_x": round(max_x + px, 2),
            "min_y": round(min_y - py, 2),
            "max_y": round(max_y + py, 2),
        },
        "centerline": tp,
        "sectors": [
            {"sector": 1, "start_dist": 0, "end_dist": round(sl, 2)},
            {"sector": 2, "start_dist": round(sl, 2), "end_dist": round(sl * 2, 2)},
            {"sector": 3, "start_dist": round(sl * 2, 2), "end_dist": round(tl, 2)},
        ],
        "pit_lane": pp,
        "finish_line": {"x": tp[0]["x"], "y": tp[0]["y"]},
        "svg_path": svg,
        "pit_svg_path": pit_svg,
    }

    await ws.send(json.dumps({
        "type": "export_trackdef",
        "trackdef": trackdef,
        "filename": f'{info.get("track_id", "custom_track")}.trackdef',
    }))
    print(f"[WS] 💾 Exported trackdef: {len(tp)} track pts, {len(pp)} pit pts")


# ============================================================
#  WEBSOCKET SERVER
# ============================================================
async def ws_handler(ws):
    """Handle a WebSocket connection."""
    role = "dashboard"  # default
    remote = ws.remote_address

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            # Client registration
            if msg_type == "register":
                role = msg.get("role", "dashboard")
                clients.setdefault(role, set()).add(ws)
                print(f"[WS] ✅ {role} connected from {remote}")
                
                # Send full state to newly connected dashboard
                if role == "dashboard":
                    await send_full_state(ws)
                
                # Notify dashboards about CEF status
                if role == "cef":
                    await broadcast({
                        "type": "cef_status",
                        "alive": True,
                    }, role="dashboard")
                continue

            # Route messages
            if msg_type == "position":
                await handle_position(ws, msg)
            elif msg_type == "command":
                await handle_command(ws, msg)
            elif msg_type == "track_info":
                await handle_track_info(ws, msg)
            elif msg_type == "export_request":
                await handle_export_request(ws, msg)
            elif msg_type == "export_svg_request":
                # SVG export — send back raw data for client-side SVG generation
                await ws.send(json.dumps({
                    "type": "export_svg_data",
                    "track_points": state["track_points"],
                    "pit_points": state["pit_points"],
                    "total_length": state["total_length"],
                    "track_id": state["track_info"].get("track_id", "custom_track"),
                }))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        for s in clients.values():
            s.discard(ws)
        print(f"[WS] ❌ {role} disconnected from {remote}")
        
        # Notify about CEF disconnect
        if role == "cef":
            await broadcast({
                "type": "cef_status",
                "alive": len(clients["cef"]) > 0,
            }, role="dashboard")


# ============================================================
#  HTTP SERVER (serves static HTML files only)
# ============================================================
class StaticHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # quiet

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/dashboard":
            self._serve("dashboard.html")
        elif path == "/client":
            self._serve("client.html")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve(self, filename):
        try:
            content = (SCRIPT_DIR / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")


def run_http_server():
    """Run the HTTP server in a separate thread."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), StaticHandler)
    server.serve_forever()


# ============================================================
#  MAIN
# ============================================================
async def main():
    print(f"""
╔══════════════════════════════════════════════════╗
║       🏁 SAMC Track Scraper — WebSocket Ed.     ║
╠══════════════════════════════════════════════════╣
║  Dashboard : http://localhost:{HTTP_PORT}/            ║
║  CEF Client: http://localhost:{HTTP_PORT}/client       ║
║  WebSocket : ws://localhost:{WS_PORT}                 ║
╠══════════════════════════════════════════════════╣
║  ✅ Data persists on server                      ║
║  ✅ Export available anytime after recording      ║
║  ✅ Real-time sync via WebSocket                 ║
╚══════════════════════════════════════════════════╝
    """)

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    print(f"[HTTP] Listening on port {HTTP_PORT}")

    # Start WebSocket server
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        print(f"[WS]   Listening on port {WS_PORT}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Server] Stopped")
