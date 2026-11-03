"""Extraction completeness is separate from arithmetic consistency.

A missing value is unknown, not proof that the paper does not print it.
These requirements can be resolved by an edit or explicitly acknowledged
against the paper, using the existing audited approval workflow.
"""


def _value(node, key):
    leaf = (node or {}).get(key) or {}
    return leaf.get("value") if isinstance(leaf, dict) else None


def _present(value):
    return value is not None and str(value).strip() != ""


def source_page_requirement(report):
    mismatch = report.get("source_page_mismatch")
    if not isinstance(mismatch, dict):
        return None
    expected, observed = mismatch.get("expected"), mismatch.get("observed")
    return {
        "id": "source_page_integrity", "status": "fail",
        "label": "Source and reading page counts differ",
        "message": f"The upload contains {expected} retained page(s), but the reading reports "
                   f"{observed if observed is not None else 'no'} page count. View every source page "
                   "and check its contents before approval.",
        "fields": [],
    }


def record_source_page_count(result, manifest):
    """Compare the stored upload manifest with the OCR result before review.

    OCR may return a plausible table after silently dropping a page. This check
    remains in metadata and is recreated after a reviewer edits the fields.
    """
    expected = (manifest or {}).get("processed_pages")
    if type(expected) is not int or expected < 1:
        return result
    meta = result.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        result["meta"] = meta
    observed = meta.get("pages")
    if type(observed) is int and observed == expected:
        return result
    meta["source_page_mismatch"] = {
        "expected": expected,
        "observed": observed if type(observed) is int else None,
    }
    check = source_page_requirement(meta)
    verification = meta.get("verification")
    if not isinstance(verification, dict):
        verification = {"checks": [], "review_requirements": [], "acknowledged": []}
        meta["verification"] = verification
    required = [r for r in verification.get("review_requirements") or []
                if r.get("id") != "source_page_integrity"]
    required.append(check)
    verification["review_requirements"] = required
    summarize(verification)
    warning = f"{check['label']}: {check['message']}"
    if warning not in (meta.get("warnings") or []):
        meta["warnings"] = [warning, *(meta.get("warnings") or [])]
    return result


def requirements(fields, report):
    from app.services.ocr.postprocess import is_pack_expression

    missing = []
    supplier = fields.get("supplier") or {}
    if not any(_present(_value(supplier, key)) for key in ("name", "gstin")):
        missing.append("supplier.name")
    for key in ("invoice_no", "invoice_date", "total_amount"):
        if not _present(_value(fields.get("invoice"), key)):
            missing.append(f"invoice.{key}")
    items = fields.get("line_items") or []
    if not items:
        missing.append("line_items")
    for n, item in enumerate(items):
        for key in ("description", "quantity", "batch_no", "expiry"):
            if not _present(_value(item, key)):
                missing.append(f"line_items[{n}].{key}")
        if not any(_present(_value(item, key)) for key in ("amount", "net_amount", "gross_amount")):
            missing.append(f"line_items[{n}].amount")
    checks = []
    page_check = source_page_requirement(report)
    if page_check:
        checks.append(page_check)
    ambiguous = [f"line_items[{n}].{key}" for n, item in enumerate(items)
                 for key in ("quantity", "free_quantity", "total_quantity")
                 if is_pack_expression(_value(item, key))]
    if ambiguous:
        checks.append({"id": "pack_quantity", "status": "fail",
                       "label": "Pack size appears in a quantity field",
                       "message": "Keep pack wording (such as 15's tablets) in Pack. Enter billed "
                                  "quantity and unit from their own columns; do not infer them from pack size.",
                       "fields": ambiguous})
    if missing:
        examples = "; ".join(p.replace("line_items[", "item[").replace("_", " ") for p in missing[:8])
        if len(missing) > 8:
            examples += f"; and {len(missing) - 8} more"
        checks.append({
            "id": "extraction_coverage", "status": "fail",
            "label": "Required invoice fields need review",
            "message": f"{len(missing)} required field(s) are blank. Fill them from the paper, "
                       "or confirm that they are not printed or do not apply. "
                       f"A matching total does not confirm these fields. Missing: {examples}.",
            "fields": missing,
        })
    if report.get("pages_failed"):
        checks.append({
            "id": "page_coverage", "status": "fail",
            "label": "Some invoice pages could not be read",
            "message": "Re-upload the missing pages or enter and check their contents before approval.",
            "fields": [],
        })
    return checks


def summarize(verification):
    checks = verification.get("checks") or []
    required = verification.get("review_requirements") or []
    acknowledged = {a.get("id") for a in verification.get("acknowledged") or []}
    failures = [c for c in checks + required if c.get("status") == "fail"]
    open_failures = [c for c in failures if c.get("id") not in acknowledged]
    verification["passed"] = sum(c.get("status") == "pass" for c in checks)
    verification["failed"] = sum(c.get("status") == "fail" for c in checks)
    verification["skipped"] = sum(c.get("status") == "skipped" for c in checks)
    # No verdict here claims every extracted value was independently verified.
    if open_failures:
        verdict = "needs_check"
    elif failures:
        verdict = "reviewed"
    elif not checks or verification["skipped"] or not verification["passed"]:
        verdict = "incomplete"
    else:
        verdict = "checks_passed"
    verification["verdict"] = verdict
    return verification
