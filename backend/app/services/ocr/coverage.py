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
