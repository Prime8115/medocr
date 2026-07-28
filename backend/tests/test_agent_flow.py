"""Desktop-agent pairing, delivery polling, and acknowledgement."""
from app.models.connector import Connector
from tests.conftest import register_and_login


def _create_agent_connector(client, headers):
    resp = client.post(
        "/v1/connectors/", json={"type": "desktop_agent", "name": "Shop PC"}, headers=headers
    )
    return resp.json()["id"]


def _pairing_code(db_session, connector_id):
    s = db_session()
    try:
        return (s.get(Connector, connector_id).config or {}).get("pairing_code")
    finally:
        s.close()


def test_pairing_issues_token_and_is_single_use(client, db_session):
    headers = register_and_login(client)
    cid = _create_agent_connector(client, headers)
    code = _pairing_code(db_session, cid)
    assert code

    paired = client.post("/v1/agent/pair", json={"code": code})
    assert paired.status_code == 200
    token = paired.json()["agent_token"]
    assert token

    # Code is single-use.
    assert client.post("/v1/agent/pair", json={"code": code}).status_code == 400
    # Bad code rejected.
    assert client.post("/v1/agent/pair", json={"code": "NOPE"}).status_code == 400


def test_agent_polls_and_acks_delivery(client, db_session):
    from app.models.document import Document
    from app.models.push_delivery import PushDelivery

    headers = register_and_login(client)
    cid = _create_agent_connector(client, headers)
    code = _pairing_code(db_session, cid)
    agent_token = client.post("/v1/agent/pair", json={"code": code}).json()["agent_token"]
    agent_headers = {"Authorization": f"Bearer {agent_token}"}

    shop_id = client.get("/v1/auth/me", headers=headers).json()["shop_id"]

    # Seed an approved doc + a pending delivery queued for this agent.
    s = db_session()
    try:
        doc = Document(shop_id=shop_id, doc_type="prescription", status="approved", payload={"fields": {}})
        s.add(doc)
        s.flush()
        delivery = PushDelivery(
            document_id=doc.id, connector_id=cid, status="pending",
            request_payload={"document_id": doc.id, "data": {}}, idempotency_key=f"{doc.id}:{cid}",
        )
        s.add(delivery)
        s.commit()
        delivery_id = delivery.id
    finally:
        s.close()

    # Agent sees the pending delivery.
    pending = client.get("/v1/agent/deliveries", headers=agent_headers).json()
    assert len(pending) == 1
    assert pending[0]["id"] == delivery_id

    # Agent acks it.
    assert client.post(f"/v1/agent/deliveries/{delivery_id}/ack", headers=agent_headers).status_code == 200
    # Now no pending deliveries remain.
    assert client.get("/v1/agent/deliveries", headers=agent_headers).json() == []


def test_agent_endpoints_reject_normal_user_token(client, db_session):
    headers = register_and_login(client)  # normal user bearer token
    assert client.get("/v1/agent/deliveries", headers=headers).status_code == 401
