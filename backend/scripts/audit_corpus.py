"""Report annotation coverage without changing pins or calling any AI.

python scripts/audit_corpus.py G:/MediScan_OCR_corpus
Use --details --output <private-path> for the invoice-level work queue.
--require-complete exits nonzero until the declared annotation scope is met.
This measures reference-data coverage, NOT extraction accuracy.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

HEADERS = ("supplier.name", "supplier.gstin", "invoice.invoice_no", "invoice.invoice_date",
           "invoice.total_amount")
LINE_FIELDS = ("description", "product_code", "batch_no", "expiry", "pack", "uom", "quantity",
               "free_quantity", "mrp", "ptr", "pts", "rate", "amount", "gst_percent")


def audit(manifest):
    entries = manifest.get("invoices") or []
    queue = []
    provenance = Counter()
    header_pins = line_pins = 0
    for entry in entries:
        source = entry.get("verified") or "unspecified"
        provenance[source] += 1
        paths = [("parser", entry)]
        if entry.get("ai_path"):
            paths.append(("ai", entry["ai_path"]))
        for route, expected in paths:
            if route == "parser" and entry.get("reader") == "ai":
                continue  # the parser is expected to decline this invoice
            headers = expected.get("expect") or {}
            rows = expected.get("line_expect") or []
            count = (expected.get("lines") or {}).get("count")
            missing_headers = [p for p in HEADERS if p not in headers]
            missing_rows = [{"line": i + 1, "fields": [f for f in LINE_FIELDS if f not in row]}
                            for i, row in enumerate(rows)]
            missing_rows = [r for r in missing_rows if r["fields"]]
            unresolved = len(expected.get("todo") or [])
            header_pins += len(headers)
            line_pins += sum(len(row) for row in rows)
            # Explicit null is a labelled absence; an omitted key is unlabelled.
            gaps = []
            if source != "checked by hand":
                gaps.append("human_validation_not_recorded")
            if unresolved:
                gaps.append("unresolved_comparisons")
            if missing_headers:
                gaps.append("unlabelled_headers")
            if count is None:
                gaps.append("line_count_not_labelled")
            elif len(rows) != count:
                gaps.append("line_rows_not_fully_labelled")
            if missing_rows:
                gaps.append("unlabelled_line_fields")
            if gaps:
                queue.append({"file": entry.get("file"), "part": entry.get("part"), "route": route,
                              "gaps": gaps, "unresolved": unresolved, "missing_headers": missing_headers,
                              "expected_line_count": count, "labelled_rows": len(rows),
                              "missing_line_fields": missing_rows})
        if entry.get("reader") == "ai" and not entry.get("ai_path"):
            queue.append({"file": entry.get("file"), "part": entry.get("part"), "route": "ai",
                          "gaps": ["ai_path_not_recorded"]})
    return {
        "measurement": "reference_annotation_coverage_not_accuracy",
        "invoices": len(entries), "unique_files": len({e.get("file") for e in entries}),
        "reader_counts": dict(Counter(e.get("reader", "parser") for e in entries)),
        "validation_provenance": dict(provenance),
        "pinned_header_fields": header_pins, "pinned_line_fields": line_pins,
        "entries_with_todo": sum(bool(e.get("todo")) for e in entries),
        "ai_paths_with_todo": sum(bool((e.get("ai_path") or {}).get("todo")) for e in entries),
        "complete": bool(entries) and not queue,
        "work_items": len(queue), "gap_counts": dict(Counter(g for q in queue for g in q["gaps"])),
        "required_header_fields": HEADERS, "required_line_fields": LINE_FIELDS,
        "queue": queue,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = audit(json.loads((args.corpus / "corpus.json").read_text(encoding="utf-8")))
    if not args.details:
        report.pop("queue")
    content = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.output:
        # A reporting command must never replace the reference manifest.
        if args.output.resolve() == (args.corpus / "corpus.json").resolve():
            parser.error("The report cannot overwrite corpus.json")
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 1 if args.require_complete and not report["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
