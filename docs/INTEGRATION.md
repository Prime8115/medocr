# MediScan — External Integration Guide

How to receive approved documents from MediScan into your pharmacy software.
There are three connector types; all deliver the **same versioned payload**.

## Push payload (v1.0)

Every delivery — webhook body, exported JSON file, or agent payload — has this shape:

```json
{
  "payload_version": "1.0",
  "event": "document.approved",
  "document_id": "doc_ab12cd34ef56",
  "shop_id": "…",
  "doc_type": "prescription",          // or "invoice"
  "overall_confidence": 0.91,
  "schema_version": "1.0",
  "data": { … type-specific fields … },
  "meta": { "overall_confidence": 0.91, "language": "en",
            "pipeline": "gemini", "warnings": ["prescriber.registration_no"] }
}
```

Each leaf field in `data` is `{ "value": <string|null>, "confidence": <0..1> }`.
Normalized companions may appear (e.g. `strength.normalized = {amount, unit}`,
dates as `normalized: "YYYY-MM-DD"`).

### Prescription `data`
`patient{name,age,gender}`, `prescriber{name,registration_no}`,
`medications[]{name,strength,form,frequency,duration,instructions}`.

### Invoice `data`
`supplier{name,gstin,address}`, `invoice{invoice_no,invoice_date,total_amount}`,
`line_items[]{description,batch_no,expiry,quantity,mrp,rate,amount,hsn,gst_percent}`.

> **Versioning:** additive fields may appear within v1. Renames/removals bump
> `payload_version`. Pin to the major version and ignore unknown fields.

---

## Connector 1 — Webhook

MediScan sends `POST <your-url>` with the JSON payload. Configure a **secret** to
enable HMAC-SHA256 signing.

**Headers**
- `X-MediScan-Signature: sha256=<hex>` — HMAC-SHA256 of the **raw body** using your secret
- `X-MediScan-Payload-Version: 1.0`

**Verify (Python)**
```python
import hmac, hashlib

def verify(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Respond `2xx` to acknowledge. Non-2xx / timeouts are retried with exponential
backoff (default 3 attempts). Every attempt is logged and viewable in Settings.

---

## Connector 2 — File export (CSV / JSON)

MediScan renders `<document_id>.csv` and/or `<document_id>.json` into a folder you
choose (or offers them for download in the web admin). CSV is flattened to one row
per medication / invoice line item. Point your software's import at that folder.

---

## Connector 3 — Desktop agent

For software with no API and no shared folder reachable from the cloud. A small
Windows companion app pairs once with a one-time code, then pulls approved
documents and writes CSV/JSON locally. See `desktop-agent/README.md`.

---

## Idempotency

Delivery is idempotent per `(document_id, connector)`. Re-pushing never
double-posts an already-delivered document; previously failed deliveries are
retried. Use `document_id` as your dedupe key on the receiving side.
