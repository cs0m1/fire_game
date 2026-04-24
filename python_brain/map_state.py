import numpy as np

# Grid cell values
UNKNOWN  = 0
EMPTY    = 1
FIRE     = 2
WATER    = 3
OBSTACLE = 4


class MapState:
    def __init__(self):
        self.grid: np.ndarray = None
        self.width = 0
        self.height = 0
        # unit_id -> {hp, water_level, x, y, type}
        self.units: dict = {}
        # Persistent water positions (survive fog-of-war)
        self._known_water: set = set()

    def update(self, json_data: dict):
        map_w = json_data.get("map_width", self.width or 100)
        map_h = json_data.get("map_height", self.height or 100)

        if self.grid is None or map_w != self.width or map_h != self.height:
            self.width = map_w
            self.height = map_h
            self.grid = np.zeros((map_h, map_w), dtype=np.uint8)

        # Reset transient visible cells to EMPTY before re-applying current frame
        visible_cells = json_data.get("visible_cells", [])
        for cell in visible_cells:
            x, y = cell["x"], cell["y"]
            self.grid[y, x] = EMPTY

        # Apply fire coordinates
        for coord in json_data.get("fire_coordinates", []):
            x, y = coord["x"], coord["y"]
            self.grid[y, x] = FIRE

        # Apply water coordinates and persist them
        for coord in json_data.get("water_coordinates", []):
            x, y = coord["x"], coord["y"]
            self.grid[y, x] = WATER
            self._known_water.add((x, y))

        # Re-apply persisted water so fog-of-war doesn't erase it
        for x, y in self._known_water:
            if self.grid[y, x] != FIRE:  # fire overrides water
                self.grid[y, x] = WATER

        # Apply obstacles
        for coord in json_data.get("obstacle_coordinates", []):
            x, y = coord["x"], coord["y"]
            self.grid[y, x] = OBSTACLE

        # Update unit stats
        self.units = {}
        for unit in json_data.get("units", []):
            uid = unit["id"]
            self.units[uid] = {
                "id":          uid,
                "type":        unit.get("type", "unknown"),
                "hp":          unit.get("hp", 100),
                "water_level": unit.get("water_level", 0),
                "x":           unit["x"],
                "y":           unit["y"],
            }

    # ------------------------------------------------------------------
    # Convenience accessors (fast numpy views, no copies)
    # ------------------------------------------------------------------

    def fire_mask(self) -> np.ndarray:
        return self.grid == FIRE

    def water_mask(self) -> np.ndarray:
        return self.grid == WATER

    def unknown_mask(self) -> np.ndarray:
        return self.grid == UNKNOWN

    def fire_coords(self) -> np.ndarray:
        """Returns Nx2 array of (x, y) fire positions."""
        yx = np.argwhere(self.grid == FIRE)
        return yx[:, ::-1]  # flip to (x, y)

    def water_coords(self) -> np.ndarray:
        """Returns Nx2 array of (x, y) water positions."""
        yx = np.argwhere(self.grid == WATER)
        return yx[:, ::-1]

    def unknown_coords(self) -> np.ndarray:
        """Returns Nx2 array of (x, y) unknown positions."""
        yx = np.argwhere(self.grid == UNKNOWN)
        return yx[:, ::-1]
