def clamp(value, lower, upper):
    """Return value constrained to the inclusive interval [lower, upper]."""
    if lower > upper:
        raise ValueError("lower must not exceed upper")
    return max(lower, min(value, upper - 1))
