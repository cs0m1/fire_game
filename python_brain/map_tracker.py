"""
MapTracker — thread-safe map state for the Fire AI explorer.

Cell semantics
──────────────
  OBSTACLE (4)  permanent  — walls, map edges, impassable terrain
  WATER    (3)  persistent — stays even when out of sight
  FIRE     (2)  transient  — removed if a unit can see that cell but it's not on fire
  EMPTY    (1)  revealed clear cell
  UNKNOWN  (0)  not yet seen by any unit

Update rules (applied every tick for each visible cell):
  ┌─────────────────────────────┬────────────┐
  │ situation                   │ result     │
  ├─────────────────────────────┼────────────┤
  │ current = OBSTACLE          │ no change  │
  │ pos in fire_set             │ FIRE       │
  │ pos in water_set            │ WATER      │
  │ current = FIRE  (not in fs) │ EMPTY      │ ← fire extinguished
  │ current = UNKNOWN           │ EMPTY      │ ← newly seen
  │ current = EMPTY / WATER     │ no change  │
  └─────────────────────────────┴────────────┘

Obstacle detection:
  If a unit sent move command D but didn't move → cell in direction D = OBSTACLE
"""

import math
import threading

# ── cell type constants (import these in other modules) ───────────────────────
UNKNOWN  = 0
EMPTY    = 1
FIRE     = 2
WATER    = 3
OBSTACLE = 4

CELL_NAMES = {UNKNOWN: "unknown", EMPTY: "empty", FIRE: "fire",
              WATER: "water", OBSTACLE: "obstacle"}

# ── per-unit-type sight radii (Euclidean cells) ───────────────────────────────
SIGHT_RADIUS = {
    "firefighter": 2,
    "firetruck":   8,
    "firecopter":  16,
}

_DIR_DELTA = {"Right": (1, 0), "Left": (-1, 0), "Down": (0, 1), "Up": (0, -1)}


class MapTracker:
    def __init__(self):
        self._lock        = threading.Lock()
        self._cells       = {}    # (x, y) → int
        self._visited     = set() # cells our units have physically stood on
        self._sight_cache = {}    # radius → [(dx, dy), ...]

    # ── sight-offset cache ────────────────────────────────────────────────────

    def _offsets(self, r: int) -> list:
        """Euclidean-radius offsets, precomputed once per radius."""
        if r not in self._sight_cache:
            self._sight_cache[r] = [
                (dx, dy)
                for dx in range(-r, r + 1)
                for dy in range(-r, r + 1)
                if math.hypot(dx, dy) <= r
            ]
        return self._sight_cache[r]

    # ── public API ────────────────────────────────────────────────────────────

    def mark_visited(self, x: int, y: int):
        """Record that one of our units physically stood on this cell."""
        with self._lock:
            self._visited.add((x, y))
            if self._cells.get((x, y)) != OBSTACLE:
                self._cells[(x, y)] = EMPTY

    def reveal(self, unit: dict, fire_set: set, water_set: set):
        """
        Apply one tick of sight-based revelation for a single unit.

        unit      — {"type": str, "x": int, "y": int, ...}
        fire_set  — set of (x, y) the unit currently sees as fire
        water_set — set of (x, y) the unit currently sees as water
        """
        utype  = unit["type"].lower()
        r      = SIGHT_RADIUS.get(utype, 2)
        ux, uy = unit["x"], unit["y"]

        with self._lock:
            for dx, dy in self._offsets(r):
                pos = (ux + dx, uy + dy)
                cur = self._cells.get(pos, UNKNOWN)

                if cur == OBSTACLE:
                    continue                    # obstacles are permanent

                if pos in fire_set:
                    self._cells[pos] = FIRE
                elif pos in water_set:
                    self._cells[pos] = WATER
                elif cur == FIRE:
                    self._cells[pos] = EMPTY   # fire was extinguished
                elif cur == UNKNOWN:
                    self._cells[pos] = EMPTY   # newly seen cell
                # EMPTY stays EMPTY, WATER stays WATER

    def detect_obstacle(self, direction: str, old_x: int, old_y: int,
                        new_x: int, new_y: int):
        """
        Call after receiving a unit's new position.
        If the unit didn't move despite being commanded to, mark the cell
        it tried to enter as OBSTACLE.
        """
        if direction not in _DIR_DELTA:
            return
        if old_x == new_x and old_y == new_y:
            ddx, ddy = _DIR_DELTA[direction]
            bx, by   = old_x + ddx, old_y + ddy
            with self._lock:
                self._cells[(bx, by)] = OBSTACLE

    def frontier(self) -> set:
        """
        BFS frontier: 4-neighbours of visited cells that are neither
        visited nor known obstacles.

        This is the set of candidate cells for the AI to target next.
        """
        with self._lock:
            visited   = frozenset(self._visited)
            obstacles = frozenset(p for p, t in self._cells.items() if t == OBSTACLE)
        excluded = visited | obstacles
        result   = set()
        for x, y in visited:
            for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                if (nx, ny) not in excluded:
                    result.add((nx, ny))
        return result

    def cells_of_type(self, cell_type: int) -> list:
        """Return all (x, y) positions of a given cell type."""
        with self._lock:
            return [pos for pos, t in self._cells.items() if t == cell_type]

    # ── snapshots ─────────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Cell-type counts, cheap."""
        with self._lock:
            counts = {UNKNOWN: 0, EMPTY: 0, FIRE: 0, WATER: 0, OBSTACLE: 0}
            for t in self._cells.values():
                counts[t] = counts.get(t, 0) + 1
        return counts

    def snapshot(self) -> dict:
        """
        JSON-serialisable snapshot suitable for the web visualiser.
        Returns bounding box + flat [[x, y, type], ...] cell list.
        """
        with self._lock:
            cells = dict(self._cells)

        if not cells:
            return {"bounds": {"min_x": 0, "max_x": 0,
                               "min_y": 0, "max_y": 0}, "cells": []}

        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        return {
            "bounds": {
                "min_x": min(xs), "max_x": max(xs),
                "min_y": min(ys), "max_y": max(ys),
            },
            "cells": [[x, y, t] for (x, y), t in cells.items()],
        }
