# Invoice accuracy safeguards

Implemented locally on 14 October 2026. These changes reduce silent errors;
they do not establish error-free extraction for every supplier or scan.

## Behavior in this change

- Arithmetic agreement no longer produces a blanket `verified` verdict.
  Results distinguish `checks_passed`, `incomplete`, `needs_check`, and
  `reviewed`. Both review interfaces show skipped checks.
- New extractions and edited invoices require review when supplier identity,
  invoice number/date/total, line items, or line description/quantity/batch/
  expiry/amount are missing. Zero is a value, not an absent field.
  Missing fields are separate from arithmetic checks in
  `meta.verification.review_requirements`.
- Failed page extraction remains an approval requirement after edits.
  Reviewers can enter the missing data or explicitly acknowledge the issue
  against the original. Acknowledgements record the reviewer and time;
  editing reopens review. An acknowledged exception is not proof of accuracy.
- Learned column mappings are scoped to shop, supplier GSTIN, and ordered
  normalized table headings. Changed column order is a different layout.
  Mixed layouts retain all signatures and cannot teach a single mapping.
  Multiple known supplier GSTINs cause learning selection to abstain.
- A mapping needs matching printed evidence on at least two changed rows
  (one for a one-row invoice), with a majority of supporting corrections.
  Reordered/replaced rows, conflicting meanings, and unknown source layouts
  do not teach a mapping. Existing outcome-based lesson retirement remains.
- Approved AI gap fills cannot become header lessons when any failed check
  or required-field gap remains, even if acknowledged.
- Recheck compares numbers exactly, preserving decimal position and signs,
  and preserves reference punctuation and token boundaries. Finding a value
  somewhere on a page still does not prove the correct row or field.
- A long invoice is rechecked only when its line can be uniquely located
  using independent identifying values. Ambiguous lines and long-document
  header rechecks ask for manual review instead of choosing the first page.
- Recheck results are invalidated when the document or edited fields change.
  Answers contradicted by the digital page text have no one-click acceptance.
  AI-supplied surrounding text is labelled as AI-reported context.
- AI review no longer counts empty, duplicate, contradictory, unanswered,
  or out-of-limit responses as a complete pass. Documents exceeding its page
  limit remain unreviewed. Editing invalidates an earlier independent pass;
  editing disputed values does not independently verify their replacements.
- PDF routing, parsing and licence checks reuse parsed pages within one
  extraction. Objects close when that extraction finishes, including errors;
  worker threads do not share parsed PDF objects.

## Release requirements

Apply Alembic revision `c7d9e2f4a6b8` before running the updated backend.
It adds `supplier_column_lessons.layout_signature` and changes the unique
constraint. Legacy lessons are preserved but disabled because their source
layout is unknown. Re-teach them from newly extracted documents carrying
layout metadata. A rollback refuses to collapse distinct layouts into one
legacy key instead of deleting history.

Release backend and web/mobile review changes together. Old clients do not
display the new required-field approval checks. Existing persisted invoices
are not bulk reprocessed by this patch: re-read or edit pending old invoices
to obtain the new completeness checks. Previously approved/pushed invoices
are not silently changed. No deployment or production migration was run as
part of this local work.

## Reference corpus and measurement

The private corpus has 134 invoice entries across 130 files, routed as 113
parser and 21 AI entries. Existing pinned reference values were not repinned.
Passing those regressions demonstrates preservation of the recorded values,
not correctness of unlabelled fields or future invoices.

The new reporting tool audits annotation coverage without calling AI or
changing the manifest. Run from `backend`, using the private corpus path:

```powershell
.\.venv\Scripts\python.exe scripts/audit_corpus.py G:\MediScan_OCR_corpus --details --output G:\MediScan_OCR_corpus\accuracy-audit.json
```

Add `--require-complete` when checking whether annotation work is complete.
An explicit null is a labelled absence; an omitted field is unlabelled.
Only recorded human validation counts as human validation. Agreement between
two automated readers is kept as its own provenance.

The current report contains 134 annotation work items, including unresolved
comparisons and incomplete row labels. This is not a count of faulty
invoices. Keep the detailed work queue private alongside the invoices.

## Next work needed to establish measured reliability

1. Have a reviewer confirm every critical field on every corpus row,
   recording absent/not-applicable values explicitly and resolving `todo`
   entries against the original. Add actual failure examples as regressions.
2. Record page, row and bounding-box evidence per extracted field. Use that
   evidence for cropped rechecks and exact row alignment across long scans.
3. Evaluate by supplier/layout and critical field, keeping new supplier
   formats separate from examples used to create parsing rules. Track errors
   that escaped review separately from documents correctly sent for review.
4. Add approval-only promotion and held-out evaluation for candidate lessons.
   Column corrections currently learn on explicit save; this patch scopes
   and constrains that behavior but does not introduce a training pipeline.
   Header-label learning remains supplier-scoped.
5. Validate with live AI and a staging PostgreSQL database before release.
   Local regression used synthetic/recorded AI responses and disabled live
   credentials. SQLite migration checks do not replace a staging migration.

The safe operating target is measurable accuracy plus visible uncertainty
and human review where evidence is insufficient, not an unsupported promise
that every possible invoice will be read perfectly.
