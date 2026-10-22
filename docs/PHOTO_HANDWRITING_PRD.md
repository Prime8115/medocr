# MediScan: photo, scanned PDF and handwriting accuracy

Version 1.0 · 15 October 2026 · Engineering baseline: `5b7a097`

This PRD extends the existing product. It supersedes the accuracy assumptions
in the older production-rebuild PRD, whose prototype description is now stale.
Implementation is authorized; production rollout is a separate release step.

## 1. Problem and outcome

Pharmacy staff need every page and medicine row captured faithfully from
phone photos, scanned invoices, handwritten invoices and prescriptions.
Reported failures include a two-page scanned PDF appearing as one page and
pack wording such as `15's tablets` appearing as billed quantity.

The outcome is accurate transcription with visible evidence and uncertainty.
An unreadable value must remain unresolved; it must not become a plausible
guess. Matching totals or agreement between two AI readers is not proof that
all product, batch, expiry or medication instructions are correct.

## 2. Audit of the current product

| Area | Already implemented | Remaining gap |
|---|---|---|
| Upload | Type sniffing, EXIF rotation, HEIC conversion, size/blank checks, PDF repair | Original transformed bytes are not retained separately; mobile always resizes/recompresses photos |
| PDF intake | Page counting, blank removal, conservative invoice grouping | Blank detection uses an 18-DPI image and can discard faint/sparse scanned pages |
| PDF extraction | Chunking, retries, ordered merges, failed-page warning | Several scanned pages can share one AI answer; a valid first-page-only answer is not a page failure; last chunk uses configured rather than actual page count |
| Invoice verification | Arithmetic, required-field review, audited exceptions, layout-scoped column learning | Pack/UOM wording can be interpreted as quantity; missing values differ from confirmed absence |
| Quantity normalization | Numeric companion alongside literal value | Searches for the first number even inside pack wording and can retain stale normalized values |
| Prescriptions | Patient, prescriber and medication fields, manual edit | Invoice-only recheck and independent checks; no dedicated prescription approval requirements |
| Evidence | Stored document, AI-reported surrounding text | No authenticated page preview/crop tied to explicit page coordinates |
| Capture | Camera/gallery/PDF, offline queue, preview | No quality guidance or lossless original capture path |
| Evaluation | Private invoice corpus, synthetic scans, live Gemini smoke tests | Incomplete human labels; no representative held-out handwritten prescription benchmark |

Audit sources: `services/intake.py`, `services/ocr/__init__.py`,
`services/ocr/postprocess.py`, `services/recheck.py`, `api/documents.py`,
`schemas/extraction.py`, and mobile capture/review code. The user-reported
documents must be added privately when their filenames/examples are identified;
synthetic regressions alone do not prove those particular documents fixed.

## 3. Users and journeys

Pharmacy staff capture/upload, inspect page count, correct fields and save.
Pharmacists confirm unresolved prescription information against the source.
Owners manage access and downstream exports. Support uses traceable failure
reports and anonymized regression cases without exposing patient documents.

1. Capture or choose an original photo/PDF; select document type or Auto.
2. See quality advice and page accounting. Retake poor photos when possible.
3. Processing accounts for each retained page, with progress and explicit failures.
4. Review literal fields beside the source page; choose a page/region for recheck.
5. Correct the reading. Recheck suggestions never save themselves.
6. Resolve or explicitly acknowledge remaining requirements before approval.
7. Export only approved records. Later edits reopen review and clear stale checks.

## 4. Scope and priorities

### P0: prevent lost pages and pack/quantity corruption — first build

**PDF-01:** Preserve scanned pages containing image content even when faint,
sparse or visually near blank. Remove only demonstrably empty pages; uncertainty
favours retention. Keep the original upload and report source/retained counts.

**PDF-02:** Read image-based PDFs one page per extraction request, with bounded
concurrency and ordered merging. Strip unreliable scanner text without losing
pages. Count actual pages, not chunk capacity. A page-level request is evidence
of an attempt, not proof every row on it was read.

**PDF-03:** Expose processed and failed counts; partial results remain visibly
incomplete and require review. Retrying cannot convert a failed page to success
without another successful attempt. Keep original order.

**QTY-01:** Separate `pack`, `uom`, `quantity`, `free_quantity` and description.
`15's tablets` is pack evidence, not independently a quantity of 15.
`2 strips` has numeric quantity 2 and printed unit strips. `1 x 15` is a pack
expression, not independently quantity 1. `15 tablets` under a quantity heading
can be a valid billed quantity and must not be blindly moved to pack.

**QTY-02:** Preserve ambiguous original text, clear misleading numeric companions,
flag its row for review and never infer billed quantity from pack size alone.
Do not multiply strips into tablets or change units for downstream inventory
without explicit unit-conversion evidence and a reviewed mapping.

**QTY-03:** Show Pack and Unit separately in both review clients. Field prompts
must explain the distinction. Regression tests cover apostrophe variants,
multiplier packs, genuine unit quantities, commas, decimals and zero.

### P1: photo and prescription review — initial implementation

**IMG-01:** Stop unconditional phone-side resizing/recompression. Retain original
uploaded bytes alongside any processing derivative, including rotation/conversion.
Respect upload size limits; an oversized photo receives an actionable message.
Offline capture must retain the same original file selected for upload.

**IMG-02:** Provide measured, advisory photo-quality feedback for low resolution,
low contrast/darkness and probable blur. Do not claim heuristics reliably detect
glare, clipped text or illegible handwriting. No enhancement may synthesize strokes.
Keep a retake option and preserve the original even when advice is ignored.

**RX-01:** Extend field recheck to patient, prescriber and medication fields.
Support name, strength, form, frequency, duration and instructions; preserve
literal wording. Add separate dose/route fields only with a versioned schema
and export compatibility plan; do not mix them into strength by inference.

**RX-02:** Prescription review must surface missing essential information,
partial page extraction and uncertain medicine instructions. Require explicit
pharmacist confirmation before approval/export; no clinical interpretation,
substitution, inferred dosing, or automatic dispensing is in scope.

**EVID-01:** Offer authenticated source-page previews and bounded crops using
explicit page number and normalized coordinates. A user-selected region is
labelled as selected; it is not presented as independently verified evidence.
Reject out-of-range pages/rectangles and enforce shop ownership on every request.

**EVID-02:** Recheck may use the selected page/region. Show which source was used,
retain surrounding context, and invalidate results after edits or region changes.
Do not accept AI-invented coordinates as verified OCR locations.

### P2: measured handwriting improvement — following increments

Benchmark Gemini against an independent handwriting OCR provider, then route
only difficult documents to a second reader if the benchmark justifies it.
Keep literal transcription separate from catalogue suggestions. A catalogue
match must never silently replace a similar-looking medicine name or strength.
Implement automatic line/row localization with confidence and original-coordinate
mapping before enabling automatic crops. Calibrate confidence on held-out data.

Candidate lessons must be reviewed, evaluated against held-out examples, scoped
by shop/document type/layout, versioned and reversible before automatic promotion.
Do not train from unapproved edits, model agreement alone or acknowledged errors.

## 5. Data and API requirements

- Preserve original storage reference privately; expose source previews through
  authenticated owned-document routes, never public object URLs or arbitrary paths.
- Keep source/retained/attempted/failed page counts distinct. For split documents,
  distinguish uploaded-file pages from pages belonging to the selected invoice.
- Add optional source metadata compatibly so old documents remain readable.
- Recheck request: existing field path plus optional page and crop. Existing
  invoice callers continue to work. Response identifies selected source context.
- All edits, approval exceptions and rechecks remain audited. No raw images,
  patient names, AI keys or prescription text go to public logs/tests.
- Original and derivative files follow the same access, retention and deletion
  policy. Retaining originals must not imply indefinite retention or model training.

## 6. Acceptance scenarios

| Case | Required result |
|---|---|
| Two scan pages; second page faint or only one item | Both retained and attempted; source count shown |
| Scanner text layer is wrong | Visible pages are read; hidden text is not treated as independent truth |
| One page fails | Other results retained; failed count and approval requirement remain |
| Two pages repeat the same product but different batch | Both rows retained; no guessed duplicate removal |
| Truly empty separator page | Removal is explicit and original remains available |
| Quantity contains `15’s tablets` | No numeric quantity 15 silently exported; original wording and review flag retained |
| Pack `15's tablets`, billed `2 strips` | Pack preserved; quantity 2, no automatic conversion to 30 |
| Existing quantity 0 | Zero remains valid, not missing |
| Handwritten prescription medicine ambiguous | Source visible; no catalogue substitution; requires confirmation |
| Recheck responds after user edits | Old answer cannot overwrite newer fields |
| Other shop requests source/crop | Access denied |
| PDF crop page out of range or oversized rendering | Clear validation error, bounded resource use |

## 7. Evaluation and release gates

Maintain private, separately labelled sets for printed phone photos, scanned
PDFs, handwritten invoices and handwritten prescriptions. Include rotations,
shadows, low contrast, folds, crossed-out text, marginal notes, mixed scripts,
duplicate copies and continuation pages. Keep supplier/author/layout holdouts;
do not tune rules on their final evaluation examples.

Human reviewers label every critical field, page and row, with explicit absent,
unreadable and ambiguous states. Report exact critical-field accuracy, row/page
recall, manual-review rate, erroneous approvals, latency and provider cost.
Do not publish a single accuracy percentage from partially labelled invoices.

First-build gates: all deterministic regression tests pass; all pages in the
synthetic loss cases survive; ambiguous pack values have no numeric quantity;
existing private-corpus pins remain unchanged; ownership and crop bounds tested;
web/mobile tests, type checks and APK build succeed. Real reported cases remain
an explicit verification item until obtained. Handwriting accuracy targets are
set after a human-labelled baseline, not invented before measurement.

## 8. Delivery and risks

Deliver in order: audit/PRD; P0 reproductions and fixes; P1 source preservation
and prescription review; regression/corpus verification; GitHub APK build;
device acceptance and approved backend rollout. Update this document's delivery
record to distinguish completed work from deferred requirements.

Page-by-page reading increases AI calls. Retained originals increase storage.
Heuristic quality advice can produce false alarms. Poor source material may
remain unreadable. Backend and APK must be released compatibly; older clients
may not expose new controls. Use feature-compatible optional fields and retain
manual correction while the newer client is adopted.

## 9. Delivery record

- Audit and requirements: complete. A private production audit covered the
  selected tester's uploads and checked page accounting, corrections and
  repeated scans. Customer-specific activity is retained outside this
  repository. Page accounting does not establish every field's correctness.
- Implemented in this release candidate: conservative blank-page retention,
  per-page AI requests for scanned PDFs, empty-item retry/stop, literal pack
  handling, prescription review requirements, authenticated processed-page
  preview and selected-page recheck, original mobile photo bytes, saved-review
  duplicate selection, exact-file correction replay and supplier-rule feedback.
- The address regression now stops at the delivery boundary. Typed address
  edits are replayed only for identical file bytes and matching invoice parts.
  They do not train a general address rule without a unique printed locator.
  Reusing a street address on another invoice would be unsafe.
- Still required before any claim of handwriting or photo accuracy: a labelled
  held-out benchmark and measured critical-field/page/row recall, original-file
  preservation alongside transformed bytes, reliable region coordinates/crops,
  capture quality guidance, a validated address locator, provider comparison
  and versioned rule promotion. The source endpoint renders the stored processed
  page; it does not yet serve a retained original or arbitrary crop.
- Backend and APK checks/build are recorded in the release PR and Actions run.
- A private historical multi-page AI recording asked picture-chunk questions.
  The new scanner asks one question per page. Its
  old confirmed-output pin is replayed with the historical question grouping
  in the test harness; it must be re-recorded with a live provider before it
  can measure the new page-by-page behavior on that invoice. Synthetic
  page-level tests cover the new path meanwhile.
