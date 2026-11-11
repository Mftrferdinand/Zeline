"""Tests for the webhook gateway per-IP rate limiter."""
from zeline.gateways.webhook import _RateLimiter, RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS


def test_allows_up_to_limit():
    limiter = _RateLimiter(3, 60.0)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False


def test_identities_are_independent():
    limiter = _RateLimiter(1, 60.0)
    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True


def test_window_expiry_restores_access():
    limiter = _RateLimiter(1, 60.0)
    assert limiter.allow("5.5.5.5") is True
    assert limiter.allow("5.5.5.5") is False
    # Simulate the window having fully elapsed.
    limiter._hits["5.5.5.5"] = [limiter._hits["5.5.5.5"][0] - RATE_LIMIT_WINDOW_SECONDS - 1]
    assert limiter.allow("5.5.5.5") is True


def test_defaults_are_sane():
    assert RATE_LIMIT_MAX_REQUESTS >= 10
    assert RATE_LIMIT_WINDOW_SECONDS >= 30


def test_map_prunes_expired_identities():
    limiter = _RateLimiter(1, 60.0)
    for i in range(2000):
        limiter.allow(f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}")
    assert len(limiter._hits) == 2000  # all fresh: kept (bounded per entry)
    # Expire everything, then one more request triggers the prune.
    for key in list(limiter._hits):
        limiter._hits[key] = [limiter._hits[key][0] - RATE_LIMIT_WINDOW_SECONDS - 1]
    limiter.allow("10.9.9.9")
    assert len(limiter._hits) == 1
