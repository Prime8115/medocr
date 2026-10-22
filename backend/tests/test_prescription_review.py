"""A prescription needs source confirmation and literal field rechecks."""
from app.services import recheck
from tests.conftest import register_and_login, sample_image


def test_prescription_requires_confirmation_before_approval(client, mock_ocr):
    headers = register_and_login(client)
    uploaded = client.post("/v1/documents/", headers=headers,
                           files={"file": ("rx.png", sample_image(), "image/png")},
                           data={"doc_type": "prescription"})
    assert uploaded.status_code == 200
    doc_id = uploaded.json()["document_id"]
    doc = client.get(f"/v1/documents/{doc_id}", headers=headers).json()
    checks = doc["payload"]["meta"]["verification"]["review_requirements"]
    assert any(c["id"] == "prescription_confirmation" for c in checks)
    assert client.post(f"/v1/documents/{doc_id}/approve", headers=headers).status_code == 409
    ids = [c["id"] for c in checks if c["status"] == "fail"]
    approved = client.post(f"/v1/documents/{doc_id}/approve", headers=headers,
                           json={"acknowledged": ids})
    assert approved.status_code == 200, approved.text


def test_prescription_recheck_asks_for_literal_medication_text():
    fields = {"patient": {"name": {"value": "A"}}, "prescriber": {},
              "medications": [{"name": {"value": "Paracip"}, "strength": {"value": "500 mg"},
                               "frequency": {"value": "twice daily"}}]}
    prompt, current, kind = recheck.question(fields, "medications[0].frequency", "prescription")
    assert current == "twice daily" and kind == "text"
    assert "Do not infer dosing" in prompt
    assert "medication 1 of 1" in prompt
