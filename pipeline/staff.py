def classify_staff_by_hsv(mean_hsv: tuple[float, float, float], ranges: list[tuple[int, int, int, int, int, int]]) -> bool:
    h, s, v = mean_hsv
    for h_min, s_min, v_min, h_max, s_max, v_max in ranges:
        if h_min <= h <= h_max and s_min <= s <= s_max and v_min <= v <= v_max:
            return True
    return False
