from datetime import date

from app.limits import DailyQuota, RateLimiter


def test_rate_limiter_counts_each_client_separately():
    limiter = RateLimiter(2)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=1)
    assert not limiter.allow("a", now=2)
    assert limiter.allow("b", now=2)


def test_rate_limiter_window_slides():
    limiter = RateLimiter(1)
    assert limiter.allow("a", now=0)
    assert not limiter.allow("a", now=59)
    assert limiter.allow("a", now=60)


def test_rate_limiter_zero_means_no_limit():
    limiter = RateLimiter(0)
    assert all(limiter.allow("a", now=0) for _ in range(100))


def test_daily_quota_resets_each_day():
    quota = DailyQuota(2)
    day = date(2026, 1, 1)
    assert quota.try_consume(day) and quota.try_consume(day)
    assert not quota.try_consume(day)
    assert quota.try_consume(date(2026, 1, 2))


def test_daily_quota_zero_means_no_cap():
    quota = DailyQuota(0)
    assert all(quota.try_consume(date(2026, 1, 1)) for _ in range(1000))
