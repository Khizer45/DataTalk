import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone


class RateLimiter:
    """Per-client sliding one-minute window. In-memory, so limits are per API instance."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return True


class DailyQuota:
    """Caps an action per UTC day (0 = no cap). In-memory, so it resets when the process restarts:
    keep a spending limit at the LLM provider as the real backstop."""

    def __init__(self, limit: int):
        self.limit = limit
        self._day: date | None = None
        self._used = 0
        self._lock = threading.Lock()

    def try_consume(self, today: date | None = None) -> bool:
        if self.limit <= 0:
            return True
        today = today or datetime.now(timezone.utc).date()
        with self._lock:
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True
