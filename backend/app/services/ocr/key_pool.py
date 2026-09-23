"""Thread-safe Gemini API Key Pool with round-robin rotation, client caching,
and automatic cooldown circuit-breaking on HTTP 429 rate limits.
"""
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


class KeyPool:
    """Manages a pool of Gemini API keys for load balancing and fault tolerance."""

    def __init__(self, keys: List[str], client_factory: Optional[Callable[[str], Any]] = None):
        cleaned = [k.strip() for k in keys if k and k.strip()]
        if not cleaned:
            raise ValueError("KeyPool requires at least one valid API key.")
        self._keys = cleaned
        self._lock = threading.Lock()
        self._index = 0
        self._cooldowns: Dict[str, float] = {k: 0.0 for k in self._keys}
        self._clients: Dict[str, Any] = {}
        self._client_factory = client_factory

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def mark_rate_limited(self, key: str, cooldown_seconds: float = 45.0) -> None:
        """Place a key into cooldown after encountering a rate limit (HTTP 429)."""
        with self._lock:
            if key in self._cooldowns:
                self._cooldowns[key] = time.time() + cooldown_seconds

    def is_in_cooldown(self, key: str) -> bool:
        with self._lock:
            return time.time() < self._cooldowns.get(key, 0.0)

    def get_next_key(self) -> str:
        """Return the next healthy API key in round-robin order.

        If all keys are currently in cooldown, falls back to the key that will
        exit cooldown soonest.
        """
        now = time.time()
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
            client = self._clients.get(selected_key)
            if client is None:
                if self._client_factory:
                    client = self._client_factory(selected_key)
                else:
                    from google import genai

                    client = genai.Client(api_key=selected_key)
                self._clients[selected_key] = client
            return client, selected_key
