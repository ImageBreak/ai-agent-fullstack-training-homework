from app.session import is_session_valid, refresh_expiry


def test_before_expiry():
    assert is_session_valid(99, 100)


def test_at_expiry():
    assert not is_session_valid(100, 100)


def test_after_expiry():
    assert not is_session_valid(101, 100)


def test_refresh_extends_ttl():
    assert refresh_expiry(100) == 100 + 30 * 60
