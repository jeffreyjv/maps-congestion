from main import get_tier

def test_tier_low():
    assert get_tier(5) == "low"

def test_tier_moderate():
    assert get_tier(25) == "moderate"

def test_tier_high():
    assert get_tier(75) == "high"