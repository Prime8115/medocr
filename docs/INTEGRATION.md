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
`line_items[]{description,pack,batch_no,expiry,quantity,free_quantity,mrp,ptr,pts,`
`rate,rate_source,discount_percent,amount,hsn,gst_percent}`.

**Prices are not interchangeable.** An Indian pharma invoice prints several per
line, so each has its own field:

| Field | Meaning |
|---|---|
| `mrp` | Maximum Retail Price — what the customer pays |
| `ptr` | Price To Retailer — what the pharmacy pays per unit |
| `pts` | Price To Stockist |
| `rate` | **The rate the line was billed at.** Always populated — this is the field to import. |
| `rate_source` | Which column `rate` came from, as the supplier printed it: `"RATE"`, `"PTR"` or `"PTS"`. Carries `confidence: null` (it is a label, not a reading). |

`quantity` is the billed quantity only; scheme/bonus goods are in
`free_quantity` and must be added to stock separately.

### Invoice integrity (`meta`)
Every invoice is cross-checked before it can be approved. Read these if you
reconcile on your side:

| Key | Meaning |
|---|---|
| `copies_detected` | Printed copies found in one file (GST invoices are often Original/Duplicate/Triplicate). Only the first is extracted. |
| `duplicates_removed` | Identical rows collapsed after extraction. |
| `stated_item_count` | The item count the invoice prints about itself, when present. |
| `line_items_total` | Sum of the line `amount` values. |
| `total_reconciles` | `true` when the printed total matches either the taxable sum or that sum plus per-line GST (tolerance: the greater of ₹5 or 2%) — an Indian grand total is tax-inclusive while line amounts are taxable value. `false` when neither matches, `null` when no total could be read at all. |
| `billed_rate_column` | Which printed price column the bill turned out to be charged on (`rate`, `ptr`, `pts`), decided from amount ÷ quantity rather than from the column's name. |

Any mismatch also appears in plain language in `meta.warnings`.

> **Versioning:** additive fields may appear within v1. Renames/removals bump
> `payload_version`. Pin to the major version and ignore unknown fields.
> `ptr`, `pts`, `pack`, `free_quantity`, `discount_percent` and `rate_source`
> were added in this way; `rate` keeps its meaning and is still always set.

---

## Export profiles

`profile` picks a ready-made column layout; `columns` overrides it entirely.

| Profile | Use it for |
|---|---|
| `generic` | A stable, minimal column set. **Its shape never changes** — safe to import against. |
| `detailed` | Everything we extract: pack, free qty, MRP, PTR, PTS, rate + rate source, discount, HSN, GST. |
| `marg` / `vyapar` / `tally` | Matched to those products' import layouts. |

**If your shop receives scheme goods (10+2), use `detailed` or add
`free_quantity` to a custom `columns` list.** `generic` carries only the billed
quantity, so scheme units would never reach your stock.

---

## Monitoring

`GET /v1/documents/stats?days=30` returns extraction health for your shop:
counts by status / doc type / pipeline, and for invoices the share that
reconcile against their printed total, the share handled by the exact PDF
parser rather than the AI fallback, duplicate rows removed, and multi-copy PDFs
seen. `warnings` carries plain-language flags worth acting on.

`POST /v1/documents/{id}/report` with `{"note": "..."}` flags an extraction as
wrong. The note is stored with what the pipeline produced and the stored file
reference, so the case can be reproduced exactly.

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
