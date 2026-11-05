# MediScan next product increment: scope of work

Version 1.0 | 4 November 2026 | Status: proposed scope, no production change

## 1. Outcome and boundary

MediScan will become a dependable, independent invoice-to-purchase product for Indian pharmacies that use SohamERP or another ERP. The paid outcome is a reviewed invoice whose products, batches, quantities, taxes and total are accepted by the receiving ERP, with the source and every correction traceable. Handwritten prescriptions remain a separate pharmacist-reviewed transcription workflow.

This scope extends the current Android, web, OCR and connector stack. It does not replace the existing document API, organization/workspace model, review screen, supplier lessons or webhook contract. No release may claim universal or error-free OCR. A successful arithmetic total alone is not proof that every item was captured correctly.

## 2. Verified baseline on 4 November 2026

- The source and SohamERP repositories both contain the four-page Sai Venkata GST invoice parser fix and Android 1.5.15 source. The latest deployed backend is the invoice fix at source commit 9823462. Local extraction of D-00004 returns four unique products, four batches, their printed quantities and PTR, and IGST rather than CGST.
- Already built: multi-page PDF and photo intake, page manifest and source preview, field recheck and saved correction replay, scoped supplier lessons, required review checks, organization/workspace users and licences, problem-report administration, optional inventory catalog matching, file/Tally pilot/HTTPS/desktop connectors, and idempotent delivery safeguards.
- The private regression corpus now contains 135 invoice entries. Only a small subset carries hand-check notes and many fields and rows are not independently labelled. It is a regression asset, not a measured accuracy result.
- Source CI on the Android 1.5.15 version-only commit failed before backend tests because its private-corpus SSH fetch was denied. The prior OCR commit passed CI and deployed. Repair the corpus credential and rerun current-main CI before treating 1.5.15 as a fully gated release.
- Partner hosting instructions exist, but a SohamERP server installation, receiver-created voucher and restore drill have not been witnessed.

The detailed functional and technical design is in OCR_PRODUCT_PRD_V2.md. Earlier PRDs remain historical context; a requirement is not marked shipped merely because an earlier document proposed it.

## 3. Work packages and exit criteria

### WP0 — Restore release evidence and freeze the benchmark

Deliver:

1. Restore read-only private-corpus access in source CI without exposing invoice PDFs or a deploy key in logs. Re-run backend, web, Android, live-model smoke and APK gates on the same commit.
2. Freeze a separate holdout with consent, document hashes and reviewer provenance. Label every content page, every product row and every critical field; record explicit absent, unreadable and ambiguous states. Keep new supplier/layout examples out of rule development.
3. Run the existing strict scorer per digital PDF, scanned PDF, phone photo, unseen supplier and credit-note cohort. Report exact denominators, excluded cases, row/page recall, critical-field accuracy, silent accepted errors, reviewer time and per-document cost.

Exit: current-main CI is green; every case counted as eligible has complete human labels; results are reproducible by build SHA. A 500-case holdout is the proposed scale before a public accuracy claim; an initial smaller pilot is reported with its actual denominator.

### WP1 — Source-linked extraction and review

Deliver:

1. Store immutable extraction runs and per-field evidence: source file hash, page, row identity, normalized bounding region, literal text, extractor, confidence and transformation from original to processed page.
2. Add source-region highlighting and a bounded crop to web and Android review. Selecting an invoice field opens the exact source page and region; fields without reliable coordinates say so. A model-reported location is not silently promoted to verified evidence.
3. Keep the deterministic table candidate and AI second reading as separate candidates. A grand-total disagreement caused by freight, schemes or rounding must not replace source-supported batch, quantity or price values. Reviewers see the conflict and choose against the page.
4. Confirm the e-invoice QR signature before labelling QR values verified. Until then, decoded QR contents are cross-check evidence, not cryptographic proof.

Exit: 100% of critical fields on the pilot holdout either have a valid source region or an explicit no-region reason; source click-through works across page order, rotation and Android/web; candidate conflicts never silently overwrite a source-supported row.

### WP2 — Photo and scanned-page quality

Deliver:

1. Preserve original upload bytes under the configured encrypted retention policy and record each processing derivative. Add pre-upload guidance for clipping, glare, perspective, blur and small print, with retake and override.
2. Keep every nonblank scanned page visible and accounted for, even if one extraction fails. Show page-attempt status and support page-only retry.
3. Benchmark a second OCR provider only on uncertain fields or pages. Route by measured improvement, latency and cost; do not run two full models on every invoice by default.

Exit: no labelled content page is silently dropped in the pilot; quality warnings reduce retakes or critical errors in a measured photo cohort; provider fallback has a positive held-out benefit and bounded cost.

### WP3 — Safe supplier learning and stock matching

Deliver:

1. Turn corrections into proposed rules with supplier identity, workspace, document class, layout signature, field, source locator and supporting examples. Require review, shadow evaluation on unseen invoices, promotion and rollback. Show applied, abstained, conflicted and retired outcomes.
2. Retain exact-file replay. A new file from the same supplier may reuse only a rule whose printed evidence matches. Typed addresses, quantities and medicine names do not become general rules by themselves.
3. Extend optional inventory matching into a reviewer-confirmed ERP product link. Match brand, strength, form, pack and manufacturer; keep the original invoice name. Unit conversions and free quantities need an explicit approved mapping. Similar molecule or fuzzy name alone is never an automatic substitution.

Exit: same-supplier fresh invoices improve on a held-out set without new silent critical errors; rule rollback is demonstrated; no unconfirmed catalog match or unit conversion enters an approved ERP payload.

### WP4 — Accepted purchase entry and connector certification

Deliver:

1. Define a versioned purchase-entry contract for invoice, credit note and return semantics, line-level batch/expiry, billed/free quantity, pack/unit, scheme, discounts, HSN and tax. Keep old clients and connector versions readable.
2. Supply a SohamERP receiver conformance kit with synthetic success, rejection, timeout, duplicate and correction examples. The partner implements a sandbox receiver and nominates the first receiving product.
3. Record received versus imported outcomes, durable voucher ID, row/tax reconciliation and immutable approved payload digest. Introduce an amendment/reversal handshake before allowing edited imported invoices to be sent again.
4. Keep the generic signed HTTPS connector and file/desktop routes for customers outside SohamERP. Certify each ERP mapping separately; a generated file or HTTP 200 is not an imported voucher.

Exit: witnessed imports at at least three pilot pharmacies, with no duplicate voucher in retry tests and at least 99% of approved supported pilot invoices acknowledged as imported and reconciled. Unsupported documents stay in a visible exception queue.

### WP5 — Operations and commercial pilot

Deliver:

1. Admin views for exception age, correction themes, page failures, supplier/layout drift, model cost and latency by workspace; privacy-safe support diagnostics and service targets.
2. Tenant-isolation and role tests for every new source, evidence, rule and delivery route. Run backup, restore, secret-rotation and incident drills on the chosen SohamERP deployment.
3. Measure median and p90 time from scan to accepted voucher, cost per accepted voucher, support minutes and paid-pilot conversion. Use these measurements to set plans and usage limits.

Exit: a repeatable onboarding checklist, a witnessed server restore, documented support ownership, and a paid-pilot decision based on observed economics.

## 4. Sequence, ownership and dependencies

WP0 is the release prerequisite and measurement foundation. WP1 starts next because it makes both accuracy review and learning auditable. WP2 and WP3 can proceed after the evidence contract is stable. WP4 needs the product/version contract and SohamERP sandbox. WP5 runs through the pilot. Work packages may overlap only when their interface contracts and evaluation set are frozen.

MediScan engineering owns APIs, clients, migrations, tests, privacy controls and generic connector conformance. A human reviewer owns ground-truth labels and adjudication. SohamERP owns its receiving product, sandbox endpoint, voucher mapping and server operation. Pilot pharmacies provide consented documents and measured review sessions. The first SohamERP product, domains, proxy configuration, receiver owner and sandbox credentials remain partner decisions.

## 5. Change and release controls

- Add schema fields and tables through backward-compatible migrations; keep Android 1.5.14/1.5.15 able to read old and new documents.
- Store raw OCR, source evidence, reviewer edits and approved export versions separately. Never rewrite a historical approved payload.
- Feature-flag new routing, learning and ERP amendments. Run shadow comparisons before enabling write actions. A failed gate blocks promotion; rollback restores the previous reader or rule without deleting the source.
- Run unit/integration tests, all 135 known invoice regressions, a frozen holdout, cross-device save/reupload tests, migration and connector conformance tests in both repositories. Compare shared runtime files after each mirror.
- Publish a release note with build SHA, APK version, migration, corpus/holdout result and known limitations. Merge and production deployment are separate approval steps.

## 6. Scope exclusions

This increment does not promise universal handwriting recognition, unreviewed stock posting, automatic medicine substitution, clinical dosing advice, certified imports into every ERP, or a public percentage based on partially labelled invoices. Prescription export and clinical decisions require a separate governed pilot.

## 7. Definition of done

The increment is done when the pilot user can see and verify the source behind every critical value, saved corrections survive devices and reuploads, the labelled holdout shows no silent accepted critical errors, a supported approved invoice becomes one reconciled ERP voucher, exceptions remain visible, and both repositories pass the same release gates. The corresponding commercial claim must state the document mix, denominator and measured results.
