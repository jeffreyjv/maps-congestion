from main import calculate_congestion, get_tier


def test_calculate_congestion_fresh_devices():
    now = 1000.0
    entries = [("d1", now), ("d2", now)]
    assert calculate_congestion(entries, now) == 2.0


def test_calculate_congestion_old_device():
    now = 1000.0
    entries = [("d1", now - 150)]
    assert calculate_congestion(entries, now) == 0.5


def test_calculate_congestion_expired_device():
    now = 1000.0
    entries = [("d1", now - 300)]
    assert calculate_congestion(entries, now) == 0.0


def test_calculate_congestion_duplicate_devices():
    now = 1000.0
    entries = [("d1", now - 100), ("d1", now)]
    assert calculate_congestion(entries, now) == 1.0


def test_calculate_congestion_mixed():
    now = 1000.0
    entries = [
        ("d1", now),
        ("d2", now - 150),
        ("d2", now - 50),
        ("d3", now - 300),
    ]
    score = calculate_congestion(entries, now)
    expected = 1.0 + (1.0 - 50 / 300)
    assert abs(score - round(expected, 4)) < 1e-9


def test_get_tier_low():
    assert get_tier(0.0) == "low"
    assert get_tier(4.99) == "low"


def test_get_tier_moderate_boundaries():
    assert get_tier(5.0) == "moderate"
    assert get_tier(20.0) == "moderate"


def test_get_tier_high():
    assert get_tier(20.01) == "high"
    assert get_tier(100.0) == "high"
