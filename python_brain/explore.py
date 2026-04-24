"""
Explorer AI — frontier-based map exploration + real-time web visualiser.

Run:   python explore.py
Then:  http://localhost:5000
"""
import grpc
import json
import math
import random
import threading
import time

from flask import Flask, Response

import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER   = "10.4.4.59:5001"
TEAM     = "Meow"
WEB_PORT = 5000

# ── cell type constants ───────────────────────────────────────────────────────
UNKNOWN = 0
EMPTY   = 1
FIRE    = 2
WATER   = 3

# ── shared state (all guarded by _lock) ──────────────────────────────────────
_lock        = threading.Lock()
_cells       = {}    # (x, y) → int
_units       = {}    # uid  → {"id","x","y","type","water","hp"}
_visited     = set() # every cell a unit has stood on

# latest serialised state for SSE clients (written by game thread, read by web threads)
_latest_json = None  # str | None

# ── exploration state (game thread only — no lock needed) ─────────────────────
_targets     = {}    # uid → (tx, ty)
_claimed     = set() # frontier cells currently assigned to a unit
_stale       = {}    # uid → ticks with unchanged position


# ─────────────────────────────────── helpers ─────────────────────────────────

def _set_cell(x, y, cell_type):
    """Update a cell; never downgrade a known type."""
    existing = _cells.get((x, y), UNKNOWN)
    if cell_type > existing:
        _cells[(x, y)] = cell_type


def _frontier():
    """4-neighbours of visited cells that are themselves unvisited."""
    with _lock:
        visited_snap = frozenset(_visited)
    result = set()
    for x, y in visited_snap:
        for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            if (nx, ny) not in visited_snap:
                result.add((nx, ny))
    return result


def _step_toward(ux, uy, tx, ty):
    """One-step direction string toward (tx, ty), largest-delta axis first."""
    dx, dy = tx - ux, ty - uy
    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return "Right" if dx > 0 else "Left"
    return "Down" if dy > 0 else "Up"


def _assign_targets(units_snap):
    """
    Frontier BFS assignment: each unit gets the closest unassigned frontier
    cell.  Stale units (haven't moved in 4 ticks) get their target released so
    they can be reassigned to a different cell.
    """
    front = _frontier()

    # release targets that have been reached, gone off-frontier, or stalled
    for uid, tgt in list(_targets.items()):
        u = units_snap.get(uid)
        stale = _stale.get(uid, 0) >= 4
        if u is None or tgt not in front or (u["x"], u["y"]) == tgt or stale:
            _claimed.discard(tgt)
            del _targets[uid]
            _stale.pop(uid, None)

    available = front - _claimed

    # assign closest unassigned frontier cell to each unit that needs one
    for uid, u in units_snap.items():
        if uid in _targets:
            continue
        if not available:
            break
        ux, uy = u["x"], u["y"]
        best = min(available, key=lambda p: math.hypot(p[0]-ux, p[1]-uy))
        _targets[uid] = best
        _claimed.add(best)
        available.discard(best)


# ───────────────────────────── game AI loop ───────────────────────────────────

def _parse_coord(obj):
    """Accept both {"X":…,"Y":…} and {"x":…,"y":…} coordinate dicts."""
    x = obj.get("X") if obj.get("X") is not None else obj.get("x")
    y = obj.get("Y") if obj.get("Y") is not None else obj.get("y")
    return x, y


def _process_units(raw):
    """Parse UnitsFromServer payload and update shared state."""
    global _latest_json

    with _lock:
        _units.clear()
        for u in raw:
            uid = u["Id"]
            x, y = u["Position"]["X"], u["Position"]["Y"]
            _units[uid] = {
                "id":    uid,
                "type":  u.get("UnitType", "?"),
                "x":     x,
                "y":     y,
                "water": u.get("CurrentWaterLevel", 0),
                "hp":    u.get("CurrentHP", 0),
            }
            _visited.add((x, y))
            _set_cell(x, y, EMPTY)

        for u in raw:
            for f in u.get("SeenFires", []):
                fx, fy = _parse_coord(f)
                if fx is not None:
                    _set_cell(fx, fy, FIRE)
            for w in u.get("SeenWaters", []):
                wx, wy = _parse_coord(w)
                if wx is not None:
                    _set_cell(wx, wy, WATER)

        # build SSE payload
        if _cells or _units:
            all_x = [x for x, _ in _cells] + [v["x"] for v in _units.values()]
            all_y = [y for _, y in _cells] + [v["y"] for v in _units.values()]
            _latest_json = json.dumps({
                "bounds": {
                    "min_x": min(all_x), "max_x": max(all_x),
                    "min_y": min(all_y), "max_y": max(all_y),
                },
                "cells": [[x, y, t] for (x, y), t in _cells.items()],
                "units": list(_units.values()),
            })


def ai_loop():
    """Generator: yields CommandMessages to the gRPC stream."""
    counter   = 0
    prev_pos  = {}  # uid → (x, y) from last tick

    while True:
        with _lock:
            units_snap = dict(_units)

        if not units_snap:
            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter,
                unitId=0, operation="NOP", extraJson=""
            )
            time.sleep(0.3)
            continue

        # stagnation tracking
        for uid, u in units_snap.items():
            cur = (u["x"], u["y"])
            if prev_pos.get(uid) == cur:
                _stale[uid] = _stale.get(uid, 0) + 1
            else:
                _stale[uid] = 0
            prev_pos[uid] = cur

        _assign_targets(units_snap)

        for uid, u in units_snap.items():
            tgt = _targets.get(uid)
            if tgt:
                direction = _step_toward(u["x"], u["y"], *tgt)
            else:
                direction = None  # frontier exhausted — random walk

            if direction is None:
                direction = random.choice(["Up", "Down", "Left", "Right"])

            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter,
                unitId=uid, operation=direction, extraJson=""
            )

        time.sleep(0.25)


def game_main():
    print(f"[Game] Connecting to {SERVER} as '{TEAM}' …")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        r = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"[Game] Hello: {r.message}")
    except grpc.RpcError as e:
        print(f"[Game] SayHello failed: {e.details()}")

    print(f"[Game] Exploring — open http://localhost:{WEB_PORT}\n")
    try:
        for msg in stub.CommunicateWithStreams(ai_loop()):
            if msg.operation == "UnitsFromServer" and msg.extraJson:
                try:
                    _process_units(json.loads(msg.extraJson))
                except Exception as e:
                    print(f"[Game] parse error: {e}")
    except grpc.RpcError as e:
        print(f"[Game] Stream ended: {e.details()}")
    except KeyboardInterrupt:
        print("[Game] Stopped.")


# ──────────────────────────────── web server ─────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Fire AI – Map</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #111; color: #ddd; font-family: monospace;
       display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
#bar { padding: 6px 14px; background: #1c1c1c; border-bottom: 1px solid #333;
       display: flex; gap: 18px; align-items: center; font-size: 13px; flex-shrink: 0; }
#bar b { color: #fff; }
#status { margin-left: auto; font-size: 12px; }
canvas { flex: 1; display: block; cursor: crosshair; }
#legend { padding: 4px 12px; background: #181818; border-top: 1px solid #2a2a2a;
          display: flex; gap: 16px; font-size: 11px; flex-shrink: 0; align-items: center; }
.dot { width: 10px; height: 10px; border-radius: 50%;
       display: inline-block; margin-right: 4px; vertical-align: middle; }
#tip { margin-left: auto; opacity: .5; }
</style>
</head>
<body>
<div id="bar">
  <b>🔥 Fire AI Explorer</b>
  <span>Known: <b id="ncells">0</b></span>
  <span>Fire: <b id="nfire" style="color:#e63">0</b></span>
  <span>Water: <b id="nwater" style="color:#69f">0</b></span>
  <span>Units: <b id="nunits">0</b></span>
  <span>Coverage: <b id="cov">—</b></span>
  <span id="status">⏳ connecting…</span>
</div>

<canvas id="c"></canvas>

<div id="legend">
  <span><span class="dot" style="background:#222"></span>Unknown</span>
  <span><span class="dot" style="background:#aaa"></span>Empty</span>
  <span><span class="dot" style="background:#e06030"></span>Fire</span>
  <span><span class="dot" style="background:#3060e0"></span>Water</span>
  <span><span class="dot" style="background:#ff0; border-radius:0"></span>Firefighter</span>
  <span><span class="dot" style="background:#f0f; border-radius:0"></span>Firetruck</span>
  <span><span class="dot" style="background:#0f8; border-radius:0"></span>Firecopter</span>
  <span id="tip">scroll to zoom · drag to pan</span>
</div>

<script>
const canvas = document.getElementById('c');
const ctx    = canvas.getContext('2d');

// colours per cell type
const CELL_COLOR = ['#222', '#a8a8a8', '#e06030', '#3060e0'];

function unitColor(t) {
  t = (t||'').toLowerCase();
  if (t.includes('cop'))   return '#00ff88';
  if (t.includes('truck')) return '#ff44ff';
  return '#ffff00';
}

// viewport state
let vx = 0, vy = 0, scale = 8;
let dragging = false, dragX = 0, dragY = 0;

let state = null;

function cellToCanvas(cx, cy, b) {
  return [
    vx + (cx - b.min_x) * scale,
    vy + (cy - b.min_y) * scale
  ];
}

function render() {
  if (!state) return;
  const { bounds: b, cells, units } = state;
  const W = canvas.width  = canvas.offsetWidth;
  const H = canvas.height = canvas.offsetHeight;

  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, W, H);

  // cells
  for (const [x, y, t] of cells) {
    const [px, py] = cellToCanvas(x, y, b);
    ctx.fillStyle = CELL_COLOR[t] || '#444';
    ctx.fillRect(px, py, scale, scale);
  }

  // unit dots
  const r = Math.max(2, scale * 0.9);
  for (const u of units) {
    const [px, py] = cellToCanvas(u.x, u.y, b);
    ctx.beginPath();
    ctx.arc(px + scale/2, py + scale/2, r, 0, Math.PI*2);
    ctx.fillStyle = unitColor(u.type);
    ctx.fill();
  }

  // stats
  let nfire = 0, nwater = 0, known = 0;
  for (const [,,t] of cells) {
    if (t === 2) nfire++;
    else if (t === 3) nwater++;
    if (t > 0) known++;
  }
  const mapW = b.max_x - b.min_x + 1;
  const mapH = b.max_y - b.min_y + 1;
  document.getElementById('ncells').textContent = known;
  document.getElementById('nfire').textContent  = nfire;
  document.getElementById('nwater').textContent = nwater;
  document.getElementById('nunits').textContent = units.length;
  document.getElementById('cov').textContent    = (known / (mapW * mapH) * 100).toFixed(1) + '%';
}

// auto-centre when first data arrives
let centred = false;
function autoCenter(b) {
  if (centred) return;
  centred = true;
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  const mapW = b.max_x - b.min_x + 1;
  const mapH = b.max_y - b.min_y + 1;
  scale = Math.max(2, Math.min(Math.floor(W / mapW), Math.floor(H / mapH), 24));
  vx = Math.floor((W - mapW * scale) / 2);
  vy = Math.floor((H - mapH * scale) / 2);
}

// SSE
const es = new EventSource('/events');
es.onmessage = e => {
  state = JSON.parse(e.data);
  autoCenter(state.bounds);
  document.getElementById('status').textContent = '🟢 live';
  render();
};
es.onerror = () => {
  document.getElementById('status').textContent = '🔴 disconnected';
};

// zoom
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.25 : 0.8;
  const mx = e.offsetX, my = e.offsetY;
  vx = mx - (mx - vx) * factor;
  vy = my - (my - vy) * factor;
  scale = Math.max(1, Math.min(64, scale * factor));
  render();
}, { passive: false });

// pan
canvas.addEventListener('mousedown', e => { dragging = true; dragX = e.clientX; dragY = e.clientY; });
canvas.addEventListener('mousemove', e => {
  if (!dragging) return;
  vx += e.clientX - dragX; vy += e.clientY - dragY;
  dragX = e.clientX; dragY = e.clientY;
  render();
});
canvas.addEventListener('mouseup',   () => dragging = false);
canvas.addEventListener('mouseleave',() => dragging = false);

window.addEventListener('resize', render);
</script>
</body>
</html>"""

app = Flask(__name__)

@app.route('/')
def index():
    return HTML


@app.route('/events')
def sse_stream():
    def generate():
        last = None
        while True:
            cur = _latest_json
            if cur is not last and cur is not None:
                yield f"data: {cur}\n\n"
                last = cur
            time.sleep(0.15)
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def main():
    web = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=WEB_PORT, debug=False, threaded=True),
        daemon=True,
    )
    web.start()
    print(f"[Web] http://localhost:{WEB_PORT}")
    game_main()


if __name__ == "__main__":
    main()
