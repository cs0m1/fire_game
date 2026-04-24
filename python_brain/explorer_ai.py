"""
ExplorerAI — role-aware movement decisions for the Fire AI explorer.

Role rules
──────────
  firecopter  pure explorer — always targets the nearest unassigned frontier cell
  firetruck   ① if water ≤ LOW_WATER and water known → go refill
              ② elif fire known → go to nearest fire cell
              ③ else → explore frontier
  firefighter ① if fire known → go to nearest fire cell
              ② else → explore frontier

Stagnation
──────────
  If a unit hasn't moved for STALE_TICKS consecutive ticks its current target
  is released (the MapTracker will have already marked the blocked direction
  as OBSTACLE, so the frontier won't suggest the same dead-end again).

Obstacle detection
──────────────────
  Call record_command(uid, direction, x, y) immediately after yielding each
  move command.  Call process_tick(my_units) at the start of each tick with
  fresh positions; it calls tracker.detect_obstacle() automatically.
"""

import math
import random
from map_tracker import MapTracker, FIRE, WATER

STALE_TICKS = 5
LOW_WATER   = 2

_DIR_DELTA = {"Right": (1, 0), "Left": (-1, 0), "Down": (0, 1), "Up": (0, -1)}
_DIRECTIONS = list(_DIR_DELTA.keys())


def _nearest(ux: int, uy: int, candidates) -> tuple | None:
    if not candidates:
        return None
    return min(candidates, key=lambda p: math.hypot(p[0] - ux, p[1] - uy))


def _step_toward(ux: int, uy: int, tx: int, ty: int) -> str | None:
    """One-step direction toward (tx, ty); largest-delta axis first."""
    dx, dy = tx - ux, ty - uy
    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return "Right" if dx > 0 else "Left"
    return "Down" if dy > 0 else "Up"


class ExplorerAI:
    def __init__(self, tracker: MapTracker):
        self._tracker   = tracker
        self._targets   = {}    # uid → (tx, ty)
        self._claimed   = set() # frontier cells currently assigned to a unit
        self._stale     = {}    # uid → consecutive ticks without movement
        self._prev_pos  = {}    # uid → (x, y) from previous tick
        self._last_cmd  = {}    # uid → (direction, from_x, from_y)

    # ── call these from the main game loop ────────────────────────────────────

    def record_command(self, uid: int, direction: str, x: int, y: int) -> None:
        """
        Record the command just sent to a unit.
        Must be called after every move command so obstacle detection works.
        """
        self._last_cmd[uid] = (direction, x, y)

    def process_tick(self, my_units: dict) -> None:
        """
        Call at the start of each tick with fresh unit positions.
        Updates stagnation counters and runs obstacle detection.
        """
        for uid, u in my_units.items():
            cur  = (u["x"], u["y"])
            prev = self._prev_pos.get(uid)
            self._stale[uid] = (self._stale.get(uid, 0) + 1) if prev == cur else 0
            self._prev_pos[uid] = cur

            if uid in self._last_cmd:
                direction, ox, oy = self._last_cmd[uid]
                self._tracker.detect_obstacle(direction, ox, oy, u["x"], u["y"])

    def get_direction(self, uid: int, unit: dict) -> str:
        """
        Return the next move direction string for this unit.
        Handles target assignment internally; falls back to random walk
        if the frontier is exhausted.
        """
        self._refresh_target(uid, unit)
        tgt = self._targets.get(uid)
        direction = _step_toward(unit["x"], unit["y"], *tgt) if tgt else None
        return direction if direction is not None else random.choice(_DIRECTIONS)

    def current_targets(self) -> dict:
        """Snapshot of {uid: (tx, ty)} for console display."""
        return dict(self._targets)

    # ── internal ──────────────────────────────────────────────────────────────

    def _refresh_target(self, uid: int, unit: dict) -> None:
        front = self._tracker.frontier()
        tgt   = self._targets.get(uid)

        # release target if reached / off-frontier / stale
        if tgt is not None:
            reached    = (unit["x"], unit["y"]) == tgt
            off_front  = tgt not in front
            stale      = self._stale.get(uid, 0) >= STALE_TICKS
            if reached or off_front or stale:
                self._claimed.discard(tgt)
                self._targets.pop(uid, None)
                self._stale.pop(uid, None)
                tgt = None

        if tgt is not None:
            return  # still valid

        # role-based target selection
        available   = front - self._claimed
        fire_cells  = self._tracker.cells_of_type(FIRE)
        water_cells = self._tracker.cells_of_type(WATER)
        ux, uy      = unit["x"], unit["y"]
        utype       = unit["type"].lower()

        if utype == "firecopter":
            new_tgt = _nearest(ux, uy, available)

        elif utype == "firetruck":
            if unit.get("water", 99) <= LOW_WATER and water_cells:
                new_tgt = _nearest(ux, uy, water_cells)
            elif fire_cells:
                new_tgt = _nearest(ux, uy, fire_cells)
            else:
                new_tgt = _nearest(ux, uy, available)

        else:  # firefighter
            if fire_cells:
                new_tgt = _nearest(ux, uy, fire_cells)
            else:
                new_tgt = _nearest(ux, uy, available)

        if new_tgt is None:
            return

        self._targets[uid] = new_tgt
        if new_tgt in available:
            self._claimed.add(new_tgt)
