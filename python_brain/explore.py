"""
Explorer AI — role-aware frontier exploration + real-time web visualiser.

Run:   python explore.py
Open:  http://localhost:5000
"""
import grpc, json, math, random, threading, time
from flask import Flask, Response
import fire_ra_pb2, fire_ra_pb2_grpc

SERVER   = "10.4.4.59:5001"
TEAM     = "Meow"
WEB_PORT = 5000

# ── cell types ────────────────────────────────────────────────────────────────
UNKNOWN  = 0
EMPTY    = 1
FIRE     = 2
WATER    = 3
OBSTACLE = 4   # walls, map edges, impassable cells

# ── per-unit-type sight radii (Euclidean cells) ───────────────────────────────
SIGHT = {
    "firefighter": 2,
    "firetruck":   8,
    "firecopter":  16,
}

# precomputed (dx, dy) offsets for each sight radius
_SIGHT_OFFSETS: dict[int, list] = {}

def _offsets_for(r: int) -> list:
    if r not in _SIGHT_OFFSETS:
        _SIGHT_OFFSETS[r] = [
            (dx, dy)
            for dx in range(-r, r + 1)
            for dy in range(-r, r + 1)
            if math.hypot(dx, dy) <= r
        ]
    return _SIGHT_OFFSETS[r]


# ── shared state (guarded by _lock) ──────────────────────────────────────────
_lock         = threading.Lock()
_cells        = {}    # (x, y) → int
_my_units     = {}    # uid → {id, x, y, type, water, hp}
_enemy_units  = {}    # uid → {id, x, y, type, owner}
_visited      = set() # cells our units have physically stood on
_last_cmd     = {}    # uid → (direction, from_x, from_y)  — obstacle detection
_latest_json  = None  # str serialised state for SSE

# ── exploration / AI state (game thread only, no lock needed) ─────────────────
_targets  = {}    # uid → (tx, ty)
_claimed  = set() # frontier cells assigned to a unit
_stale    = {}    # uid → ticks with unchanged position

DIR_DELTA = {"Right": (1, 0), "Left": (-1, 0), "Down": (0, 1), "Up": (0, -1)}

# ── console log timing ────────────────────────────────────────────────────────
_tick      = 0
_start_t   = time.time()


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _parse_coord(obj):
    x = obj.get("X") if obj.get("X") is not None else obj.get("x")
    y = obj.get("Y") if obj.get("Y") is not None else obj.get("y")
    return x, y


def _nearest(ux, uy, candidates):
    if not candidates:
        return None
    return min(candidates, key=lambda p: math.hypot(p[0] - ux, p[1] - uy))


def _step_toward(ux, uy, tx, ty):
    """Return direction string for one step toward (tx, ty)."""
    dx, dy = tx - ux, ty - uy
    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return "Right" if dx > 0 else "Left"
    return "Down" if dy > 0 else "Up"


def _frontier():
    """
    4-neighbours of visited cells that are neither visited nor known obstacles.
    Called from both game and web threads → takes _lock briefly.
    """
    with _lock:
        visited_snap  = frozenset(_visited)
        obstacle_snap = frozenset(p for p, t in _cells.items() if t == OBSTACLE)
    excluded = visited_snap | obstacle_snap
    result = set()
    for x, y in visited_snap:
        for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            if (nx, ny) not in excluded:
                result.add((nx, ny))
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Map update helpers  (all called with _lock already held)
# ═════════════════════════════════════════════════════════════════════════════

def _set_cell_locked(x, y, t):
    """Set cell type; obstacles are permanent, everything else can be updated."""
    if _cells.get((x, y)) == OBSTACLE:
        return
    _cells[(x, y)] = t


def _reveal_sight_locked(unit, fire_set, water_set):
    """
    Mark all cells within this unit's sight radius.
    Cells not in fire_set or water_set are marked EMPTY (fire can be extinguished).
    """
    utype = unit["type"].lower()
    r     = SIGHT.get(utype, 2)
    ux, uy = unit["x"], unit["y"]
    for dx, dy in _offsets_for(r):
        pos = (ux + dx, uy + dy)
        if _cells.get(pos) == OBSTACLE:
            continue  # permanent
        if pos in fire_set:
            _cells[pos] = FIRE
        elif pos in water_set:
            _cells[pos] = WATER
        else:
            _cells[pos] = EMPTY


def _detect_obstacles_locked(units_now):
    """
    If a unit sent a move command but didn't move, mark the destination as OBSTACLE.
    This catches both map edges and impassable terrain.
    """
    for uid, (direction, fx, fy) in list(_last_cmd.items()):
        u = units_now.get(uid)
        if u is None:
            continue
        if u["x"] == fx and u["y"] == fy:
            ddx, ddy = DIR_DELTA[direction]
            bx, by = fx + ddx, fy + ddy
            _cells[(bx, by)] = OBSTACLE


# ═════════════════════════════════════════════════════════════════════════════
# Role-based target assignment
# ═════════════════════════════════════════════════════════════════════════════

def _assign_targets(units_snap):
    """
    Assign each unit a navigation target based on its role:
      firecopter  → nearest unassigned frontier cell (pure explorer)
      firetruck   → refill at water if low, else nearest fire, else frontier
      firefighter → nearest fire if known, else frontier
    """
    front = _frontier()

    # release stale / reached / off-frontier targets
    for uid, tgt in list(_targets.items()):
        u = units_snap.get(uid)
        if u is None or (u["x"], u["y"]) == tgt or tgt not in front:
            _claimed.discard(tgt)
            _targets.pop(uid, None)
            _stale.pop(uid, None)
            continue
        if _stale.get(uid, 0) >= 5:
            _claimed.discard(tgt)
            _targets.pop(uid, None)
            _stale.pop(uid, None)

    available_front = front - _claimed

    with _lock:
        fire_cells  = [p for p, t in _cells.items() if t == FIRE]
        water_cells = [p for p, t in _cells.items() if t == WATER]

    for uid, u in units_snap.items():
        if uid in _targets:
            continue
        ux, uy  = u["x"], u["y"]
        utype   = u["type"].lower()

        if utype == "firecopter":
            tgt = _nearest(ux, uy, available_front)

        elif utype == "firetruck":
            if u["water"] <= 2 and water_cells:
                tgt = _nearest(ux, uy, water_cells)
            elif fire_cells:
                tgt = _nearest(ux, uy, fire_cells)
            else:
                tgt = _nearest(ux, uy, available_front)

        else:  # firefighter
            if fire_cells:
                tgt = _nearest(ux, uy, fire_cells)
            else:
                tgt = _nearest(ux, uy, available_front)

        if tgt is None:
            continue
        _targets[uid] = tgt
        if tgt in available_front:
            _claimed.add(tgt)
            available_front.discard(tgt)


# ═════════════════════════════════════════════════════════════════════════════
# Console logging
# ═════════════════════════════════════════════════════════════════════════════

UNIT_ICON = {"firefighter": "🧑‍🚒", "firetruck": "🚒", "firecopter": "🚁"}

def _console_log(units_snap):
    global _tick
    _tick += 1
    elapsed = int(time.time() - _start_t)

    with _lock:
        n_fire  = sum(1 for t in _cells.values() if t == FIRE)
        n_water = sum(1 for t in _cells.values() if t == WATER)
        n_obs   = sum(1 for t in _cells.values() if t == OBSTACLE)
        n_known = sum(1 for t in _cells.values() if t != UNKNOWN)
        n_enemy = len(_enemy_units)

    front_n = len(_frontier())

    print("\033[2J\033[H", end="", flush=True)
    print(f"╔═ Fire AI Explorer  t={elapsed}s  tick={_tick} {'═'*30}")
    print(f"║ Known:{n_known:5}  Fire:{n_fire:4}  Water:{n_water:4}  "
          f"Obstacles:{n_obs:4}  Frontier:{front_n:4}  Enemies:{n_enemy}")
    print(f"╠{'═'*55}")
    for uid, u in units_snap.items():
        icon  = UNIT_ICON.get(u["type"].lower(), "❓")
        tgt   = _targets.get(uid)
        role  = _role_label(u, tgt)
        stale = _stale.get(uid, 0)
        stale_s = f" [stale:{stale}]" if stale else ""
        print(f"║ {icon} {u['type']:12} #{uid:3}  "
              f"({u['x']:3},{u['y']:3})  HP:{u['hp']:4}  W:{u['water']:3}  "
              f"→ {role}{stale_s}")
    print(f"╚{'═'*55}")


def _role_label(u, tgt):
    utype = u["type"].lower()
    if tgt is None:
        return "random walk"
    with _lock:
        fire_known  = any(t == FIRE  for t in _cells.values())
        water_known = any(t == WATER for t in _cells.values())
    if utype == "firetruck" and u["water"] <= 2 and water_known:
        return f"refilling → {tgt}"
    if utype in ("firetruck", "firefighter") and fire_known:
        return f"fighting fire → {tgt}"
    return f"exploring → {tgt}"


# ═════════════════════════════════════════════════════════════════════════════
# Process server data
# ═════════════════════════════════════════════════════════════════════════════

def _process_units(raw):
    global _latest_json

    with _lock:
        new_my    = {}
        new_enemy = {}

        fire_set  = set()
        water_set = set()

        for u in raw:
            uid   = u["Id"]
            x, y  = u["Position"]["X"], u["Position"]["Y"]
            owner = u.get("Owner", "")
            udata = {
                "id":    uid,
                "type":  u.get("UnitType", "?"),
                "owner": owner,
                "x": x, "y": y,
                "water": u.get("CurrentWaterLevel", 0),
                "hp":    u.get("CurrentHP", 0),
            }
            if owner == TEAM:
                new_my[uid] = udata
                _visited.add((x, y))
                _set_cell_locked(x, y, EMPTY)
            else:
                new_enemy[uid] = udata

        # collect all seen fires/waters from our units
        for u in raw:
            if u.get("Owner") != TEAM:
                continue
            for f in u.get("SeenFires", []):
                fx, fy = _parse_coord(f)
                if fx is not None:
                    fire_set.add((fx, fy))
            for w in u.get("SeenWaters", []):
                wx, wy = _parse_coord(w)
                if wx is not None:
                    water_set.add((wx, wy))

        # obstacle detection from last move commands
        _detect_obstacles_locked(new_my)

        # sight-based cell revelation for each of our units
        for udata in new_my.values():
            _reveal_sight_locked(udata, fire_set, water_set)

        _my_units.clear();    _my_units.update(new_my)
        _enemy_units.clear(); _enemy_units.update(new_enemy)

        # serialise for SSE
        all_x = [x for x, _ in _cells] + [v["x"] for v in _my_units.values()] + [v["x"] for v in _enemy_units.values()]
        all_y = [y for _, y in _cells] + [v["y"] for v in _my_units.values()] + [v["y"] for v in _enemy_units.values()]
        if all_x:
            _latest_json = json.dumps({
                "bounds": {
                    "min_x": min(all_x), "max_x": max(all_x),
                    "min_y": min(all_y), "max_y": max(all_y),
                },
                "cells":       [[x, y, t] for (x, y), t in _cells.items()],
                "my_units":    list(_my_units.values()),
                "enemy_units": list(_enemy_units.values()),
            })


# ═════════════════════════════════════════════════════════════════════════════
# AI generator loop
# ═════════════════════════════════════════════════════════════════════════════

def ai_loop():
    counter  = 0
    prev_pos = {}  # uid → (x, y) last tick

    while True:
        with _lock:
            units_snap = dict(_my_units)

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
            _stale[uid] = _stale.get(uid, 0) + 1 if prev_pos.get(uid) == cur else 0
            prev_pos[uid] = cur

        _assign_targets(units_snap)
        _console_log(units_snap)

        for uid, u in units_snap.items():
            tgt       = _targets.get(uid)
            direction = _step_toward(u["x"], u["y"], *tgt) if tgt else None
            if direction is None:
                direction = random.choice(["Up", "Down", "Left", "Right"])

            with _lock:
                _last_cmd[uid] = (direction, u["x"], u["y"])

            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter,
                unitId=uid, operation=direction, extraJson=""
            )

        time.sleep(0.25)


# ═════════════════════════════════════════════════════════════════════════════
# Game connection
# ═════════════════════════════════════════════════════════════════════════════

def game_main():
    print(f"[Game] Connecting to {SERVER} as '{TEAM}' …")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        r = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"[Game] Hello: {r.message}")
    except grpc.RpcError as e:
        print(f"[Game] SayHello failed: {e.details()}")

    print(f"[Game] AI active — web view: http://localhost:{WEB_PORT}\n")
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
        print("\n[Game] Stopped.")


# ═════════════════════════════════════════════════════════════════════════════
# Web visualiser
# ═════════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Fire AI – Map</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0e0e0e; color: #ddd; font-family: monospace;
       display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

#bar { padding: 6px 14px; background: #161616; border-bottom: 1px solid #2a2a2a;
       display: flex; gap: 20px; align-items: center; font-size: 13px; flex-shrink: 0; }
#bar b  { color: #fff; }
#bar .lbl { opacity: .6; font-size: 11px; }
.stat-fire  { color: #f74; }
.stat-water { color: #59f; }
.stat-obs   { color: #888; }
.stat-enemy { color: #f44; }
#status { margin-left: auto; font-size: 12px; }

canvas { flex: 1; display: block; cursor: crosshair; }

#legend { padding: 5px 12px; background: #121212; border-top: 1px solid #222;
          display: flex; gap: 14px; font-size: 11px; flex-shrink: 0; align-items: center;
          flex-wrap: wrap; }
.swatch { width: 11px; height: 11px; display: inline-block;
          margin-right: 3px; vertical-align: middle; border-radius: 2px; }
.circle { border-radius: 50%; }
#tip { margin-left: auto; opacity: .4; }
</style>
</head>
<body>

<div id="bar">
  <b>🔥 Fire AI</b>
  <span><span class="lbl">known </span><b id="s-known">0</b></span>
  <span><span class="lbl">fire </span><b id="s-fire" class="stat-fire">0</b></span>
  <span><span class="lbl">water </span><b id="s-water" class="stat-water">0</b></span>
  <span><span class="lbl">obstacles </span><b id="s-obs" class="stat-obs">0</b></span>
  <span><span class="lbl">my units </span><b id="s-my">0</b></span>
  <span><span class="lbl">enemies </span><b id="s-enemy" class="stat-enemy">0</b></span>
  <span><span class="lbl">coverage </span><b id="s-cov">—</b></span>
  <span id="status">⏳ connecting…</span>
</div>

<canvas id="c"></canvas>

<div id="legend">
  <span><span class="swatch" style="background:#181818;border:1px solid #333"></span>Unknown</span>
  <span><span class="swatch" style="background:#909090"></span>Empty</span>
  <span><span class="swatch" style="background:#e05020"></span>Fire</span>
  <span><span class="swatch" style="background:#2060e0"></span>Water</span>
  <span><span class="swatch" style="background:#2a2a2a;border:1px solid #555"></span>Obstacle</span>
  <span><span class="swatch circle" style="background:#ffee00"></span>Firefighter</span>
  <span><span class="swatch circle" style="background:#dd44ff"></span>Firetruck</span>
  <span><span class="swatch circle" style="background:#00ff88"></span>Firecopter</span>
  <span><span class="swatch circle" style="background:#ff3333"></span>Enemy</span>
  <span id="tip">scroll=zoom · drag=pan</span>
</div>

<script>
const canvas = document.getElementById('c');
const ctx    = canvas.getContext('2d');

// UNKNOWN EMPTY FIRE WATER OBSTACLE
const CELL_COLOR = ['#181818', '#909090', '#e05020', '#2060e0', '#2a2a2a'];

function myUnitColor(t) {
  t = (t||'').toLowerCase();
  if (t.includes('cop'))   return '#00ff88';
  if (t.includes('truck')) return '#dd44ff';
  return '#ffee00';
}

// viewport
let vx = 0, vy = 0, scale = 8;
let dragging = false, dragX = 0, dragY = 0;
let centred  = false;
let state    = null;

function toCanvas(cx, cy, b) {
  return [vx + (cx - b.min_x) * scale, vy + (cy - b.min_y) * scale];
}

function autoCenter(b) {
  if (centred) return;
  centred = true;
  const W = canvas.offsetWidth, H = canvas.offsetHeight;
  const mW = b.max_x - b.min_x + 1, mH = b.max_y - b.min_y + 1;
  scale = Math.max(2, Math.min(Math.floor(W / mW), Math.floor(H / mH), 20));
  vx = Math.floor((W - mW * scale) / 2);
  vy = Math.floor((H - mH * scale) / 2);
}

function render() {
  if (!state) return;
  const { bounds: b, cells, my_units, enemy_units } = state;
  canvas.width  = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  const W = canvas.width, H = canvas.height;

  ctx.fillStyle = '#0e0e0e';
  ctx.fillRect(0, 0, W, H);

  // cells
  for (const [x, y, t] of cells) {
    const [px, py] = toCanvas(x, y, b);
    if (px < -scale || py < -scale || px > W || py > H) continue; // cull offscreen
    ctx.fillStyle = CELL_COLOR[t] ?? '#444';
    ctx.fillRect(px, py, scale, scale);
  }

  // grid lines if zoomed in enough
  if (scale >= 12) {
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth   = 0.5;
    const [x0] = toCanvas(b.min_x, b.min_y, b);
    const [x1] = toCanvas(b.max_x + 1, b.min_y, b);
    const [,y0] = toCanvas(b.min_x, b.min_y, b);
    const [,y1] = toCanvas(b.min_x, b.max_y + 1, b);
    for (let x = b.min_x; x <= b.max_x + 1; x++) {
      const [px] = toCanvas(x, b.min_y, b);
      ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y1); ctx.stroke();
    }
    for (let y = b.min_y; y <= b.max_y + 1; y++) {
      const [,py] = toCanvas(b.min_x, y, b);
      ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
    }
  }

  // enemy units
  const re = Math.max(2, scale * 0.85);
  for (const u of (enemy_units || [])) {
    const [px, py] = toCanvas(u.x, u.y, b);
    ctx.beginPath();
    ctx.arc(px + scale/2, py + scale/2, re, 0, Math.PI*2);
    ctx.fillStyle   = '#ff3333';
    ctx.fill();
    ctx.strokeStyle = '#ff0000';
    ctx.lineWidth   = 1;
    ctx.stroke();
    // small X
    if (scale >= 8) {
      ctx.strokeStyle = '#fff';
      ctx.lineWidth   = 1;
      const cx = px + scale/2, cy = py + scale/2;
      ctx.beginPath(); ctx.moveTo(cx-2,cy-2); ctx.lineTo(cx+2,cy+2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx+2,cy-2); ctx.lineTo(cx-2,cy+2); ctx.stroke();
    }
  }

  // my units
  const rm = Math.max(2, scale * 1.0);
  for (const u of (my_units || [])) {
    const [px, py] = toCanvas(u.x, u.y, b);
    ctx.beginPath();
    ctx.arc(px + scale/2, py + scale/2, rm, 0, Math.PI*2);
    ctx.fillStyle   = myUnitColor(u.type);
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.6)';
    ctx.lineWidth   = 1;
    ctx.stroke();

    // label at high zoom
    if (scale >= 10) {
      ctx.fillStyle  = 'rgba(0,0,0,0.75)';
      ctx.font       = `${Math.max(7, scale * 0.55)}px monospace`;
      ctx.textAlign  = 'center';
      ctx.fillText(String(u.id), px + scale/2, py + scale/2 + scale * 0.9);
    }
  }

  // stats
  let nfire=0, nwater=0, nobs=0, nknown=0;
  for (const [,,t] of cells) {
    if (t===2) nfire++;  else if (t===3) nwater++; else if (t===4) nobs++;
    if (t>0) nknown++;
  }
  const mW = b.max_x - b.min_x + 1, mH = b.max_y - b.min_y + 1;
  document.getElementById('s-known').textContent = nknown;
  document.getElementById('s-fire').textContent  = nfire;
  document.getElementById('s-water').textContent = nwater;
  document.getElementById('s-obs').textContent   = nobs;
  document.getElementById('s-my').textContent    = (my_units||[]).length;
  document.getElementById('s-enemy').textContent = (enemy_units||[]).length;
  document.getElementById('s-cov').textContent   = (nknown/(mW*mH)*100).toFixed(1) + '%';
}

// SSE
const es = new EventSource('/events');
es.onmessage = e => {
  state = JSON.parse(e.data);
  autoCenter(state.bounds);
  document.getElementById('status').textContent = '🟢 live';
  document.getElementById('status').style.color = '#4f4';
  render();
};
es.onerror = () => {
  document.getElementById('status').textContent = '🔴 disconnected';
  document.getElementById('status').style.color = '#f44';
};

// zoom
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  const f = e.deltaY < 0 ? 1.25 : 0.8;
  vx = e.offsetX - (e.offsetX - vx) * f;
  vy = e.offsetY - (e.offsetY - vy) * f;
  scale = Math.max(1, Math.min(64, scale * f));
  render();
}, { passive: false });

// pan
canvas.addEventListener('mousedown',  e => { dragging=true; dragX=e.clientX; dragY=e.clientY; });
canvas.addEventListener('mousemove',  e => {
  if (!dragging) return;
  vx += e.clientX - dragX; vy += e.clientY - dragY;
  dragX = e.clientX; dragY = e.clientY;
  render();
});
canvas.addEventListener('mouseup',    () => dragging = false);
canvas.addEventListener('mouseleave', () => dragging = false);
window.addEventListener('resize',     render);
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


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    web = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=WEB_PORT, debug=False, threaded=True),
        daemon=True,
    )
    web.start()
    print(f"[Web] Visualiser → http://localhost:{WEB_PORT}")
    game_main()

if __name__ == "__main__":
    main()
