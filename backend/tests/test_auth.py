"""Auth flow: register, login, protected route, bad credentials."""
from tests.conftest import register_and_login


def test_register_creates_owner(client):
    resp = client.post(
        "/v1/auth/register",
        json={"email": "a@shop.com", "password": "password123", "shop_name": "Shop A"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "a@shop.com"
    assert body["role"] == "owner"
    assert body["shop_id"]


def test_duplicate_email_rejected(client):
    payload = {"email": "dup@shop.com", "password": "password123", "shop_name": "Shop"}
    assert client.post("/v1/auth/register", json=payload).status_code == 201
    assert client.post("/v1/auth/register", json=payload).status_code == 400


def test_login_and_me(client):
    headers = register_and_login(client, email="login@shop.com")
    resp = client.get("/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "login@shop.com"


def test_login_wrong_password(client):
    client.post(
        "/v1/auth/register",
        json={"email": "wp@shop.com", "password": "password123", "shop_name": "S"},
    )
    resp = client.post("/v1/auth/login", data={"username": "wp@shop.com", "password": "nope"})
    assert resp.status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/v1/auth/me").status_code == 401
    assert client.get("/v1/documents/").status_code == 401


def test_short_password_rejected(client):
    resp = client.post(
        "/v1/auth/register",
        json={"email": "x@shop.com", "password": "short", "shop_name": "S"},
    )
    assert resp.status_code == 422
