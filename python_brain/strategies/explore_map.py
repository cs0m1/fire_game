def get_direction(unit, tracker, ai):
    # Fallback to the default AI logic from explorer_ai.py
    return ai.get_direction(unit["id"], unit)
