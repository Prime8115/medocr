# MediScan — UAT Checklist (User Acceptance Testing)

Run through this on a real device + deployed backend before going live in a shop.
Check each box; note any failure.

## Auth
- [ ] Register a new pharmacy (owner) succeeds
- [ ] Log out, log back in; session persists on app restart
- [ ] Wrong password is rejected with a clear message
- [ ] A second shop cannot see the first shop's documents

## Capture — prescription
- [ ] Camera scan works (with framing guide)
- [ ] Gallery image upload works
- [ ] PDF upload works
- [ ] Image is compressed before upload (large photos still upload quickly)

## Capture — invoice
- [ ] Invoice scan auto-detects as "invoice" (or manual selection works)
- [ ] Line items (batch, expiry, qty, price) are extracted

## Review & correct
- [ ] Low-confidence fields are visibly highlighted
- [ ] Editing a field and saving persists the change (reopen to confirm)
- [ ] Overall confidence is shown

## Approve & send
- [ ] Approve moves status to "approved"
- [ ] With no connector, "Send" gives a clear "no connector" message
- [ ] Webhook connector: receiver gets a correctly-signed payload
- [ ] File-export connector: CSV + JSON produced with correct data
- [ ] Desktop agent: pairs with code, receives + writes the file, status→sent
- [ ] Re-sending the same document does NOT double-post (idempotency)

## Offline
- [ ] Turn off wifi/data, scan → document goes to the queue (badge shows count)
- [ ] Turn connectivity back on → queued items upload automatically
- [ ] App does not lose the captured image across a restart while queued

## Failure handling
- [ ] With OCR misconfigured, a scan ends as "failed" (never fake data)
- [ ] A failed document cannot be approved or sent

## Web admin
- [ ] Review queue shows live documents with search + filters
- [ ] Open a document, edit, approve, send from the console
- [ ] Add/test/delete a connector; test round-trip reports success/failure
- [ ] Delivery log lists each push attempt

## Non-functional
- [ ] Scan→result under ~10s for a clear single-page image
- [ ] App is usable one-handed at the counter (large buttons, readable text)
- [ ] HTTPS in production; no secrets in the app bundle beyond the API URL
