"""Thread-safe Gemini API key pool: round-robin rotation, client caching, and
rate-limit awareness that survives from one scan to the next.

Gemini counts its quotas per key AND per model - Flash-Lite running out says
nothing about the fallback model. So besides the original per-key cooldown,
the pool tracks, for every (key, model):

* a cooldown, set from what Gemini says when it refuses a request - the
  retry delay it gives, or, when the DAILY quota is gone, until that quota
  resets (midnight Pacific time, Google's day);
* a local requests-per-minute limit (OCR_RPM_PER_KEY), so the app paces itself
  below the quota instead of discovering it by being refused.

`shared_pool()` returns one pool per process. Before it, every scan built its
own pool, so a cooldown learned on one scan was forgotten by the next and every
scan hit the limit afresh.
"""
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple


class QuotaWait(Exception):
    """No key can serve this model now. `retry_after` is how long until one
    can; `daily` means the day's quota is gone for every key."""

    def __init__(self, model: str, retry_after: float, daily: bool):
        self.model = model
        self.retry_after = max(0.0, retry_after)
        self.daily = daily
        kind = "daily quota exhausted" if daily else "rate limited"
        # The 429 wording keeps it classed as transient by the provider.
        super().__init__(f"429 RESOURCE_EXHAUSTED: {model} {kind}; retry in {self.retry_after:.0f}s")


def next_quota_reset(now: Optional[float] = None) -> float:
    """When Gemini's daily quotas reset: the next midnight, Pacific time."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    current = datetime.fromtimestamp(now if now is not None else time.time(), tz)
    midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


class KeyPool:
    """Manages a pool of Gemini API keys for load balancing and fault tolerance."""

    def __init__(
        self,
        keys: List[str],
        client_factory: Optional[Callable[[str], Any]] = None,
        rpm_per_key: int = 0,
        clock: Callable[[], float] = time.time,
    ):
        cleaned = [k.strip() for k in keys if k and k.strip()]
        if not cleaned:
            raise ValueError("KeyPool requires at least one valid API key.")
        self._keys = cleaned
        self._lock = threading.Lock()
        self._index = 0
        self._cooldowns: Dict[str, float] = {k: 0.0 for k in self._keys}
        self._clients: Dict[str, Any] = {}
        self._client_factory = client_factory
        self._clock = clock
        self.rpm_per_key = rpm_per_key
        # (key, model) -> when it may be used again, and whether that is a day's wait
        self._model_cooldowns: Dict[Tuple[str, str], Tuple[float, bool]] = {}
        # (key, model) -> times of the requests sent in the last minute
        self._sent: Dict[Tuple[str, str], Deque[float]] = {}

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    # ------------------------------------------------- per-key (any model) --
    def mark_rate_limited(self, key: str, cooldown_seconds: float = 45.0) -> None:
        """Place a key into cooldown after encountering a rate limit (HTTP 429)."""
        with self._lock:
            if key in self._cooldowns:
                self._cooldowns[key] = self._clock() + cooldown_seconds

    def is_in_cooldown(self, key: str, model: Optional[str] = None) -> bool:
        """Whether `key` is resting - for any model, or with `model` given, for
        that model (a per-model cooldown counts; the per-minute pacing does not)."""
        with self._lock:
            now = self._clock()
            if now < self._cooldowns.get(key, 0.0):
                return True
            if model is None:
                return any(now < until for (k, _m), (until, _d) in self._model_cooldowns.items() if k == key)
            return now < self._model_cooldowns.get((key, model), (0.0, False))[0]

    def get_next_key(self) -> str:
        """Return the next healthy API key in round-robin order.

        If all keys are currently in cooldown, falls back to the key that will
        exit cooldown soonest.
        """
        now = self._clock()
        with self._lock:
            healthy = [k for k in self._keys if now >= self._cooldowns[k]]
            if healthy:
                chosen = healthy[self._index % len(healthy)]
                self._index = (self._index + 1) % len(healthy)
                return chosen
            # All keys are cooling down — pick the one closest to recovery.
            return min(self._keys, key=lambda k: self._cooldowns[k])

    def get_client(self, key: Optional[str] = None) -> Tuple[Any, str]:
        """Return a cached (client, key) tuple, selecting next healthy key if None."""
        selected_key = key or self.get_next_key()
        with self._lock:
            client = self._client_for(selected_key)
            return client, selected_key

    def _client_for(self, key: str) -> Any:
        client = self._clients.get(key)
        if client is None:
            if self._client_factory:
                client = self._client_factory(key)
            else:
                from google import genai

                client = genai.Client(api_key=key)
            self._clients[key] = client
        return client

    # ------------------------------------------------------- per (key, model) --
    def report_rate_limit(self, key: str, model: str, retry_after: Optional[float], daily: bool) -> None:
        """Gemini refused `model` on `key`: rest it as long as Gemini said, or
        until the daily quota resets."""
        now = self._clock()
        until = next_quota_reset(now) if daily else now + (retry_after if retry_after else 60.0)
        with self._lock:
            self._model_cooldowns[(key, model)] = (until, daily)

    def _free_at(self, key: str, model: str, now: float) -> Tuple[float, bool]:
        """When (key, model) can next send a request, and whether the wait is
        for the daily quota. Caller holds the lock."""
        until, daily = self._model_cooldowns.get((key, model), (0.0, False))
        until = max(until, self._cooldowns.get(key, 0.0))
        if self.rpm_per_key > 0:
            sent = self._sent.setdefault((key, model), deque())
            while sent and sent[0] <= now - 60.0:
                sent.popleft()
            if len(sent) >= self.rpm_per_key:
                until = max(until, sent[0] + 60.0)
        return until, daily and until > now

    def acquire(self, model: str, max_wait: float = 0.0,
                sleep: Callable[[float], None] = time.sleep) -> Tuple[Any, str]:
        """A client for a key that may send a `model` request now.

        Keys are taken round-robin among those free. If none is, waits for the
        soonest one when that is within `max_wait` seconds; otherwise raises
        QuotaWait saying how long, so the scan can go back in the queue
        instead of tying up a worker.
        """
        while True:
            with self._lock:
                now = self._clock()
                status = {k: self._free_at(k, model, now) for k in self._keys}
                free = [k for k in self._keys if status[k][0] <= now]
                if free:
                    key = free[self._index % len(free)]
                    self._index = (self._index + 1) % len(free)
                    if self.rpm_per_key > 0:
                        self._sent.setdefault((key, model), deque()).append(now)
                    return self._client_for(key), key
                soonest = min(status.values(), key=lambda s: s[0])
                wait = soonest[0] - now
                daily = all(d for _, d in status.values())
            if wait > max_wait:
                raise QuotaWait(model, wait, daily)
            sleep(wait)


# One pool per process, per set of keys - so what one scan learns about the
# quota, the next scan already knows.
_shared: Dict[Tuple[str, ...], KeyPool] = {}
_shared_lock = threading.Lock()


def shared_pool(keys: List[str], rpm_per_key: int = 0) -> KeyPool:
    signature = tuple(k.strip() for k in keys if k and k.strip())
    with _shared_lock:
        pool = _shared.get(signature)
        if pool is None:
            pool = _shared[signature] = KeyPool(list(signature), rpm_per_key=rpm_per_key)
        pool.rpm_per_key = rpm_per_key
        return pool
