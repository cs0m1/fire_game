def get_direction(unit, tracker, ai):
    if unit["x"] > -10:
        return "Left"
    elif unit["y"] > -10:
        return "Up"
    return "NOP"
