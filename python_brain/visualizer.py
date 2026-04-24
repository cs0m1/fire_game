import numpy as np
import cv2
from map_state import UNKNOWN, EMPTY, FIRE, WATER, OBSTACLE

# BGR colors per cell type
COLORS = {
    UNKNOWN:  (40,  40,  40),
    EMPTY:    (180, 180, 180),
    FIRE:     (0,   60,  220),
    WATER:    (200, 120,  0),
    OBSTACLE: (20,  20,  20),
}

MAX_W, MAX_H = 1280, 720
WINDOW = "AI Brain View"


class Visualizer:
    def __init__(self):
        self._ready = False

    def _init_window(self):
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
        self._ready = True

    def render(self, map_state, debug_visuals: dict):
        if map_state.grid is None:
            return

        grid = map_state.grid
        h, w = grid.shape

        # Build BGR image from grid values
        img = np.zeros((h, w, 3), dtype=np.uint8)
        for cell_val, color in COLORS.items():
            img[grid == cell_val] = color

        # Draw fire edges
        for (ex, ey) in debug_visuals.get("fire_edges", []):
            if 0 <= ex < w and 0 <= ey < h:
                img[ey, ex] = (0, 255, 255)  # yellow

        # Scale to fit monitor
        scale = min(MAX_W / w, MAX_H / h, 4.0)
        dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
        img = cv2.resize(img, (dw, dh), interpolation=cv2.INTER_NEAREST)

        # Draw cluster bounding boxes on scaled image
        def to_px(x, y):
            return int(x * scale), int(y * scale)

        for cluster in debug_visuals.get("small_clusters", []):
            if not cluster:
                continue
            pts = np.array(cluster)
            x1, y1 = to_px(pts[:, 0].min(), pts[:, 1].min())
            x2, y2 = to_px(pts[:, 0].max(), pts[:, 1].max())
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 1)

        # Draw largest cluster center
        lc = debug_visuals.get("largest_center")
        if lc:
            cx, cy = to_px(lc[0], lc[1])
            cv2.circle(img, (cx, cy), max(4, int(scale * 2)), (0, 0, 255), -1)

        # Draw small cluster centers
        for sc in debug_visuals.get("small_centers", []):
            cx, cy = to_px(sc[0], sc[1])
            cv2.circle(img, (cx, cy), max(3, int(scale)), (0, 165, 255), -1)

        # Draw units
        for unit in map_state.units.values():
            px, py = to_px(unit["x"], unit["y"])
            utype = unit["type"].lower()
            color = (255, 255, 0) if utype == "drone" else \
                    (255, 0, 255) if utype == "truck" else \
                    (0, 255, 0)
            cv2.circle(img, (px, py), max(3, int(scale)), color, -1)

        if not self._ready:
            self._init_window()

        cv2.imshow(WINDOW, img)
        cv2.waitKey(1)
