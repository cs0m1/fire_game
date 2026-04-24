from abc import ABC, abstractmethod


class BaseBrain(ABC):
    @abstractmethod
    def calculate_moves(self, map_state) -> dict:
        """
        Receive the current MapState and return a dict:
          {
            "commands": [
              {"unit_id": int, "action": str, "target_x": int, "target_y": int},
              ...
            ],
            "debug_visuals": { ... }   # optional, brain-specific
          }
        """
