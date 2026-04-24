import math
import numpy as np
from sklearn.cluster import DBSCAN
from scipy.ndimage import convolve
from .base_brain import BaseBrain
from .fallback_brain import FallbackBrain

LAPLACIAN = np.array([[0, 1, 0],
                       [1,-4, 1],
                       [0, 1, 0]], dtype=np.float32)


def _center(points):
    arr = np.array(points)
    return tuple(arr.mean(axis=0).astype(int))


def _nearest_point(from_xy, points):
    fx, fy = from_xy
    best, best_d = None, float("inf")
    for p in points:
        d = math.hypot(p[0] - fx, p[1] - fy)
        if d < best_d:
            best, best_d = p, d
    return best


class MLBrain(BaseBrain):
    def __init__(self):
        self._fallback = FallbackBrain()

    def calculate_moves(self, map_state) -> dict:
        fire_xy = map_state.fire_coords()  # Nx2 (x, y)

        if len(fire_xy) < 2:
            return self._fallback.calculate_moves(map_state)

        # --- DBSCAN clustering ---
        db = DBSCAN(eps=3, min_samples=2).fit(fire_xy)
        labels = db.labels_

        clusters = {}
        for label, point in zip(labels, fire_xy.tolist()):
            if label == -1:
                continue
            clusters.setdefault(label, []).append(point)

        if not clusters:
            return self._fallback.calculate_moves(map_state)

        # Sort clusters by size descending
        sorted_clusters = sorted(clusters.values(), key=len, reverse=True)
        largest = sorted_clusters[0]
        small_clusters = sorted_clusters[1:]

        # --- Fire edge detection via Laplacian on fire mask ---
        fire_mask = map_state.fire_mask().astype(np.float32)
        edge_map  = convolve(fire_mask, LAPLACIAN)
        edge_yx   = np.argwhere(edge_map > 0)
        edge_xy   = edge_yx[:, ::-1].tolist()  # flip to (x, y)

        largest_center = _center(largest)
        small_centers  = [_center(c) for c in small_clusters]

        debug_visuals = {
            "largest_cluster":  largest,
            "small_clusters":   small_clusters,
            "largest_center":   largest_center,
            "small_centers":    small_centers,
            "fire_edges":       edge_xy,
        }

        commands = []
        waters = map_state.water_coords().tolist()

        for uid, unit in map_state.units.items():
            ux, uy   = unit["x"], unit["y"]
            utype    = unit["type"].lower()

            if utype == "drone":
                unknown = map_state.unknown_coords().tolist()
                if not unknown:
                    continue
                import math as _m
                target = min(unknown, key=lambda p: _m.hypot(p[0]-ux, p[1]-uy))

            elif utype == "truck":
                if unit["water_level"] <= 2 and waters:
                    target = min(waters, key=lambda p: math.hypot(p[0]-ux, p[1]-uy))
                elif edge_xy:
                    # Send trucks to nearest edge of a small cluster
                    edge_candidates = edge_xy
                    if small_clusters:
                        small_pts = [p for c in small_clusters for p in c]
                        edge_candidates = [p for p in edge_xy if p in small_pts] or edge_xy
                    target = _nearest_point((ux, uy), edge_candidates)
                else:
                    target = _nearest_point((ux, uy), fire_xy.tolist())

            else:  # firefighter → center of largest cluster
                target = largest_center

            if target is None:
                continue

            commands.append({
                "unit_id":  uid,
                "action":   "move_to",
                "target_x": int(target[0]),
                "target_y": int(target[1]),
            })

        return {"commands": commands, "debug_visuals": debug_visuals}
