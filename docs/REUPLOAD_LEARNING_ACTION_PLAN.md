# Invoice reupload, supplier learning and address accuracy: action plan

21 October 2026 · Review of GitHub `main` at `61f3422`

## Current state

- PRs [#20](https://github.com/Prime8115/medocr/pull/20) through [#24](https://github.com/Prime8115/medocr/pull/24) are merged. They improve table boundaries, product/pack parsing, party identity and links between documents split from one PDF.
- GitHub CI, backend deployment and the 1.5.8 Android APK build succeeded for `61f3422` on 19 October. The APK is [artifact 12176669043](https://github.com/Prime8115/medocr/actions/runs/39228981841/artifacts/12176669043).
- The 21 October live Gemini check failed on a scanned invoice returning **zero** line items, although the same commit's 20 October check passed. This is a separate extraction reliability problem to investigate; ordinary CI did not catch it. [Failing run](https://github.com/Prime8115/medocr/actions/runs/39553440932).
- The earlier photo/handwriting PRD and implementation are local, uncommitted work based on pre-#20 main. Reconcile them with current main before release; #24 already implements the linked-parts feature in a different schema.

## Findings to verify against reported invoices

1. A byte-identical upload without **Scan again** does not run OCR: it returns the first nonfailed document for that shop and file hash. Since the earliest document is chosen, later corrected/reviewed copies can be bypassed. For a split PDF, #24 returns links to all parts of that earlier upload. A user may see a stale earlier result and conclude learning failed.
2. **Scan again** or a changed PDF/photo creates a fresh extraction. Edits are saved, but supplier label learning needs usable page text, a 15-character supplier GSTIN, a changed eligible field, and one unambiguous printed label. Column learning further needs one consistent table layout, unchanged supplier identity and row alignment, and enough correction evidence. Many valid edits intentionally create no lesson. The UI does not explain which condition prevented learning.
3. Address fields are not in the label learner's eligible fields. The parser may take supplier, bill-to or ship-to address from a nearby block; its buyer fallback reads words below a recognised buyer name inside a bounded column. An incorrect neighbouring line can still look address-like. Reuploading cannot fix this through current automatic supplier learning.
4. Corrections are recorded and lesson status is available for column rules, but the journey from a saved field to a lesson applied on the next invoice lacks a single trace visible to the reviewer. This makes duplicate reuse, a skipped lesson and a lesson that failed to match look the same.

These are code-path findings, not a diagnosis of each reported invoice. Obtain its document ID, upload time, shop, supplier GSTIN, exact changed field and source page to establish which path occurred. Keep customer PDFs and extracted addresses in the private corpus only.

## Ordered work

### 1. Reproduce the reported lifecycle (P0)

- For each reported supplier/distributor, follow **upload → correction → save → ordinary reupload → Scan again → next invoice from the same supplier**. Record document ID, content hash, duplicate flag, original/new field values, audit `learned` paths, lesson row/status, and `learned_filled` or `lessons_applied` outcome. Do not print raw addresses or patient information in logs.
- Add integration tests for an identical file after a correction, a forced re-scan, a changed file from the same supplier, multiple scans of the same hash, and a split PDF. Verify that saving persists and that every part belongs to the expected upload.
- Separate three outcomes in the test report: **reused corrected document**, **fresh extraction with learned rule**, and **fresh extraction where the rule abstained**. Each must have the expected review state.

### 2. Fix duplicate selection and explain reupload behavior (P0)

- On a normal duplicate upload, select the latest usable corrected/reviewed upload group rather than the earliest scan. Return its exact linked parts. Do not replace a completed human correction with a new raw OCR result.
- Make the upload result say whether it reused an earlier reviewed document or started new extraction. Label **Scan again** as a fresh read whose corrections must be reviewed; explain when the result was reused.
- Preserve explicit retry of failed documents and shop isolation. Add tests that an older scan cannot supersede a newer corrected one and that cross-shop hashes never share a document or lesson.

### 3. Make learning observable and reproducible (P0)

- Record a private, structured decision for each corrected field: learned label/column; no page evidence; missing or disputed supplier identity; ambiguous label; mixed layout; changed/reordered rows; insufficient evidence; or unsupported field. Surface a plain explanation on review and Suppliers pages. Do not expose raw source text in telemetry.
- On the next invoice, record whether each eligible rule was applied, did not match, conflicted, or retired. Keep original and proposed values and link to the page/row evidence for reviewer confirmation.
- Use shop + verified supplier GSTIN + layout signature where applicable. Measure **correction-to-lesson rate**, **lesson application rate**, **incorrect application rate**, and **repeat-correction rate** separately. A high learning rate alone is not an accuracy target.

### 4. Repair address extraction with party-specific evidence (P1)

- Build private regression cases for wrong supplier, bill-to and ship-to address lines, including two-column headers, contact lines, licence/GSTIN blocks, bank details and continuation pages. Label the correct block and whether the address is truly absent.
- Improve address selection around a party heading/name and GSTIN using source coordinates. Stop at the next party or unrelated section; require one unique candidate. If ambiguous, leave it for review instead of selecting a plausible nearby line.
- Apply learned address *locator* rules only after a reviewer points to the printed address block and later held-out invoices confirm the same supplier layout. Store the region/label relation, not a copied street address. Do not create a permanent rule from an unverified typed address or one example that might be an exception.
- Add a role-consistency check: the same address must not silently populate supplier and buyer, and the extracted block must not include reference numbers, contacts, GSTIN or a neighbouring party's name. Show the selected source block beside the editable address.

### 5. Close the scanned-invoice reliability gap (P1)

- Reproduce the [21 October live failure](https://github.com/Prime8115/medocr/actions/runs/39553440932) with recorded provider output. An AI response with zero items on a page that visibly has rows must be treated as incomplete and retried or flagged; it cannot be considered an accurate empty invoice.
- Carry the multi-page and pack/quantity regressions from the local photo/handwriting work onto current main, resolving overlap with #20–#24. Run the private corpus without changing its expected values merely to make tests pass.

## Release gates

1. Real reported cases pass with privately reviewed expected values; at least one same-supplier **new** invoice shows a justified learning outcome, or the UI clearly states why learning abstained.
2. No address from the wrong party is silently approved in the labelled cases. Uncertain addresses stay blank or require explicit review.
3. Duplicate, fresh-scan, split-PDF, ownership and migration integration tests pass. The private corpus, backend, web and mobile checks pass without weakening prior pins.
4. Run the live AI check on recorded scanned cases, build the APK from the tested commit, and compare the APK with the deployed backend before rollout.

## Delivery sequence

First reconcile the existing local accuracy branch with current main and preserve its unfinished work. Implement items 1–2 as one reviewable PR, then learning visibility, then address evidence and provider reliability. Keep deployments and APK distribution tied to passing checks and a reviewed release candidate.

## Tester audit and implemented response

Repeated uploads of the same invoice reproduced saved address and line edits.
The existing learner deliberately did not promote typed addresses into supplier
rules because it lacked a safe printed locator. The private audit informed the
regression tests; customer-specific activity remains outside this repository.

The implementation now reopens a saved review on ordinary identical upload. If
older copies of the same file hold complementary edits, that upload starts one
new reviewable reading and replays the latest human correction per field. An
explicit fresh scan also replays those edits. Replay requires the same shop,
file hash, PDF part, supplier and invoice identity; line edits additionally
require row alignment. Address edits containing neighbouring delivery or
reference labels are ignored in favour of an earlier clean correction. It
never turns a typed address into a rule for another
invoice. Each replayed field is shown to the reviewer and all verification
checks run again. A later upload reopens the combined result without a new OCR
call.

The audited records had complete page accounting and reconciled totals, while
some corrected fields still repeated on reupload. Structural checks do not
prove every address, product or price is right. A private held-out corpus is
used for release regression; future handwriting accuracy needs separately
labelled photos and prescriptions.

The unchanged GitHub main has local private-corpus failures on this Windows
environment. The branch initially added one stale recorded-answer mismatch
caused by changing a scanned multi-page bill from picture chunks to one call
per page. The replay-only test harness now uses its historical question
grouping for that existing output pin; it
does not claim the old answers validate the new per-page model path. The
page-level code is covered by new synthetic tests, and the private AI answer
should be re-recorded when the provider can be run on that source.
