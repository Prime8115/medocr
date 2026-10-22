# MediScan Verified Purchase Entry — Product Requirements Document

- **Version:** 0.1 (implementation proposal)
- **Date:** 22 October 2026
- **Owner:** Product and Engineering
- **Status:** Ready for implementation planning; numeric targets are proposed pilot gates, not achieved results
- **Shipped baseline:** GitHub `main` at `77768f8` (PR #25 merged)

## 1. Decision and product promise

Build MediScan as a **reviewed invoice-to-stock bridge** for Indian pharmacies that already use billing or inventory software. A staff member captures a supplier invoice, checks uncertain values against the printed source, approves the purchase entry, and sends it to that software with a visible receipt. The product remembers safe supplier-specific reading rules and reduces repeated correction work.

The launch promise is **traceable, faster purchase entry with visible uncertainty**. It is not an error-free OCR guarantee. An arithmetic match does not prove that a medicine, batch, expiry, pack, party or quantity was read correctly. No critical field should be silently guessed to make an invoice appear complete.

The first buyer is an independent pharmacy or small chain with recurring supplier invoices and an incumbent billing system. Distributors and large chains are later segments that need separate workflow validation. A prospect's own difficult invoices and accepted imports, rather than a generic OCR demonstration, determine product-market fit.

## 2. Why this build, and what already exists

Low-cost Indian pharmacy packages already advertise purchase OCR. As observed on 22 October 2026, [ChemistMitra](https://chemistmitra.com/pricing/) lists ₹1,999/year with a post-trial invoice allowance, [MedixoRx](https://medixorx.in/index.html) lists AI-scan plans from ₹2,999/year, and [Nesayo](https://nesayo.com/pricing) lists dealer-bill import from ₹249/month. Their units, tax treatment and included services differ. These are vendor-advertised prices, not verified checkout quotes or accuracy measurements. MediScan must earn its price through accepted purchase entries, correction time saved and trustworthy handling of difficult files.

The current product already has PDF/photo intake, page-level handling for scanned PDFs, structured invoice and prescription extraction, required-field review, source-page preview, field/page recheck, offline Android capture, web review, exact-file correction replay, scoped supplier lessons, exports, a desktop agent, and CI/live-AI/APK/deployment workflows. The October accuracy release is [PR #25](https://github.com/Prime8115/medocr/pull/25). Do not rebuild these as new features. Extend and measure them.

Current gaps are an independently human-labelled holdout; preserved originals and field/row source coordinates; photo-quality guidance; a verified address locator across new invoices; versioned, reversible supplier learning; a witnessed end-to-end import into a named pharmacy system; complete staff/audit/data-lifecycle controls; usage and cost metering; and operational release gates. The older [production rebuild PRD](PRD.md) describes an obsolete prototype state. The [photo and handwriting PRD](PHOTO_HANDWRITING_PRD.md) remains the detailed extraction baseline; this document defines the marketable workflow and release decision.

## 3. Outcomes, non-goals and boundaries

### Outcomes

1. Staff can account for every uploaded page, inspect every uncertain medicine row, and approve an invoice without losing the original source.
2. An approved invoice becomes one accepted, reconcilable purchase entry in a named incumbent system, with safe retry and no duplicate stock receipt.
3. The same supplier's later invoices need fewer repeated corrections when there is printed evidence for a rule; unsafe rules abstain and can be reversed.
4. An owner can see who captured, corrected, approved and exported each document, what failed, how many pages were used, and the cost and time saved.
5. A pilot demonstrates a measurable improvement over manual entry without an unacceptable critical-field error rate.

### Non-goals for the invoice launch

- Replacing pharmacy billing/POS, tax filing, e-invoice generation or clinical systems.
- Automatically approving all invoices, copying a typed address to different invoices, or auto-substituting medicines.
- Claiming “100% accuracy” from a small or template-heavy test set.
- Autonomous interpretation or dispensing from handwritten prescriptions. Prescription work has a separate gate in §11.
- Supporting every billing system at launch. One certified connector is more useful than many unverified mappings.

## 4. Users and primary jobs

| User | Job | Required result |
| --- | --- | --- |
| Counter or purchase-entry staff | Capture invoice and resolve exceptions quickly | Clear source, page count, editable rows, safe save/retry |
| Pharmacist or supervisor | Confirm high-risk medicine and batch information | Evidence beside each disputed value and explicit approval record |
| Shop owner | Prevent duplicate purchase entries and manage costs | Export receipt, correction history, staff access and usage report |
| Support operator | Diagnose an extraction or import failure | Redacted diagnostics, versioned pipeline/rule identifiers and replay path; no casual access to patient or customer source files |
| Integration partner | Import approved purchase data | Stable, versioned payload and acknowledgement/reconciliation contract |

The core journey is **capture → page accounting → extraction → exception review → approval → export → receiving-system acknowledgement → searchable history**. Every transition has a timestamp and actor. A later edit invalidates approval and export readiness until re-reviewed. A repeat upload shows whether MediScan reused a reviewed identical file, started a fresh read, or applied a supplier rule. It must never appear to have “learned” when it merely reopened an identical saved record.

## 5. Release scope and functional requirements

Priority meanings: **P0** is required for a paid invoice pilot; **P1** is required for a repeatable commercial release; **P2** is conditional expansion. Each requirement needs an owner, implementation design and test case before development.

### 5.1 Evidence, capture and page integrity (P0)

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| VE-01 | Keep encrypted original upload bytes separately from processed derivatives. Record hash, MIME, dimensions/page count, transformations, source group/part and storage version. | Reviewer can retrieve the exact owned original; derivative and original are never confused; cross-shop access is denied. |
| VE-02 | Maintain a page manifest: input pages, retained pages, extraction attempted/completed/failed, and split invoice parts. A faint or sparse page is kept when emptiness is uncertain. | Every input page has a terminal state; no 2-page file silently appears as a complete 1-page invoice in the frozen regression set. |
| VE-03 | Provide capture feedback for blur, glare, cut-off edges, orientation and too-small text, with retake guidance. Do not silently discard a submitted page. | Quality advice is actionable; users can proceed with a visible warning; poor pages remain accounted for. |
| VE-04 | Attach page and bounding region to each critical field and row when evidence is found. Distinguish observed source, model-reported context, derived arithmetic and human entry. | Selecting a value opens its actual page/region; missing coordinates are labelled “source not located,” not fabricated. |
| VE-05 | Allow a bounded crop or selected-page recheck; suggestions never overwrite saved values without acceptance. | Recheck uses the selected source and logs old/proposed/accepted values; stale suggestions invalidate after edits. |

### 5.2 Review and pharmacy semantics (P0)

Critical invoice values are supplier identity/GSTIN, invoice number/date, buyer/ship-to identity when used in import, product, pack, billed and free quantity, batch, expiry, billed rate, line amount and tax. A configurable receiving system may add other required fields.

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| VR-01 | Route missing, ambiguous, contradictory or low-evidence critical values to review. Show the reason and source. Explicit absence is different from unreadable or unprocessed. | No unresolved critical requirement can be exported as verified; an exception acknowledgement names actor, time and reason. |
| VR-02 | Preserve literal pack wording such as `15's tablets` separately from billed quantity, free quantity, unit and derived base-unit count. Never infer billed quantity from a pack label. | Frozen pack/quantity cases pass; ambiguous conversions stay in review. |
| VR-03 | Display one row per purchase line with source evidence and independent row-count/page-coverage checks. Do not treat matching invoice totals as proof that rows are complete. | Missing/duplicate line scenarios trigger review even when totals happen to reconcile. |
| VR-04 | Identify likely duplicate invoices using shop, supplier identity, invoice number/date, value and file identity, including linked PDF parts. | Reupload and export retries cannot create a second accepted stock receipt without an explicit override and audit. |
| VR-05 | Handle credit/debit notes, returns, free goods and schemes as distinct document/line semantics before sending them to stock. | Import cannot turn a credit into a purchase or free quantity into billed quantity; unsupported cases remain manual. |
| VR-06 | Keep supplier, bill-to and ship-to address blocks distinct. New-invoice address reuse needs a unique printed locator or explicit review; typed street text is not a supplier rule. | Wrong-party and adjacent delivery/reference cases are caught on the frozen address set. |

### 5.3 Learning that can be trusted (P0 foundation; P1 promotion)

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| VL-01 | Record a structured outcome for each correction: identical-file replay, proposed supplier label/column/region rule, unsupported field, insufficient evidence, ambiguous supplier/layout, conflict or rule abstention. | Reviewer sees a plain explanation; support can trace the decision without logging raw address or prescription text. |
| VL-02 | Scope candidate rules to shop, verified supplier identity, layout signature and applicable document class. Store printed locator, expected field, source examples, rule version and creator. | A rule cannot transfer across shops or changed supplier/layout; a single typed address cannot become a cross-invoice rule. |
| VL-03 | Promote rules only after independent evidence and owner/supervisor approval. Evaluate against held-out invoices and retire or roll back rules that cause wrong applications. | Rule history and affected documents are queryable; rollback restores the previous interpretation for future scans. |
| VL-04 | Show on each new invoice whether a rule applied, abstained, conflicted or was retired. Keep raw OCR and proposed learned value available for comparison. | Same-supplier fresh-invoice test proves a justified improvement or an explicit abstention. |

### 5.4 Certified purchase export (P0 one system; P1 second system)

The launch team selects the first named pharmacy system from pilot demand and its supported import contract. Existing CSV/Tally/webhook/desktop-agent capabilities are a starting point, not certification.

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| VX-01 | Define a versioned purchase-entry payload mapping supplier, buyer, invoice, line, pack, quantity/free quantity, batch, expiry, HSN, tax, scheme and totals. Document required and unsupported fields for the named system. | A real import round-trip is witnessed with the customer or vendor; received values match the approved review. |
| VX-02 | Use a stable external idempotency key and delivery state machine with acknowledgement, failure reason and retry. Keep approval version tied to the exported payload. | A retry or reupload cannot double-post; edited invoices require a new approved export version or explicit reversal. |
| VX-03 | Provide an exception queue for master-data mismatch, unsupported batch/expiry, invalid tax, connection failure and receiving-system rejection. | Staff can correct, retry, or send to manual entry without losing the source or previous delivery attempts. |
| VX-04 | Reconcile accepted rows, quantities and totals with the receiving system; record the external receipt/reference. | Pilot batch has a signed-off receiving-system receipt and no unexplained stock difference. |

### 5.5 Operational and commercial controls (P1)

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| VO-01 | Roles for staff, supervisor, owner and scoped support; approval/export permissions; access logs for source and prescription data. | Tenant-isolation and privilege tests pass, including source-page and export endpoints. |
| VO-02 | Audit field revisions, acknowledgements, learning changes, approvals, connector configuration and delivery. Provide correction and incident export without exposing secrets. | A disputed purchase entry can be reconstructed from immutable event history. |
| VO-03 | Encrypt connector credentials and retained originals, define retention/deletion policy with counsel, and exercise backup/restore and incident response. | Restore drill recovers documents, rule versions and deliveries; deletion request is testable and logged. |
| VO-04 | Meter source pages, billable pages, retries/rechecks, model calls, storage, support actions and approved imports by shop. Publish a clear page-count rule before billing. | Owner and internal finance reports reconcile to the same event ledger; retries caused by MediScan are not silently billed twice. |
| VO-05 | Monitor page failures, empty-row responses, critical-field review rate, repeated corrections, import failures, provider cost and service health. Alert on regressions. | A production incident identifies affected shops/documents and supports replay or rollback. |
| VO-06 | Gate release on backend, migration, web/mobile compatibility, private holdout, live extraction and APK from the intended commit; verify production health and rollback. | Release record links all checks, deployed revision and APK; a failed gate blocks rollout. |

## 6. Data and interface contract

The implementation design should extend existing document, correction, supplier-lesson and push-delivery models without rewriting stored reviews.

- **Source manifest:** shop, document group, original hash/ref, processed derivative refs, original/retained page indices, transformation chain, quality findings and retention class.
- **Evidence record:** document/version, field or line ID, source page and normalized bounding region, observed text (access controlled), evidence type, extractor/rule version and confidence calibration bucket. Unknown location is explicit.
- **Correction and rule decision:** before/after value, actor, source evidence ID, rule candidate/promotion/abstention reason, version and rollback link.
- **Approval and export:** approved field version, reviewer/acknowledgements, receiving-system profile version, idempotency key, attempts, acknowledgement and external receipt.
- **Usage ledger:** immutable page/model/storage/export events, billable decision and reason. A split PDF retains one source group while each page remains countable.

APIs must enforce shop ownership on every source, crop, lesson, review and export path. Source and evidence endpoints use bounded page/crop parameters and short-lived access; logs and analytics exclude raw medical or address content. Schema migrations and old-client behavior must be specified in each implementation PR.

## 7. Evaluation contract and release gates

No accuracy percentage is publishable until its numerator, denominator, document mix and annotation method are recorded. Freeze a consented, human-labelled holdout that is never used to write supplier rules or prompts. An initial pilot target is at least **500 invoice documents** across at least **25 supplier/layout combinations**, including at least **100 scanned PDFs**, **100 phone photos** and **50 multi-page sources**. These are collection targets, not evidence of statistical certainty; repeated templates do not count as independent suppliers. Label every page, line and critical field, including explicit absence and illegibility. Keep the files and reviewer notes private.

Report exact-match critical-field accuracy; page and row recall/precision; wrong-party and pack-as-quantity errors; escaped errors after approval; review/abstention rate; first and repeat correction time; fresh-invoice lesson application and wrong-application rates; accepted-import rate; p50/p95 capture-to-approved-entry time; and cost and contribution margin per approved invoice. Segment by digital PDF, scanned PDF, photo, supplier seen/unseen, page count, quality and script. Report confidence intervals; zero observed errors in a small sample is not proof of zero risk.

| Gate | Proposed criterion for a paid invoice pilot |
| --- | --- |
| Page integrity | 100% of pages in the frozen multi-page set accounted for as retained/processed/failed; no silent lost page. |
| Critical review | Every unresolved critical requirement blocks ordinary export or has a logged supervisor exception; no fabricated source region. |
| Error audit | Double-review a blind sample of approved entries; zero **known** severe escaped errors before limited paid rollout. Any severe escape triggers root-cause fix and a new frozen evaluation. Publish the denominator and uncertainty internally. |
| Learning | No cross-shop application; no known wrong promoted rule on holdout; eligible repeat corrections trend down on fresh same-supplier invoices. |
| Import | One named connector passes a witnessed batch, reconciles approved rows/quantities/totals, and safely replays failures without duplicate stock. Target ≥99% accepted imports for valid approved payloads; investigate every rejection. |
| Security/operations | Tenant and role tests pass; restore and rollback drills succeed; no unresolved high-severity access or data-loss finding. |
| Commercial | In a controlled pilot, median staff time per accepted entry improves by a proposed ≥30% versus manual entry without a worse severe-error outcome; real retries, support and storage leave positive contribution margin. |

These thresholds are **release hypotheses** to calibrate with a measured baseline and pilot buyers, not current MediScan performance. Do not auto-approve solely because a confidence number or arithmetic check passes.

## 8. Pilot, packaging and go-to-market

Recruit **5–10 pharmacies** that already use billing software and have varied supplier formats. Choose the first certified connector by their actual software usage. During a bounded trial, process their own difficult invoices in parallel with their normal purchase entry; compare accepted values, correction minutes, failure causes and staff preference. Ask buyers what time saving and support promise justify payment before publishing a tariff.

Offer a sample evaluation first, then test a one-shop **Core** package with a clear monthly page allowance, source-backed review and one certified export. A **Growth** package can add staff roles, larger allowance, supplier analytics, second validated connector and support commitment. Handwritten prescription review is an opt-in add-on only after its separate safety gate. As a pricing **experiment**, test ₹249–₹399/month Core and ₹799–₹1,199/month Growth, with transparent overage and annual options; these are not approved prices. Billable unit is the **source page**, not an ambiguously defined “scan”; state how split PDFs, repeated uploads, failed pages and rechecks count. Validate willingness to pay and positive margin before launch.

Marketing claims should name the measured workflow and test conditions: “reviewed purchase entries imported into [certified system]” and a documented time saving on buyer files. Do not advertise universal error-free OCR, unsupported connector compatibility, or autonomous prescription safety.

## 9. Delivery sequence and dependencies

| Window | Deliverable | Exit decision |
| --- | --- | --- |
| Days 0–21 | Consent and annotation workflow; frozen baseline by document class; source manifest and original retention design; page/row metrics; pilot and connector selection. | Page accounting regression and baseline report are reproducible. |
| Days 22–45 | Original/source regions, quality guidance, review exceptions, pack/quantity and address evidence, learning-decision trace; one connector mapping and sandbox round-trip. | Blind review finds no silent severe known case in the frozen set; connector accepts a witnessed sample. |
| Days 46–70 | Rule approval/rollback; duplicate/credit-note and import exception queues; roles, audit, encryption, retention, backup/restore and usage ledger. | Import/retry and tenant safety gates pass; pilot unit economics are visible. |
| Days 71–90 | Controlled paid pilot; measure time/error/import outcomes and support; refine the first certified connector and pricing. | Launch only if §7 gates pass. Otherwise continue a review-assisted pilot and publish the failing gate internally. |

Sequence dependencies: the labelled set precedes model/rule tuning; source evidence precedes safe general address learning; a real connector contract precedes billing claims; usage and support costs precede final pricing. Each increment should preserve manual correction and old-client compatibility until the APK is adopted.

## 10. Separate prescription track (P2)

Handwritten prescriptions involve different users, privacy and error consequences. Build a consented holdout by writer, script and photo quality; source evidence for medicine, strength, dose, frequency and duration; explicit unknown/illegible states; pharmacist sign-off; and distinct access/retention policy. No inferred missing dose, medicine substitution or direct dispensing action. Test a second reader only if it reduces clinically important ambiguity enough to justify its cost. Do not bundle this track into an invoice accuracy claim.

## 11. Risks and open decisions

| Risk | Response |
| --- | --- |
| AI output is plausible but omits rows or pages | Manifest, row coverage, abstention and mandatory review; live-model smoke and frozen regression. |
| Supplier learning propagates a wrong address/column | Evidence-bound candidate, scoped promotion, versioning, held-out check and rollback. |
| Cheap all-in-one competitors compress price | Sell witnessed accepted imports and time saved; keep page/provider/support unit economics visible. |
| Billing systems reject nominally valid files | Certify one named integration with real receipt and exception recovery before adding profiles. |
| Photos and handwriting vary greatly | Capture feedback, separate holdouts and claims, human sign-off where uncertainty remains. |
| Retained source and patient data increase privacy exposure | Restricted source access, encryption, retention/deletion and legal review of applicable Indian requirements. |

Decisions needed during the first pilot: which named billing system to certify; which customer segment supplies the holdout; who can approve high-risk exceptions and supplier rules; original-image retention period and deletion process; target review-time/service levels; and whether prescription processing is offered at all in the initial commercial package. Product and legal owners must confirm these before publishing terms or pricing.

## References

- [Current photo, scanned-PDF and handwriting PRD](PHOTO_HANDWRITING_PRD.md)
- [Invoice accuracy safeguards and corpus limitations](OCR_ACCURACY.md)
- [Existing integration contract](INTEGRATION.md)
- [GST e-invoice schema](https://einvoice6.gst.gov.in/content/notified-e-invoice-schema/) for distinct buyer/ship-to, quantity, tax and optional batch fields
- [CDSCO recall guidance](https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/biologicals/Guidelinerapid24.pdf) for batch traceability context
- [MeitY DPDP Rules notification](https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf); applicability and retention require product-specific legal review
