"""
explore.py — entry point for the Fire AI explorer client.

Wires together:
  MapTracker  (map_tracker.py)  — cell state
  ExplorerAI  (explorer_ai.py)  — movement decisions
  WebViz      (web_viz.py)      — real-time browser visualiser
  RawLogger   (raw_logger.py)   — logs every server message to JSONL

Run:   python explore.py
Open:  http://localhost:5000
"""

import grpc
import json
import threading
import time

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'proto'))
import fire_ra_pb2
import fire_ra_pb2_grpc

from map_tracker import MapTracker
from explorer_ai import ExplorerAI
from web_viz     import WebViz
from raw_logger  import RawLogger

import strategies.goto_0_0
import strategies.explore_map
import strategies.up
import strategies.down
import strategies.left
import strategies.right

# Map unit types to their specific AI scripts here!
UNIT_STRATEGIES = {
    "firecopter": strategies.goto_0_0,
    "firetruck": strategies.explore_map,
    "firefighter": strategies.explore_map,
}

SERVER   = "10.4.4.59:5001"
TEAM     = "Prometheus"
WEB_PORT = 5000

# ── shared unit state (guarded by _lock) ─────────────────────────────────────
_lock         = threading.Lock()
_my_units     = {}   # uid → {id, x, y, type, water, hp}
_enemy_units  = {}   # uid → {id, x, y, type, owner}

# ── singletons ────────────────────────────────────────────────────────────────
_tracker = MapTracker()
_ai      = ExplorerAI(_tracker)


# ═════════════════════════════════════════════════════════════════════════════
# State helpers
# ═════════════════════════════════════════════════════════════════════════════

def _parse_coord(obj: dict) -> tuple:
    """Accept {"X":…,"Y":…} and {"x":…,"y":…}."""
    x = obj.get("X") if obj.get("X") is not None else obj.get("x")
    y = obj.get("Y") if obj.get("Y") is not None else obj.get("y")
    return x, y


def _get_state() -> dict:
    """Called by WebViz every frame — assembles the full visualiser payload."""
    snap = _tracker.snapshot()
    with _lock:
        snap["my_units"]    = list(_my_units.values())
        snap["enemy_units"] = list(_enemy_units.values())
    return snap


# ═════════════════════════════════════════════════════════════════════════════
# Process server messages
# ═════════════════════════════════════════════════════════════════════════════

def _process_units(raw: list) -> None:
    """Parse one UnitsFromServer payload and update all shared state."""
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
            _tracker.mark_visited(x, y)
        else:
            new_enemy[uid] = udata

    # collect everything our units currently see
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

    # sight-based cell revelation per unit
    for udata in new_my.values():
        _tracker.reveal(udata, fire_set, water_set)

    # update AI's view of current positions (obstacle detection happens here)
    _ai.process_tick(new_my)

    with _lock:
        _my_units.clear();    _my_units.update(new_my)
        _enemy_units.clear(); _enemy_units.update(new_enemy)


# ═════════════════════════════════════════════════════════════════════════════
# Console status
# ═════════════════════════════════════════════════════════════════════════════

_ICON = {"firefighter": "🧑‍🚒", "firetruck": "🚒", "firecopter": "🚁"}
_tick    = 0
_start_t = time.time()


def _console_status() -> None:
    global _tick
    _tick += 1
    elapsed = int(time.time() - _start_t)
    st = _tracker.stats
    targets = _ai.current_targets()

    with _lock:
        units_snap  = dict(_my_units)
        n_enemy     = len(_enemy_units)

    front_n = len(_tracker.frontier())

    print(f"\033[2J\033[H", end="", flush=True)  # clear terminal
    print(f"╔═ Fire AI  t={elapsed}s  tick={_tick} {'═'*35}")
    print(f"║  Known:{st[1]+st[2]+st[3]+st[4]:5}  "
          f"Fire:{st[2]:4}  Water:{st[3]:4}  "
          f"Obstacles:{st[4]:4}  Frontier:{front_n:4}  Enemies:{n_enemy}")
    print(f"╠{'═'*55}")
    for uid, u in units_snap.items():
        icon  = _ICON.get(u["type"].lower(), "❓")
        utype = u["type"].lower()
        strat = UNIT_STRATEGIES.get(utype, strategies.explore_map)
        
        if strat != strategies.explore_map:
            role = f"→ strategy: {strat.__name__.split('.')[-1]}"
        else:
            tgt   = targets.get(uid)
            role  = f"→ {tgt}" if tgt else "→ random walk"
            
        stale = _ai._stale.get(uid, 0)
        sf    = f" [stale:{stale}]" if stale else ""
        print(f"║ {icon} {u['type']:12}#{uid:3}  "
              f"({u['x']:4},{u['y']:4})  HP:{u['hp']:5}  W:{u['water']:3}  {role}{sf}")
    print(f"╚{'═'*55}")


# ═════════════════════════════════════════════════════════════════════════════
# AI generator (feeds the gRPC stream)
# ═════════════════════════════════════════════════════════════════════════════

def ai_loop():
    counter = 0

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

        _console_status()

        for uid, u in units_snap.items():
            utype = u["type"].lower()
            strategy_module = UNIT_STRATEGIES.get(utype, strategies.explore_map)
            direction = strategy_module.get_direction(u, _tracker, _ai)
            
            _ai.record_command(uid, direction, u["x"], u["y"])

            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter,
                unitId=uid, operation=direction, extraJson=""
            )

        time.sleep(0.25)


# ═════════════════════════════════════════════════════════════════════════════
# Game connection
# ═════════════════════════════════════════════════════════════════════════════

def game_main(logger: RawLogger) -> None:
    print(f"[Game] Connecting to {SERVER} as '{TEAM}' …")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        r = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"[Game] Hello: {r.message}")
    except grpc.RpcError as e:
        print(f"[Game] SayHello failed: {e.details()}")

    print(f"[Game] AI active — http://localhost:{WEB_PORT}")
    try:
        for msg in stub.CommunicateWithStreams(ai_loop()):
            logger.log(msg)  # ← log EVERYTHING before any filtering

            if msg.operation == "UnitsFromServer" and msg.extraJson:
                try:
                    _process_units(json.loads(msg.extraJson))
                except Exception as e:
                    print(f"[Game] parse error: {e}")

    except grpc.RpcError as e:
        print(f"[Game] Stream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\n[Game] Stopped.")
    finally:
        logger.close()


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    logger = RawLogger()                           # raw_YYYYMMDD_HHMMSS.jsonl
    viz    = WebViz(get_state=_get_state)
    viz.start(port=WEB_PORT)
    game_main(logger)


if __name__ == "__main__":
    main()
