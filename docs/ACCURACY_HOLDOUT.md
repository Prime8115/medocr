# Measure invoice accuracy on a private holdout

MediScan's regression corpus prevents known supplier layouts from breaking. It is not a market accuracy benchmark: many values are only agreement between two readers, and most rows are not fully checked by a person. A saleable accuracy claim needs a separately frozen, human-labelled holdout that is not used to tune parsing rules, prompts or supplier lessons.

## Collect and label

1. Freeze a private invoice set before the next extraction change. Record consent and an ID for each document. Keep files, names and predictions outside both application repositories.
2. Stratify by printed text PDF, scanned PDF, phone photo, supplier/layout, unseen supplier, page count and invoice/credit note. Report each cohort separately. Handwritten prescriptions require a separate clinical transcription benchmark; this invoice scorer does not certify them.
3. For each invoice, a reviewer checks `source_pages` and labels each page containing invoice content in `content_pages` (one-based, sorted). All other pages must be genuinely blank. The reviewer also checks supplier name, GSTIN and address; bill-to and ship-to addresses; invoice number, date and total; and **every** product row's description, batch, expiry, pack, billed and free quantity, rate, amount and GST rate. Record an explicit JSON `null` when a value is absent on paper. An omitted key means no human label exists.
4. Resolve every comparison note. Mark `verified: "checked by hand"` only after the reviewer has checked all required fields and rows against the source. Mark `split: "holdout"` only after freezing the set. Keep an audit record of reviewer, date and document hash in the private dataset.
5. Run the current MediScan build on every frozen file without using its labels or saved corrections. Save each returned document detail as a prediction, including `payload.fields`, `source_manifest` and final `status`. Use the same input set for any competitor comparison.

[Synthetic labels](examples/holdout-labels.json) and [synthetic predictions](examples/holdout-predictions.json) show the file shape. They deliberately use `split: "example"` and `verified: "example only"`, so the tool reports zero eligible cases. Do not change those values and publish the resulting 100% as product accuracy.

## Score

From `backend/`:

```bash
python scripts/score_holdout.py /private/labels.json /private/predictions.json --require-eligible
# Once the frozen set is fully labelled, make exclusion and sample size a gate:
python scripts/score_holdout.py /private/labels.json /private/predictions.json --require-complete --min-cases 500
```

The inputs each contain `{"cases": [...]}` joined by unique `id`. Missing predictions for an eligible case are an error. Partial labels, unresolved notes, nonhuman checks and nonholdout cases appear in `exclusion_reasons` and contribute nothing to accuracy. `--require-complete` fails if any case is excluded or a prediction lacks a label; `--min-cases` fails below the specified eligible sample size. The report includes every denominator and the fixed field scope used by this version. The 500-case example is a proposed collection target from the PRD, not a measured result.

- **Page recall:** human-labelled content pages present in the manifest's `page_map`. `complete_page_sets` requires exactly those content pages retained and exactly the human-labelled blank pages removed; it rejects duplicate or out-of-order pages. The report separately counts all source pages and content pages.
- **Row recall:** a one-to-one match on normalized description, batch and expiry; duplicate lines cannot reuse a single prediction. `unexpected_rows` counts every unmatched prediction, including a wrong identity with the right row count. Exact row count is reported separately.
- **Field accuracy:** the eight required headers and nine fields on every labelled row. A missing row counts all its line fields wrong, rather than disappearing from the denominator. Dates and numeric formatting are normalized; printed values and explicit absence remain distinct.
- **Accepted critical errors:** an approved or pushed document with a missing/duplicate page, wrong critical field or wrong row count. This is a count on the labelled set, not proof that other invoices are safe.

The proposed pilot thresholds are in the [independent product PRD](INDEPENDENT_PRODUCT_PRD.md). Do not combine invoice and prescription accuracy, or a largely printed cohort with a small photo cohort, into one headline percentage. Publish the document mix, eligible/excluded counts, exact numerator and denominator, reviewer method, build revision and unresolved cases with every claim. A zero-error small sample is not an error-free guarantee.
