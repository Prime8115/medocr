"""Inventory API-pull sync: ingest core + endpoint."""
import pytest

from app.services.inventory.ingest import ingest_records, pull_from_api
from app.services.inventory.matching import match_name
from tests.conftest import register_and_login


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


def test_pull_from_api_top_level_array():
    def fake_get(url, headers=None, timeout=None):
        return FakeResp([
            {"item_name": "Calpol 500", "salt": "Paracetamol 500", "quantity": 40},
            {"item_name": "Azee 500", "generic": "Azithromycin 500", "stock": 10},
        ])

    records = pull_from_api("http://x/items", http_get=fake_get)
    assert records[0]["name"] == "Calpol 500"
    assert records[0]["composition"] == "Paracetamol 500"
    assert records[0]["stock_qty"] == 40


def test_pull_from_api_nested_path_and_mapping():
    def fake_get(url, headers=None, timeout=None):
        return FakeResp({"data": {"items": [{"nm": "Dolo 650", "comp": "Paracetamol 650", "qh": 120}]}})

    records = pull_from_api(
        "http://x", http_get=fake_get, items_path="data.items",
        mapping={"name": "nm", "composition": "comp", "stock_qty": "qh"},
    )
    assert records[0]["name"] == "Dolo 650"
    assert records[0]["composition"] == "Paracetamol 650"


def test_pull_from_api_non_list_raises():
    def fake_get(url, headers=None, timeout=None):
        return FakeResp({"oops": "not a list"})

    with pytest.raises(ValueError):
        pull_from_api("http://x", http_get=fake_get)


def test_ingest_records_populates_and_matches(client, db_session):
    """ingest_records feeds the same matching as CSV import."""
    headers = register_and_login(client)
    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    s = db_session()
    try:
        n = ingest_records(s, shop_id, [
            {"name": "Crocin 500", "composition": "Paracetamol 500", "stock_qty": 90},
            {"name": "Azee 500", "composition": "Azithromycin 500", "stock_qty": 10},
        ])
        s.commit()
        assert n == 2
    finally:
        s.close()

    # Matches surface through the API using the ingested data.
    r = client.post("/v1/inventory/match", json={"name": "Crocin 500"}, headers=headers)
    assert r.json()["candidates"][0]["name"] == "Crocin 500"


def test_sync_endpoint_saves_source(client, monkeypatch):
    headers = register_and_login(client)

    # Patch the module-level requests.get used by pull_from_api.
    import app.services.inventory.ingest as ingest_mod

    class _R:
        def json(self):
            return [{"name": "Calpol 500", "composition": "Paracetamol 500", "stock": 40}]

        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: _R())

    r = client.post("/v1/inventory/sync", json={"url": "https://shop.local/api/items"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    # Source config is saved (without the secret) for re-sync.
    src = client.get("/v1/inventory/source", headers=headers).json()
    assert src["url"] == "https://shop.local/api/items"
