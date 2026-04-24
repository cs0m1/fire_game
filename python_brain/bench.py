"""
Quick performance benchmark — measures how long the brain takes per tick.
Run: venv/bin/python bench.py
"""
import time
import random
import numpy as np
from map_state import MapState
from brains.ml_brain import MLBrain
from brains.fallback_brain import FallbackBrain

MAP_W, MAP_H = 100, 100
N_FIRES      = 80
N_TICKS      = 50


def make_fake_state(tick: int) -> dict:
    random.seed(tick)
    fires  = [{"x": random.randint(30, 80), "y": random.randint(30, 80)} for _ in range(N_FIRES)]
    waters = [{"x": random.randint(0, 10),  "y": random.randint(0, 10)}]
    units  = [
        {"id": 1, "type": "drone",       "x": 5,  "y": 5,  "hp": 2000, "water_level": 5},
        {"id": 2, "type": "truck",       "x": 10, "y": 10, "hp": 1500, "water_level": 15},
        {"id": 3, "type": "firefighter", "x": 15, "y": 15, "hp": 1000, "water_level": 0},
    ]
    visible = [{"x": u["x"] + dx, "y": u["y"] + dy}
               for u in units
               for dx in range(-3, 4)
               for dy in range(-3, 4)
               if 0 <= u["x"]+dx < MAP_W and 0 <= u["y"]+dy < MAP_H]
    return {
        "map_width":  MAP_W, "map_height": MAP_H,
        "units":      units,
        "fire_coordinates":     fires,
        "water_coordinates":    waters,
        "obstacle_coordinates": [],
        "visible_cells":        visible,
    }


def bench(brain, label: str):
    ms = MapState()
    times = []
    for tick in range(N_TICKS):
        ms.update(make_fake_state(tick))
        t0 = time.perf_counter()
        brain.calculate_moves(ms)
        times.append((time.perf_counter() - t0) * 1000)

    avg = sum(times) / len(times)
    mx  = max(times)
    mn  = min(times)
    print(f"[{label}]  avg={avg:.2f}ms  min={mn:.2f}ms  max={mx:.2f}ms  ({N_TICKS} ticks, {N_FIRES} fires)")


if __name__ == "__main__":
    print(f"Map: {MAP_W}x{MAP_H}, {N_FIRES} fire cells, {N_TICKS} ticks\n")
    bench(FallbackBrain(), "FallbackBrain")
    bench(MLBrain(),       "MLBrain      ")
