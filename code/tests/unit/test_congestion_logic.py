# Unit tests for the core congestion scoring logic.
# These test the pure functions that calculate weighted scores and assign tiers.
# No Redis, no network, no Docker just the math that drives the congestion system.

from main import calculate_congestion, get_tier


# Two devices that just pinged — each contributes 1.0, total score should be 2.0
def test_calculate_congestion_fresh_devices():
    now = 1000.0
    entries = [("d1", now), ("d2", now)]
    assert calculate_congestion(entries, now) == 2.0


# Device pinged 150s ago (halfway through the 300s window) — should contribute 0.5
def test_calculate_congestion_old_device():
    now = 1000.0
    entries = [("d1", now - 150)]
    assert calculate_congestion(entries, now) == 0.5


# Device pinged exactly at the window boundary (300s ago) — should contribute 0.0
def test_calculate_congestion_expired_device():
    now = 1000.0
    entries = [("d1", now - 300)]
    assert calculate_congestion(entries, now) == 0.0


# Same device appears twice — only the fresher ping should count, not both
def test_calculate_congestion_duplicate_devices():
    now = 1000.0
    entries = [("d1", now - 100), ("d1", now)]
    assert calculate_congestion(entries, now) == 1.0


# Mix of fresh, stale, duplicate, and expired devices — verifies full scoring logic together
def test_calculate_congestion_mixed():
    now = 1000.0
    entries = [
        ("d1", now),           # weight 1.0
        ("d2", now - 150),     # overridden below
        ("d2", now - 50),      # fresher ping for d2 wins
        ("d3", now - 300),     # expired, contributes 0
    ]
    score = calculate_congestion(entries, now)
    expected = 1.0 + (1.0 - 50 / 300)
    assert abs(score - round(expected, 4)) < 1e-9


# Scores below 5 should return "low"
def test_get_tier_low():
    assert get_tier(0.0) == "low"
    assert get_tier(4.99) == "low"


# Scores between 5 and 20 inclusive should return "moderate"
def test_get_tier_moderate_boundaries():
    assert get_tier(5.0) == "moderate"
    assert get_tier(20.0) == "moderate"


# Scores above 20 should return "high"
def test_get_tier_high():
    assert get_tier(20.01) == "high"
    assert get_tier(100.0) == "high"
