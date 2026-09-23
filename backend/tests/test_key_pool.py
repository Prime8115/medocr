import time
import pytest
from app.services.ocr.key_pool import KeyPool


def test_key_pool_init():
    with pytest.raises(ValueError):
        KeyPool([])

    pool = KeyPool(["key1", "key2", "  "])
    assert pool.keys == ["key1", "key2"]
    assert len(pool) == 2


def test_key_pool_round_robin():
    pool = KeyPool(["keyA", "keyB", "keyC"])
    picks = [pool.get_next_key() for _ in range(6)]
    assert picks == ["keyA", "keyB", "keyC", "keyA", "keyB", "keyC"]


def test_key_pool_rate_limit_cooldown():
    pool = KeyPool(["key1", "key2"])
    # Mark key1 as rate limited
    pool.mark_rate_limited("key1", cooldown_seconds=60)
    assert pool.is_in_cooldown("key1")
    assert not pool.is_in_cooldown("key2")

    # get_next_key should now exclusively pick key2
    picks = [pool.get_next_key() for _ in range(3)]
    assert picks == ["key2", "key2", "key2"]


def test_key_pool_all_in_cooldown():
    pool = KeyPool(["key1", "key2"])
    now = time.time()
    # key1 cools down in 10s, key2 cools down in 30s
    pool._cooldowns["key1"] = now + 10
    pool._cooldowns["key2"] = now + 30

    # Picks key1 as it will exit cooldown soonest
    assert pool.get_next_key() == "key1"


def test_key_pool_client_caching():
    created = []

    def mock_factory(k):
        created.append(k)
        return {"key": k}

    pool = KeyPool(["k1", "k2"], client_factory=mock_factory)
    c1, key1 = pool.get_client("k1")
    c2, key2 = pool.get_client("k1")
    assert c1 == c2
    assert created == ["k1"]  # Only created once

    c3, key3 = pool.get_client("k2")
    assert created == ["k1", "k2"]
