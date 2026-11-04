# MediScan verified capture and purchase entry: product requirements

Version 2.0 | 4 November 2026 | Status: implementation proposal
Companion delivery scope: NEXT_PRODUCT_SCOPE.md

## 1. Product decision

MediScan is an independent, multi-organization product for Indian pharmacies and small chains. Its principal job is to turn an invoice into a reviewed and traceable purchase entry accepted by a chosen ERP. SohamERP is the first certification partner; the same versioned connector contract must remain available to other ERP providers. Prescription capture is a separate pharmacist-reviewed transcription lane.

The promise is less staff time and fewer silent stock-entry errors. The system must reveal uncertainty, show the source behind critical values and preserve what a reviewer approved. It must never call an invoice error-free because totals happen to add up. The release is judged on independently labelled documents and accepted ERP vouchers, not a demo PDF.

## 2. Shipped baseline and unresolved gaps

As of the 4 November 2026 source main branch, MediScan has Android 1.5.15 source, offline capture, PDF/photo intake, page manifests and source preview, deterministic digital-PDF tables, AI fallback, GST and required-field checks, recheck, saved correction replay, scoped supplier lessons, inventory catalog matching, workspace users/licences, a customer problem queue, web review, and generic HTTPS/file/desktop/Tally pilot connectors. The four-page Sai Venkata invoice D-00004 is a regression case: one unique four-row copy is extracted, with the printed batch, quantity, PTR and IGST values. Both repositories have the same core OCR implementation.

What is still unproven or incomplete:

- Most private-corpus fields are not human-labelled. The 135-entry corpus catches pinned regressions but cannot establish a product-wide accuracy percentage. The holdout scorer exists; its frozen, fully labelled dataset does not.
- Source preview shows pages; it does not bind each extracted field to a verified page region and row. A reviewer still searches for a disputed batch or address.
- Severe blur/small-print warnings exist. Glare, clipping, corners and perspective feedback, original/derivative policy and measured retake benefit remain open.
- Exact-file replay and supplier-scoped lessons exist. Rule proposals, independent promotion, outcome visibility and rollback for new invoice formats are incomplete.
- Inventory matching supplies fuzzy candidates. A reviewer-confirmed product/pack/unit link suitable for stock import is not yet certified.
- A signed HTTPS connector and delivery states exist. No witnessed SohamERP receiver-created voucher or amendment protocol is complete.
- The QR reader decodes e-invoice contents; a decoded token must not be described as cryptographically verified until its signature and trust chain are checked.
- The latest source CI attempt did not run backend tests because the private-corpus SSH fetch failed. The prior OCR commit passed and deployed.

## 3. Users and jobs

| User | Job | Required outcome |
| --- | --- | --- |
| Purchase-entry staff | Capture and correct invoices quickly | Every page and row visible; disputed field opens its source; save survives reload and second device |
| Supervisor/owner | Approve entries and manage people | Critical exceptions and stock mapping resolved; audit shows who approved and why |
| Pharmacist | Review prescription transcription | Literal medication instructions checked against source; no unreviewed clinical action |
| ERP partner | Receive approved purchase data | Versioned, signed, idempotent payload; explicit imported/rejected receipt and voucher reference |
| Support operator | Diagnose customer problems | Redacted trace, source/reader version, rule outcome, retry state and accountable resolution |

## 4. End-to-end experience

### 4.1 Invoice

1. A user chooses a workspace and captures an original photo or uploads a PDF. The app counts pages, checks quality and offers retake guidance without silently discarding faint pages.
2. The backend stores an immutable source identity and page map. It routes digital text PDFs to the deterministic table reader and scanned/photo pages to OCR. It records all extraction candidates and failures.
3. Validation compares header, row identity, batch, expiry, quantity, pack, price, GST and invoice totals. Adjustments such as freight, schemes and rounding are separate; a grand-total fit never authorizes replacement of source-supported rows.
4. The review screen groups exceptions. Tapping a value highlights its page and region, or clearly says that location is unresolved. The reviewer can edit, recheck a selected region and confirm inventory product/unit links.
5. Save returns the persisted document revision. Reload and a second device show the same revision. Exact-file reupload may replay confirmed corrections; a different file can use only approved evidence-backed supplier rules.
6. Approval freezes an immutable export version. Send posts that version to one configured connector. The screen distinguishes queued, received, imported with voucher ID, rejected and reconciliation needed.
7. An edit after import creates a proposed amendment. It cannot reuse the old delivery success or silently post a second purchase.

### 4.2 Prescription

Capture, literal transcription, source-linked recheck and pharmacist confirmation use a separate document type and benchmark. A prescription cannot enter the purchase-entry connector. Medicine catalogue candidates are suggestions only and never replace printed wording automatically.

## 5. Functional requirements

### 5.1 Evidence, pages and review

| ID | Requirement | Acceptance |
| --- | --- | --- |
| EVD-01 | Persist original file hash, source page count/order, processed page map, transformations and per-page extraction state. | A 1-, 2- and 5-page digital/scanned/photo set accounts for every content page, including failed pages. |
| EVD-02 | Each critical field and row has a source page and normalized region or an explicit no-region reason. Keep literal text and candidate provenance. | Web and Android open the correct page/crop after rotation and duplicate-copy handling. |
| EVD-03 | Separate raw OCR, deterministic parse, AI candidate, learned proposal and reviewer value. | A conflicting AI answer cannot silently overwrite a supported batch, quantity, rate or GST head. |
| EVD-04 | Region recheck uses a bounded source crop and is invalidated after edits, page changes or document revision. | Old responses cannot overwrite a newer save; out-of-range crops are rejected. |
| EVD-05 | Verify a signed e-invoice QR cryptographically before calling it verified; compare its header values with the paper. | A decoded but invalid-signature token is not trusted; mismatch is visible. |
| REV-01 | Critical errors require correction or an audited exception acknowledgement; evidence and approval remain distinct. | No failed page, missing row or unresolved critical field is silently approved. |
| REV-02 | Save is checked by server read-back and revision ID. | Main Save and Use & save survive app restart, second device and exact reupload. |

Critical invoice fields are supplier identity/GSTIN, buyer where applicable, invoice number/date/type, source page set, row description, batch, expiry, pack, billed/free quantity, rate, amount, GST rate/head and totals. A printed absence is represented as absent, not missing OCR.

### 5.2 Photo and scan quality

| ID | Requirement | Acceptance |
| --- | --- | --- |
| CAP-01 | Preserve original bytes in encrypted, tenant-scoped storage when retention is enabled; link derivatives and expiry policy. | Original and processed hashes can be traced without exposing public object URLs. |
| CAP-02 | Provide advisory pre-upload feedback for blur, glare, clipping, perspective and small print. | Reviewer can retake or continue; warnings are calibrated on a held-out photo set. |
| CAP-03 | Scanned PDFs process page by page with ordered results and retry of a failed page. | A successful first page cannot hide a failed second page. |
| CAP-04 | Use a second provider only for uncertain page/field cohorts with demonstrated accuracy gain. | Routing has recorded reason, model version, latency and cost; no default duplicate full-document spend. |

### 5.3 Supplier learning and catalog mapping

| ID | Requirement | Acceptance |
| --- | --- | --- |
| LRN-01 | Save correction outcomes as exact-file replay, proposed rule, unsupported or abstained, with source evidence. | User and support can see which outcome occurred. |
| LRN-02 | Scope proposed rules to workspace, verified supplier, document class, layout and field locator. | A rule never crosses a tenant or changed layout automatically. |
| LRN-03 | Require independent examples, supervisor promotion, shadow evaluation, versioning and rollback. | Fresh-invoice holdout improves with no new silent critical error; rollback is tested. |
| MAP-01 | Present candidate ERP products using name, strength, form, pack, manufacturer and approved aliases. | Similar molecule/brand alone cannot auto-substitute the stock item. |
| MAP-02 | Require a confirmed pack-to-stock-unit conversion, including billed and free units. | No automatic multiplication from notation such as 15's tablets; export preserves both printed and ERP units. |

### 5.4 Delivery and operations

| ID | Requirement | Acceptance |
| --- | --- | --- |
| ERP-01 | Version the approved purchase payload and hash it. Preserve document type and corrections. | Same version retries idempotently; changed version requires amendment/reversal. |
| ERP-02 | Keep received, imported and rejected separate; imported requires durable voucher ID. | HTTP success without voucher does not appear as imported. |
| ERP-03 | Reconcile received voucher lines, batch, billed/free quantity, tax and total; show mapping exceptions. | No unexplained stock difference in certified pilot imports. |
| ERP-04 | Publish a generic receiver conformance kit and certify SohamERP first, then other adapters separately. | Synthetic timeout, duplicate, rejection and amendment tests pass. |
| OPS-01 | Meter pages, model route, latency, retries, accepted vouchers and support time per workspace. | Admin can measure cost per accepted voucher and p90 review time without raw personal data in logs. |
| OPS-02 | Maintain tenant and role isolation on source, crops, rules, catalog and delivery endpoints. | Cross-workspace access and privilege tests pass. |

## 6. Reference architecture

    Android / Web capture and review
              |
              v
    Auth + organization/workspace context
              |
              v
    Intake -> encrypted original + derivative + ordered page manifest
              |
              v
    Extraction orchestrator -> digital table reader / scanned OCR / targeted second read
              |
              v
    Candidate store + evidence locator -> verification and exception engine
              |
              v
    Human review + catalog mapping -> immutable approved revision
              |
              v
    Delivery outbox -> signed connector -> ERP receiver -> voucher receipt
              |
              +-> rule proposals / quality metrics / problem queue

Keep the existing FastAPI application and background OCR jobs. Add bounded services and additive database tables; do not create a second independently mutable invoice database. Each request resolves its organization and workspace server-side. The same document revision and evidence are returned to both devices.

### 6.1 Component responsibilities

- Intake owns file sniffing, page identity, transformations, retention and quality signals. It never decides medicine or tax values.
- Extraction adapters return candidates with literal text and geometry when available. The deterministic parser and AI cannot overwrite each other's candidate record.
- Evidence locator binds a candidate to a page/row/region using original-to-processed coordinate transforms. Missing or ambiguous binds are explicit.
- Verification owns arithmetic, GST, identity, page coverage and source conflict checks. It does not edit the printed value to force reconciliation.
- Review owns versioned edits and acknowledgements. Approval freezes a snapshot of payload, source hash, mapping decisions and reviewer identity.
- Learning consumes only reviewed outcomes, proposes scoped changes and evaluates them before promotion. A rule can abstain or be rolled back.
- Delivery outbox owns retries and idempotency. Connector adapters map the approved snapshot; receiver receipts are stored separately from transport acknowledgements.

### 6.2 Additive data model

| Record | Essential fields | Purpose |
| --- | --- | --- |
| ExtractionRun | document, workspace, source hash, parser/model/prompt versions, page map, started/finished, cost, status | Immutable provenance for every scan/retry |
| FieldEvidence | extraction run, field path, stable row ID, page, region, literal text hash, origin, confidence, transform version | Source click-through and conflict checking |
| ReviewRevision | document, parent revision, changed fields, actor, time, acknowledged checks, read-back hash | Same saved state on all devices |
| RuleCandidate and RuleEvaluation | workspace, supplier, layout, field locator, examples, state, holdout result, version | Governed learning and rollback |
| ProductLink | workspace, supplier product alias, ERP item ID, pack/unit conversion, approver, version | Reviewed stock master mapping |
| DeliveryVersion and Receipt | document revision, connector, payload digest, idempotency key, state, voucher reference, received line summary | Safe import and amendments |
| UsageEvent | workspace, route, pages, latency, model units/cost, outcome | Quality and unit economics |

Existing Document, DocumentCorrection, SupplierColumnLesson, InventoryItem, PushDelivery and AuditLog records remain readable. Migrations add references and new tables without bulk rewriting approved invoices. Retention and deletion apply to originals, derivatives and evidence text together.

### 6.3 API evolution

- Extend document detail with extraction run/revision identifiers and evidence summaries; old clients ignore optional keys.
- Add a field-evidence read route and bounded source-crop route under owned document IDs. Existing source-page and recheck routes remain available.
- Add rule proposal/list/approve/rollback and product-link confirmation routes with owner/supervisor checks.
- Keep the current push route while recording a delivery version and durable receipt. A later amendment route requires receiver support and explicit confirmation.
- Expose aggregate quality and usage reports by workspace, not raw patient or invoice text.

All new responses use stable field paths and schema versions. Every write supplies the expected document revision; stale writes return a conflict with the current revision instead of overwriting another device.

## 7. Decision policy

For digital PDFs, table cells with consistent row geometry, source text and line arithmetic are the primary row candidate. AI can propose a different reading, but replacing product identity, batch, quantity or price requires independent source evidence or a reviewer. Printed freight, insurance, schemes, discount and rounding are separate invoice adjustments. A grand-total match by itself cannot justify extra or missing medicine rows.

For photos/scans, absence of reliable geometry increases review priority. A high model confidence is not proof of correctness. A second read is compared by field and row identity; disagreement is shown. If a value cannot be read, it stays unresolved. Quantity, pack and unit are distinct.

Rules learn from approved corrections only when the supplier/layout and printed locator are established. Candidate rules run in shadow before promotion and record their outcome on every future scan. A confirmed ERP product link does not alter the literal invoice name.

## 8. Quality and release gates

Build a frozen human-labelled holdout with explicit absent/unreadable labels, spanning known/unseen suppliers, digital and scanned PDFs, phone photos, single/multi-page documents, repeated copies, GST slabs, credit notes and difficult tables. Handwriting has a separate holdout. Never use holdout answers to tune the reader.

| Metric | Proposed pilot gate | Reporting rule |
| --- | --- | --- |
| Content-page recall | 100% on labelled pilot set | Count every original content page and every retained/failed page |
| Printed-invoice row recall | At least 99.5% | One-to-one source row matching; unexpected rows reported |
| Silent accepted critical errors | Zero in release holdout | Report numerator and denominator; zero is not a universal guarantee |
| Save durability | 100% of scripted cross-device/reupload cases | Verify server revision after save |
| Duplicate vouchers | Zero in retry/amendment tests | Check ERP-side voucher count |
| Accepted import | At least 99% of approved supported pilot invoices | Requires voucher ID and row/tax reconciliation |
| Review speed and cost | Improve versus measured manual baseline | Report median/p90 time and cost per accepted voucher by cohort |

Release requires green CI with private corpus access, all 135 known regression entries, complete holdout labels for claimed cohorts, migration test, Android/web checks, live-provider smoke where applicable, connector conformance and explicit production approval. The partner repository must carry the same runtime and documentation without tool-attribution text in source or reachable commit messages.

## 9. Rollout and compatibility

1. Ship evidence storage and endpoints behind a flag; shadow-record candidates and coordinates without changing decisions.
2. Enable source highlighting for pilot reviewers; measure verification time and incorrect crop rate.
3. Enable quality warnings and targeted second reads by cohort only after holdout gain.
4. Enable approved supplier rules and product links per workspace with rollback.
5. Certify the first SohamERP receiver in sandbox, then witness three pharmacies' imports and failure drills.
6. Publish measured results and operational terms; expand to other ERP adapters only after the first accepted-voucher flow is repeatable.

Android 1.5.14/1.5.15 clients continue to review and save while new optional fields are introduced. New write behavior is gated server-side; existing approved/pushed records are never silently reprocessed.

## 10. Dependencies, non-goals and open decisions

SohamERP must choose its first receiving product, receiver owner, sandbox HTTPS endpoint, voucher schema, product-master mapping and server domains. The deployment partner must perform DNS/TLS, backup/restore and secret rotation. MediScan needs consented pilot material and independent human reviewers. A private holdout and original files never enter the partner source repository.

This increment excludes unreviewed purchase posting, clinical interpretation or automatic dispensing, universal handwriting guarantees, automatic drug substitution, and certification of every ERP connector. The first implementation decision is to freeze the evidence schema and holdout annotation contract; the first external decision is the SohamERP receiver product and sandbox.
