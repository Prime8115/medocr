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


def _login(client, email, password="password123"):
    r = client.post("/v1/auth/login", data={"username": email, "password": password})
    return r, {"Authorization": f"Bearer {r.json().get('access_token', '')}"}


def _platform_admin(client, db_session, email="ops@mediscan.in"):
    from app.models.user import User

    headers = register_and_login(client, email=email, shop="MediScan Ops")
    s = db_session()
    try:
        s.query(User).filter_by(email=email).update({"is_platform_admin": True})
        s.commit()
    finally:
        s.close()
    return headers


def test_documents_follow_the_current_workspace(client, db_session):
    from app.models.document import Document

    ops = _platform_admin(client, db_session)
    org = client.post("/v1/platform/orgs", headers=ops, json={
        "name": "Chain", "plan": "growth", "workspace_name": "Branch A",
        "admin_email": "boss@chain.com", "admin_password": "password123"}).json()
    _, boss = _login(client, "boss@chain.com")
    b = client.post("/v1/org/workspaces", headers=boss, json={"name": "Branch B"}).json()
    a = [w for w in client.get("/v1/org/workspaces", headers=boss).json() if w["name"] == "Branch A"][0]
    s = db_session()
    try:
        s.add(Document(shop_id=a["id"], doc_type="invoice", status="needs_review", payload={}))
        s.add(Document(shop_id=b["id"], doc_type="invoice", status="needs_review", payload={}))
        s.add(Document(shop_id=b["id"], doc_type="invoice", status="needs_review", payload={}))
        s.commit()
    finally:
        s.close()
    assert len(client.get("/v1/documents/", headers=boss).json()) == 1          # Branch A
    me = client.post("/v1/auth/workspace", headers=boss, json={"workspace_id": b["id"]}).json()
    assert me["workspace_name"] == "Branch B"
    assert len(client.get("/v1/documents/", headers=boss).json()) == 2          # Branch B
    # A member given only Branch A cannot open Branch B.
    client.post("/v1/org/users", headers=boss, json={"email": "clerk@chain.com", "password": "password123",
                                                     "role": "staff", "workspace_ids": [a["id"]]})
    _, clerk = _login(client, "clerk@chain.com")
    assert client.post("/v1/auth/workspace", headers=clerk, json={"workspace_id": b["id"]}).status_code == 403
    assert len(client.get("/v1/documents/", headers=clerk).json()) == 1
    assert org["plan"] == "growth"


def test_deactivating_frees_a_seat_and_stops_sign_in(client):
    admin = register_and_login(client, email="a@seat.com", shop="Seats")
    ws = client.get("/v1/org/workspaces", headers=admin).json()[0]["id"]
    u = client.post("/v1/org/users", headers=admin, json={"email": "b@seat.com", "password": "password123",
                                                          "role": "staff", "workspace_ids": [ws]}).json()
    assert client.get("/v1/org", headers=admin).json()["seats_used"] == 2
    assert client.patch(f"/v1/org/users/{u['id']}", headers=admin, json={"is_active": False}).status_code == 200
    assert client.get("/v1/org", headers=admin).json()["seats_used"] == 1
    r, _ = _login(client, "b@seat.com")
    assert r.status_code == 403
    # ...and the freed seat takes someone else.
    assert client.post("/v1/org/users", headers=admin, json={"email": "c@seat.com", "password": "password123",
                                                             "role": "staff", "workspace_ids": [ws]}).status_code == 201


def test_platform_admin_sets_the_plan_and_can_switch_an_org_off(client, db_session):
    ops = _platform_admin(client, db_session, email="ops2@mediscan.in")
    org = client.post("/v1/platform/orgs", headers=ops, json={
        "name": "Pharma Co", "plan": "growth", "admin_email": "head@pharma.co", "admin_password": "password123"}).json()
    _, head = _login(client, "head@pharma.co")
    ws = client.get("/v1/org/workspaces", headers=head).json()[0]["id"]
    for i in range(3):
        client.post("/v1/org/users", headers=head, json={"email": f"s{i}@pharma.co", "password": "password123",
                                                         "role": "staff", "workspace_ids": [ws]})
    # A downgrade below what is in use removes no one, but blocks adding.
    down = client.patch(f"/v1/platform/orgs/{org['id']}", headers=ops, json={"plan": "starter"}).json()
    assert (down["seats_used"], down["seats_allowed"], down["over_limit"]) == (4, 2, True)
    assert client.post("/v1/org/users", headers=head, json={"email": "x@pharma.co", "password": "password123",
                                                            "role": "staff", "workspace_ids": [ws]}).status_code == 403
    assert client.patch(f"/v1/platform/orgs/{org['id']}", headers=ops, json={"plan": "nope"}).status_code == 422
    # Switched off: its users are refused; the platform admin is not.
    client.patch(f"/v1/platform/orgs/{org['id']}", headers=ops, json={"is_active": False})
    assert client.get("/v1/org", headers=head).status_code == 403
    r, _ = _login(client, "head@pharma.co")
    assert r.status_code == 403
    assert client.get("/v1/platform/orgs", headers=ops).status_code == 200


def test_only_platform_admins_reach_the_platform_pages(client):
    someone = register_and_login(client, email="who@shop.com", shop="Shop")
    assert client.get("/v1/platform/orgs", headers=someone).status_code == 403
    assert client.post("/v1/platform/orgs", headers=someone, json={
        "name": "X", "admin_email": "y@x.com", "admin_password": "password123"}).status_code == 403
