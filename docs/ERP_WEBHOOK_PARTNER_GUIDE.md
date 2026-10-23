# Connect an external ERP to MediScan

MediScan **sends** a reviewed and approved document to an HTTPS endpoint that your ERP team hosts. Your ERP does not need to poll MediScan. The shop owner enters the endpoint URL and a shared signing secret in MediScan Settings, then selects **Approve and send** on a document. This is a purchase/import integration; an ERP must still decide whether a document is an invoice, credit note, or prescription before posting it to accounts or inventory.

## Working example files

| File | Purpose |
| --- | --- |
| [Invoice request](examples/erp-webhook-invoice.json) | Exact v1 JSON envelope with supplier, header, two stock lines, pack, scheme quantity and tax |
| [Prescription request](examples/erp-webhook-prescription.json) | Same envelope with patient, prescriber and medication fields |
| [Imported acknowledgement](examples/erp-webhook-ack-imported.json) | ERP has created a voucher and returns its ID |
| [Received acknowledgement](examples/erp-webhook-ack-received.json) | Endpoint stored the message; ERP import is still unconfirmed |
| [Rejected acknowledgement](examples/erp-webhook-ack-rejected.json) | ERP refused the document with a reason |

All names and identifiers in the examples are synthetic. Fields may be absent or have `value: null`; ignore unknown fields within `payload_version: "1.0"`. The request also may include `rows` and `row_columns` if the shop configures a flat export profile. The nested `data` remains present.

## What your ERP team builds

1. Expose a reachable HTTPS `POST` endpoint, for example `https://erp.example.com/integrations/mediscan/documents`. Accept `Content-Type: application/json` and JSON bodies matching the examples. Keep the raw request bytes for signature verification.
2. Generate a long random shared secret and give it to the shop owner through a secure channel. Configure the **same** secret in MediScan's webhook connector. Verify `X-MediScan-Signature: sha256=<hex>` as HMAC-SHA256 over the *exact raw body* using constant-time comparison. Reject missing/invalid signatures with `401` before parsing or storing business data. Check `X-MediScan-Payload-Version: 1.0` and the body's `payload_version`.
3. Persist the message and its processing result with a unique key on `(shop_id, document_id)`. If MediScan retries the same document, return the original acknowledgement and do not create another purchase voucher. The same `document_id` can be sent to different connectors, so scope deduplication to your ERP tenant/shop.
4. Map `data.supplier` to a supplier master; `data.invoice` to a purchase header; each `data.line_items[]` entry to a stock/batch line. Read the `value` property on each leaf. Validate product, batch, expiry, unit, supplier, GST, tax totals, and whether `meta.document_kind` permits a purchase. Treat `pack` (e.g. `15 tablets`) as pack size, **not** billed quantity; `quantity` is billed units and `free_quantity` is bonus stock. `rate` is the billed rate; `mrp` is not the purchase rate. Do not substitute one for the other.
5. Return JSON within 10 seconds. If a voucher was durably created, return HTTP `200` with `{"status":"imported","external_reference":"PUR-2026-001"}`. The reference must be a non-empty string your staff can locate in the ERP. If you only accepted the message into a queue, return HTTP `202` with `{"status":"received"}`. If business validation rejects it, return HTTP `200` with `{"status":"rejected","reason":"..."}` so MediScan displays a failure and a human can correct/retry. For transient outages, return `5xx` and MediScan retries up to three times with short backoff. Other `2xx` responses, including plain text, mean only **received**, never **imported**.
6. Provide your own audit log and operator view linking `document_id` to ERP voucher ID, validation failures, and import timestamps. Test duplicate requests, timeouts, invalid signatures, 5xx, and mismatched totals before enabling live shops.

MediScan marks the connector **imported** only when the HTTP response explicitly includes both `status: "imported"` and `external_reference`. A `received` response proves transport acceptance only. There is no later ERP callback in this version; an asynchronous receiver must report a later import outcome in its own system or wait to return `imported` until the synchronous import finishes.

### Signature verification example (Python)

```python
import hashlib
import hmac

def verify_mediscan(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

For a FastAPI receiver, read `await request.body()` before JSON parsing and verify the signature against those bytes. Use the `document_id` from the parsed body as an idempotency key **after** signature verification. The endpoint should be scoped to the intended shop/tenant and should not trust a caller-supplied `shop_id` alone as authentication.

## MediScan setup and operation

The shop owner creates a **Webhook (HTTP)** connector in Settings with the endpoint URL and shared secret. A connection test sends a synthetic `test.ping` event; the ERP endpoint should acknowledge it without creating a voucher. On a real document, the reviewer corrects fields, approves, then chooses **Send to software**. The review screen shows each connector's separate outcome. A failed connector can be retried; a received or imported delivery is idempotent and will not be posted again by MediScan's normal resend action.

The document status **pushed** means every enabled **automatic** connector confirmed delivery. The manual Tally XML pilot is tracked separately and never counts as a successful push. The delivery state gives the stronger meaning: **queued** (waiting for desktop agent), **file_written** (written to a folder, ERP import unconfirmed), **received** (webhook accepted, ERP import unconfirmed), **imported** (ERP supplied a voucher reference), or **failed**. If one automatic connector fails, the document remains approved and its successful deliveries stay recorded.

## Tally and folder alternatives

The current Tally purchase pilot produces XML for **manual TallyPrime import** after a purchase-specific preflight. A downloaded XML file is not proof Tally accepted or posted a voucher. An external Tally integrator can instead build the HTTPS receiver above and translate the approved JSON into its validated Tally import flow, returning `imported` only after Tally provides a voucher reference. For software that watches files, MediScan's file-export connector requires an output folder reachable by the **MediScan server**. For a shop PC folder, install the desktop agent; its acknowledgement confirms local file writing, not ERP import.

The broader field list and optional flat-row profiles are documented in [INTEGRATION.md](INTEGRATION.md). Both the examples and that contract use payload version 1.0.
