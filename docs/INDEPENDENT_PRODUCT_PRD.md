# MediScan independent product: product requirements

- Version: 1.0 draft, 3 November 2026
- Owner: MediScan product and engineering
- Status: implementation in progress; targets below are release gates, not measured results
- Source baseline: `main` after PR #36

## 1. Product decision

MediScan is a standalone, multi-tenant product for Indian pharmacies and pharmacy groups. It converts supplier invoices into reviewed, traceable purchase data and sends approved data to an existing ERP. SohamERP is the first integration partner, through a documented HTTPS receiver. Other ERP integrations must use the same versioned contract and pass the same acceptance tests. Prescription capture remains a separate, supervised workflow; it must not create purchase entries.

The saleable outcome is **an accepted, reconciled purchase entry with less staff time**, not a claim of error-free OCR. A correct-looking total cannot prove that all pages, products, batches or quantities were captured. MediScan must show uncertainty and preserve the original evidence.

### Market position

As checked on 3 November 2026, [MargBooks](https://care.margcompusoft.com/margerp/bills/170486/1/inftrees) documents upload and auto-read of purchase invoices, plus Excel/CSV/PDF and Marg-to-Marg import routes. [ChemistMitra](https://chemistmitra.com/pricing/) advertises OCR purchase recording in a ₹1,999/year single-store plan with a post-trial invoice allowance; [MedixoRx](https://medixorx.in/index.html) advertises AI scanning within a ₹2,999/year entry plan. These are vendor claims and advertised prices, not independent accuracy results or equivalent quotes. MediScan should sell to shops that keep an existing ERP and need a dependable bridge, evidence-led review and verified import. We cannot claim better OCR accuracy than these products until the same invoices and ground truth are tested under comparable conditions.

## 2. Buyers, users and jobs

| Role | Job | Success condition |
| --- | --- | --- |
| Pharmacy owner or chain operator | Reduce purchase-entry effort and duplicate receipts | Can see accepted voucher references, exceptions, usage and staff actions by shop |
| Purchase-entry staff | Capture photos/PDFs and correct uncertain fields | Every page and line is reviewable beside its source; a save survives reload |
| Pharmacist/supervisor | Confirm high-risk medicine data | Batch, expiry, product, quantity and tax discrepancies are explicit before approval |
| ERP partner | Import approved data safely | Signed, tenant-scoped, idempotent delivery with a durable voucher acknowledgement |
| Support operator | Diagnose failures without exposing customer data | Correlation ID, redacted error, retry state and audit trail |

## 3. Current baseline and gaps

The existing application has Android capture and offline queue, PDF/photo intake, multi-page source preview, structured invoice and prescription extraction, review and field recheck, exact-file correction replay, scoped supplier lessons, GST checks, web review, file/Tally pilot/HTTPS/desktop connectors, audit events and CI. The [partner contract](ERP_WEBHOOK_PARTNER_GUIDE.md) defines signed webhook requests and received versus imported acknowledgements. These are implemented capabilities, not evidence of universal accuracy or a certified SohamERP import.

| Gap | Present state | Required proof before sale |
| --- | --- | --- |
| Accuracy | Private regression corpus exists; most fields have not been human-labelled | Blind, stratified holdout with field, row, page and accepted-voucher metrics |
| Photos and handwriting | Extraction and review exist; intake now warns conservatively about severe defocus and small print | Add glare/clipping feedback, pre-upload retake flow and a supervised prescription gate; measure photo and handwriting cohorts separately |
| Supplier learning | Exact replay and scoped lessons exist | New-invoice holdout, evidence-backed promotion, conflict abstention and rollback |
| ERP acceptance | Webhook has signed envelope and import acknowledgement | Witness SohamERP receiver creating a purchase voucher; reconcile every row and total |
| Amendments | One delivery key per document/connector protects against duplicate send | Versioned correction/reversal protocol with receiver acknowledgement; first increment blocks reuse of a stale success |
| Operations | Basic audit and deployment exist; the partner self-hosting package has Compose, HTTPS routing, env preflight and a web image | Complete a server-specific restore drill, incident runbook, tenant isolation check, usage/cost visibility and support SLA |
| Commercial | No validated plan or conversion data | Paid pilot, per-shop economics, support burden and repeatable onboarding |

## 4. Functional requirements

### P0: safe invoice-to-purchase flow

1. **Page integrity.** Preserve source page count, page order, image hash and any extraction failure. Show each source page in review. A missing or duplicate page prevents silent approval. Test with 1-, 2- and 5-page PDFs, photos and mixed invoice/credit-note bundles.
2. **Source-linked fields.** Store page/region/row evidence for supplier, invoice header, product, pack, billed quantity, free quantity, batch, expiry, rate, discount and tax. If evidence is ambiguous, require review. Never infer billed quantity from pack text such as `15's tablets`.
3. **Critical checks.** Detect missing/duplicate rows; distinguish supplier, bill-to and ship-to blocks; verify GSTIN and tax arithmetic using printed line and summary values; keep invoice, credit note and prescription semantics distinct. Approval records who resolved or acknowledged each exception.
4. **Durable correction.** A saved field survives API round-trip, app reload and second-device reload. Exact-file reupload can replay approved corrections when document identity matches. A new file from the same supplier may use only evidence-backed, scoped lessons. Conflicting or absent source evidence causes abstention.
5. **Delivery safety.** Each approved exported version has an immutable payload digest and audit record. A repeat request for the same version is idempotent. A changed version cannot reuse an earlier sent/queued success. Until amendment support exists, show a blocking 409 and require reconciliation with the receiving ERP. A failed delivery may retry without duplicating a voucher.
6. **SohamERP receiver.** Use HTTPS POST, HMAC signature over raw bytes, shop/tenant binding, versioned schema, unique `(tenant, document_id, version)` import key, explicit `received`, `imported` with voucher ID, or `rejected` outcome. Preserve the original invoice document ID and invoice/credit-note type. Validate each line, batch, quantity and tax before posting stock. The partner-facing contract and synthetic examples are in [ERP webhook guide](ERP_WEBHOOK_PARTNER_GUIDE.md).
7. **Reconciliation.** Show delivery state, ERP reference, receiver error and retriable status in MediScan. An `imported` response is accepted only with a durable voucher reference. Queue receipt or file generation is not an accepted purchase voucher.

### P1: independent product operations

1. Tenant-scoped roles for capture, review, approval, connector management and support access; audit every change and export.
2. Usage metering by page, document, model route and tenant, with cost and latency dashboards, quotas and billing export. Do not set retail pricing until pilot unit costs are measured.
3. Configurable encrypted original retention and deletion, backup/restore drills, secret rotation, connector health checks and error budgets. Never put invoice originals, credentials or customer records in the partner source repository.
4. Guided onboarding for supplier samples, ERP endpoint verification, sandbox delivery, mapping review and witnessed first import.
5. Partner SDK/receiver conformance tests and a sandbox with synthetic invoices, credit notes and failure responses.

### P2: expansion

1. Certified adapters for additional ERPs after one SohamERP integration meets reconciliation gates.
2. Chain controls: shared supplier identity, per-branch stock destination, centralized policy and site-specific reporting.
3. Handwritten prescriptions only as pharmacist-reviewed transcription, with drug/allergy/clinical validation left to a separately governed system. Do not auto-dispense or auto-post a prescription.

## 5. Experience and failure behavior

The reviewer sees page count, source pages, a compact exception list, editable header/rows and a persistent save result. Recheck offers a sourced candidate; “Use & save” must persist the selected value and confirm it after a server reload. On different devices, the same document version must show the same row count, values and review state. If a document changed after export, Send explains which receiving ERP needs reconciliation and never claims success from an older payload.

For low-quality photos, flag blur, glare, clipping and missing corners before upload when feasible, and permit retake. For scanned PDFs, page count comes from the file, not the number of successful OCR responses. If extraction fails on page two, page two remains visible and approval requires explicit handling.

## 6. Quality measurement and release gates

Freeze a human-labelled holdout before changing extraction rules. Stratify by supplier/layout, unseen supplier, page count, text PDF/scanned PDF/photo/handwriting, invoice/credit note, and device. Record ground truth for all critical fields and every row, including explicit absence. Do not count a regression test with only partial labels as a full accuracy result.

| Metric | Pilot gate (proposal) | Measurement |
| --- | --- | --- |
| Page recall | 100% on labelled pilot set | Input pages visible and accounted for after upload |
| Silent critical-field error | 0 in the release holdout | Wrong product, batch, expiry, billed/free quantity, party, invoice number, tax or total accepted without review |
| Row recall | At least 99.5% on labelled printed invoices | Ground-truth product rows matched to reviewed rows; separate photo cohort |
| Save durability | 100% in scripted cross-device cases | Save, API reload, reopen on second device, exact reupload |
| Duplicate voucher | 0 in retry/amendment tests | Receiver voucher count after retries, timeouts and repeat sends |
| Accepted import | At least 99% of approved, supported pilot invoices | Durable SohamERP voucher reference plus row/total reconciliation |
| Review time | Improvement versus manual entry, measured per cohort | Median and p90 staff time from open to accepted import |

Report denominators, confidence intervals where useful, and unresolved exceptions. A zero-error small sample is not a guarantee. The first commercial pilot requires at least three pharmacies, multiple supplier layouts and a witnessed SohamERP import at each site; numeric gates may be tightened after baseline measurement.

## 7. Phased delivery and exit criteria

1. **Safety increment (shipped).** Block stale delivery reuse after approved content changes; repeat unchanged sends remain idempotent, changed sends return 409 before any connector posts.
2. **Partner self-hosting package (built, server acceptance pending).** Publish the [server runbook](SOHAMERP_SELF_HOSTING.md), Compose stack, HTTPS routing, web image and environment preflight in both repositories. Exit: SohamERP runs it on its server, verifies DNS/TLS, live OCR, encrypted backup and a restore drill.
3. **Evidence and benchmark.** The upload manifest and OCR page counts now have an explicit review check when they disagree, and severe photo defocus has an advisory warning. Complete annotations for a blind holdout, source coordinates, glare/clipping signals and a retake flow. Exit: published cohort metrics and zero silent critical errors in the release holdout.
4. **SohamERP integration pilot.** Implement receiver in the selected SohamERP product, run synthetic conformance tests, then witnessed real purchase entries. Exit: voucher IDs, line/batch/tax reconciliation, duplicate and failure drills.
5. **Amendment protocol.** Introduce approved document versions, correction/reversal events and receiver acknowledgements. Exit: a corrected imported invoice changes the intended ERP entry once, with audit and no duplicate stock.
6. **Commercial readiness.** Add metering, support tools, retention/restore drill and contracts; run paid pilot and compare time saved and accepted-entry cost against quoted alternatives. Exit: owner signs off on unit economics, SLA and onboarding checklist.

## 8. Dependencies and decisions

- SohamERP must nominate the first receiving product, a technical owner, sandbox HTTPS endpoint, authentication/secret exchange, voucher mapping and import validation rules. This has not been decided yet.
- SohamERP selected an existing HTTPS reverse proxy for hosting. API/web domain names, server access and the first receiving product are still to be supplied; the self-hosting package binds its upstreams to loopback.
- A source-code repository is a delivery artifact, not an IP assignment or deployment. Access and commercial rights require a separate agreement.
- The partner copy uses a fresh private Git history with source and synthetic examples only. Production secrets, private invoice corpus and live deploy configuration stay outside it. Additional GitHub users receive access only when the owner supplies usernames.
- A packaged server stack is not a completed SohamERP deployment or a customer-facing accuracy claim. A witnessed partner install and accepted ERP import remain required.
