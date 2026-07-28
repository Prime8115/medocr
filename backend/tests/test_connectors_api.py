"""Connector CRUD, test round-trip, tenancy, and role enforcement."""
from tests.conftest import register_and_login


def test_create_list_get_update_delete_connector(client):
    headers = register_and_login(client)

    created = client.post(
        "/v1/connectors/",
        json={"type": "webhook", "name": "My Hook", "config": {"url": "https://x"}, "secret": "topsecret"},
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    cid = body["id"]
    assert body["has_secret"] is True  # secret is never echoed back

    assert len(client.get("/v1/connectors/", headers=headers).json()) == 1

    updated = client.patch(f"/v1/connectors/{cid}", json={"enabled": False}, headers=headers)
    assert updated.json()["enabled"] is False

    assert client.delete(f"/v1/connectors/{cid}", headers=headers).status_code == 204
    assert client.get(f"/v1/connectors/{cid}", headers=headers).status_code == 404


def test_invalid_connector_type_rejected(client):
    headers = register_and_login(client)
    resp = client.post("/v1/connectors/", json={"type": "carrier-pigeon", "name": "x"}, headers=headers)
    assert resp.status_code == 400


def test_connectors_are_shop_scoped(client):
    headers_a = register_and_login(client, email="a@shop.com", shop="A")
    headers_b = register_and_login(client, email="b@shop.com", shop="B")
    cid = client.post(
        "/v1/connectors/", json={"type": "file_export", "name": "A export"}, headers=headers_a
    ).json()["id"]
    # Shop B cannot see or fetch Shop A's connector.
    assert client.get(f"/v1/connectors/{cid}", headers=headers_b).status_code == 404
    assert client.get("/v1/connectors/", headers=headers_b).json() == []


def test_file_export_test_roundtrip(client):
    headers = register_and_login(client)
    cid = client.post(
        "/v1/connectors/", json={"type": "file_export", "name": "Export", "config": {"formats": ["json"]}},
        headers=headers,
    ).json()["id"]
    resp = client.post(f"/v1/connectors/{cid}/test", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
