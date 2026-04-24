import math
from .base_brain import BaseBrain


def _nearest(from_x, from_y, coords):
    """Return (x, y) from coords list closest to (from_x, from_y), or None."""
    best, best_dist = None, float("inf")
    for x, y in coords:
        d = math.hypot(x - from_x, y - from_y)
        if d < best_dist:
            best, best_dist = (x, y), d
    return best


class FallbackBrain(BaseBrain):
    def calculate_moves(self, map_state) -> dict:
        commands = []

        unknown = map_state.unknown_coords().tolist()
        fires   = map_state.fire_coords().tolist()
        waters  = map_state.water_coords().tolist()

        for uid, unit in map_state.units.items():
            ux, uy = unit["x"], unit["y"]
            utype  = unit["type"].lower()

            if utype == "drone":
                target = _nearest(ux, uy, unknown)

            elif utype == "truck":
                # Go refill when nearly empty
                if unit["water_level"] <= 2 and waters:
                    target = _nearest(ux, uy, waters)
                else:
                    target = _nearest(ux, uy, fires)

            else:  # firefighter
                target = _nearest(ux, uy, fires)

            if target is None:
                continue

            commands.append({
                "unit_id":  uid,
                "action":   "move_to",
                "target_x": target[0],
                "target_y": target[1],
            })

        return {"commands": commands, "debug_visuals": {}}
