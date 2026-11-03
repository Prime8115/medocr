"""Organisations, workspaces and licence seats (docs/ORG_USER_MANAGEMENT_PRD.md)."""
from tests.conftest import register_and_login


def test_admin_adds_users_up_to_the_plan_and_members_see_only_their_workspaces(client):
    admin = register_and_login(client, email="admin@org.com", shop="Org One")
    org = client.get("/v1/org", headers=admin).json()
    assert (org["plan"], org["seats_used"], org["seats_allowed"]) == ("starter", 1, 2)
    ws = client.get("/v1/org/workspaces", headers=admin).json()
    r = client.post("/v1/org/users", headers=admin, json={"email": "m1@org.com", "password": "password123",
                                                          "role": "staff", "workspace_ids": [ws[0]["id"]]})
    assert r.status_code == 201, r.text
    # Starter: 2 seats - the third is refused with the reason.
    r = client.post("/v1/org/users", headers=admin, json={"email": "m2@org.com", "password": "password123",
                                                          "role": "staff", "workspace_ids": [ws[0]["id"]]})
    assert r.status_code == 403 and "seats" in r.json()["detail"]
    # Starter allows one workspace.
    assert client.post("/v1/org/workspaces", headers=admin, json={"name": "Branch 2"}).status_code == 403
    # A member cannot manage users, and cannot switch outside their workspaces.
    member = client.post("/v1/auth/login", data={"username": "m1@org.com", "password": "password123"}).json()
    mh = {"Authorization": f"Bearer {member['access_token']}"}
    assert client.get("/v1/org/users", headers=mh).status_code == 403
    assert client.post("/v1/auth/workspace", headers=mh, json={"workspace_id": "nope"}).status_code == 403
    me = client.get("/v1/auth/me", headers=mh).json()
    assert me["org_name"] == "Org One" and [w["id"] for w in me["workspaces"]] == [ws[0]["id"]]


def test_last_admin_is_kept(client):
    admin = register_and_login(client, email="solo@org.com", shop="Solo")
    me = client.get("/v1/auth/me", headers=admin).json()
    r = client.patch(f"/v1/org/users/{me['id']}", headers=admin, json={"role": "staff"})
    assert r.status_code == 409
