from src.ranges import clamp


def test_below_lower():
    assert clamp(-2, 0, 10) == 0


def test_inside_interval():
    assert clamp(4, 0, 10) == 4


def test_inclusive_upper_boundary():
    assert clamp(12, 0, 10) == 10


def test_invalid_interval():
    try:
        clamp(4, 10, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")
