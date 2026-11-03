"""Score a private, human-labelled invoice holdout without making AI calls.

    python scripts/score_holdout.py labels.json predictions.json
    python scripts/score_holdout.py labels.json predictions.json --require-eligible

Inputs use the synthetic shape in docs/examples/holdout-{labels,predictions}.json.
Only complete cases explicitly marked ``split: holdout`` and
``verified: checked by hand`` contribute to accuracy metrics. Omitted keys are
unlabelled; explicit null is a labelled absence. Missing predictions for an
eligible case are an error, not an opportunity to improve the score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re


HEADERS = ("supplier.name", "supplier.gstin", "supplier.address", "bill_to.address",
           "ship_to.address", "invoice.invoice_no", "invoice.invoice_date",
           "invoice.total_amount")
LINE_FIELDS = ("description", "batch_no", "expiry", "pack", "quantity",
               "free_quantity", "rate", "amount", "gst_percent")
NUMERIC = {"quantity", "free_quantity", "rate", "amount", "gst_percent", "total_amount"}
DATES = {"invoice_date", "expiry"}
ROW_ID = ("description", "batch_no", "expiry")


def _leaf(node, path):
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node.get("value") if isinstance(node, dict) and "value" in node else node


def _norm(field, value):
    if value is None or str(value).strip() == "":
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    name = field.rsplit(".", 1)[-1]
    if name in NUMERIC:
        try:
            return Decimal(text.replace(",", ""))
        except InvalidOperation:
            pass
    if name in DATES:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m-%Y", "%m/%Y", "%m-%y"):
            try:
                parsed = datetime.strptime(text, fmt)
                return f"{parsed.year:04d}-{parsed.month:02d}" + (f"-{parsed.day:02d}" if "%d" in fmt else "")
            except ValueError:
                continue
    return text.casefold()


def _same(field, got, want):
    return _norm(field, got) == _norm(field, want)


def _eligible(entry):
    gaps = []
    if entry.get("split") != "holdout":
        gaps.append("not_holdout")
    if entry.get("verified") != "checked by hand":
        gaps.append("not_human_checked")
    if entry.get("todo"):
        gaps.append("unresolved_comparisons")
    if not entry.get("cohort"):
        gaps.append("missing_cohort")
    if type(entry.get("source_pages")) is not int or entry["source_pages"] < 1:
        gaps.append("missing_source_page_count")
    else:
        content_pages = entry.get("content_pages")
        if (not isinstance(content_pages, list) or not content_pages
                or any(type(page) is not int for page in content_pages)
                or content_pages != sorted(set(content_pages))
                or any(page < 1 or page > entry["source_pages"] for page in content_pages)):
            gaps.append("missing_content_page_labels")
    expected = entry.get("expect") or {}
    if not isinstance(expected, dict) or any(key not in expected for key in HEADERS):
        gaps.append("incomplete_headers")
    rows = entry.get("line_expect") or []
    count = (entry.get("lines") or {}).get("count")
    if type(count) is not int or count < 1 or len(rows) != count:
        gaps.append("incomplete_row_count")
    if any(not isinstance(row, dict) or any(key not in row for key in LINE_FIELDS) for row in rows):
        gaps.append("incomplete_line_fields")
    return gaps


def _identity(row):
    return tuple(_norm(key, _leaf(row, key)) for key in ROW_ID)


def _pages(prediction):
    manifest = prediction.get("source_manifest") or {}
    accounted = [*(manifest.get("page_map") or []), *(manifest.get("removed_pages") or [])]
    return accounted


def _score_cases(cases):
    result = {"documents": len(cases), "source_pages": 0, "content_pages": 0,
              "pages_accounted": 0,
              "complete_page_sets": 0, "expected_rows": 0, "matched_rows": 0,
              "unexpected_rows": 0, "exact_row_count_documents": 0,
              "header_fields": 0, "correct_header_fields": 0,
              "line_fields": 0, "correct_line_fields": 0,
              "accepted_with_critical_error": 0}
    header_detail = Counter({key: 0 for key in HEADERS})
    line_detail = Counter({key: 0 for key in LINE_FIELDS})
    for label, prediction in cases:
        expected_pages = set(range(1, label["source_pages"] + 1))
        content_pages = set(label["content_pages"])
        blank_pages = expected_pages - content_pages
        manifest = prediction.get("source_manifest") or {}
        page_map = manifest.get("page_map") or []
        accounted = _pages(prediction)
        result["source_pages"] += len(expected_pages)
        result["content_pages"] += len(content_pages)
        result["pages_accounted"] += len(content_pages & set(page_map))
        complete_pages = (page_map == sorted(content_pages)
                          and (manifest.get("removed_pages") or []) == sorted(blank_pages)
                          and set(accounted) == expected_pages
                          and len(accounted) == len(expected_pages))
        if complete_pages:
            result["complete_page_sets"] += 1
        fields = ((prediction.get("payload") or {}).get("fields") or {})
        case_wrong = not complete_pages
        for key in HEADERS:
            correct = _same(key, _leaf(fields, key), label["expect"][key])
            result["header_fields"] += 1
            result["correct_header_fields"] += int(correct)
            header_detail[key] += int(correct)
            case_wrong |= not correct
        actual_rows = list(fields.get("line_items") or [])
        available: dict[tuple, list] = {}
        for row in actual_rows:
            available.setdefault(_identity(row), []).append(row)
        expected_rows = label["line_expect"]
        result["expected_rows"] += len(expected_rows)
        result["exact_row_count_documents"] += int(len(actual_rows) == len(expected_rows))
        for expected in expected_rows:
            matches = available.get(_identity(expected)) or []
            actual = matches.pop(0) if matches else None
            result["matched_rows"] += int(actual is not None)
            case_wrong |= actual is None
            for key in LINE_FIELDS:
                correct = actual is not None and _same(key, _leaf(actual, key), expected[key])
                result["line_fields"] += 1
                result["correct_line_fields"] += int(correct)
                line_detail[key] += int(correct)
                case_wrong |= not correct
        result["unexpected_rows"] += sum(len(rows) for rows in available.values())
        if len(actual_rows) != len(expected_rows):
            case_wrong = True
        if prediction.get("status") in ("approved", "pushed") and case_wrong:
            result["accepted_with_critical_error"] += 1
    result["header_correct_by_field"] = dict(header_detail)
    result["line_correct_by_field"] = dict(line_detail)
    for key, numerator, denominator in (("page_recall", "pages_accounted", "content_pages"),
                                        ("row_recall", "matched_rows", "expected_rows"),
                                        ("header_field_accuracy", "correct_header_fields", "header_fields"),
                                        ("line_field_accuracy", "correct_line_fields", "line_fields")):
        result[key] = round(result[numerator] / result[denominator], 4) if result[denominator] else None
    return result


def score(labels, predictions):
    entries = labels.get("cases") or []
    supplied = predictions.get("cases") or []
    ids = [case.get("id") for case in entries]
    prediction_ids = [case.get("id") for case in supplied]
    if None in ids or len(set(ids)) != len(ids) or None in prediction_ids or len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("Every label and prediction needs a unique id")
    by_id = {case["id"]: case for case in supplied}
    excluded = Counter()
    eligible = []
    for entry in entries:
        gaps = _eligible(entry)
        if gaps:
            excluded.update(gaps)
            continue
        if entry["id"] not in by_id:
            raise ValueError(f"Missing prediction for eligible case {entry['id']}")
        eligible.append((entry, by_id[entry["id"]]))
    cohorts = sorted({label["cohort"] for label, _ in eligible})
    return {"measurement": "human_labelled_holdout_only",
            "scope": {"header_fields": HEADERS, "line_fields": LINE_FIELDS,
                      "row_identity": ROW_ID},
            "labelled_cases": len(entries), "eligible_cases": len(eligible),
            "excluded_cases": len(entries) - len(eligible), "exclusion_reasons": dict(excluded),
            "unlabelled_predictions": len(set(prediction_ids) - set(ids)),
            "overall": _score_cases(eligible) if eligible else None,
            "by_cohort": {cohort: _score_cases([(a, b) for a, b in eligible if a["cohort"] == cohort])
                          for cohort in cohorts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--require-eligible", action="store_true")
    parser.add_argument("--require-complete", action="store_true",
                        help="fail if any label is excluded or any prediction has no label")
    parser.add_argument("--min-cases", type=int, default=0,
                        help="minimum number of eligible cases for a release gate")
    args = parser.parse_args()
    if args.min_cases < 0:
        parser.error("--min-cases cannot be negative")
    try:
        report = score(json.loads(args.labels.read_text(encoding="utf-8")),
                       json.loads(args.predictions.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, default=list))
    insufficient = (args.require_eligible and not report["eligible_cases"]
                    or report["eligible_cases"] < args.min_cases)
    incomplete = args.require_complete and (report["excluded_cases"] or report["unlabelled_predictions"])
    return 1 if insufficient or incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
