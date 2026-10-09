"""Tools for the private invoice corpus (see tests/test_corpus.py).

    python scripts/corpus.py snapshot <corpus> <out.json>
        Read every invoice in the corpus (AI off) and save the key values.

    python scripts/corpus.py diff <before.json> <after.json>
        Every value that changed between two snapshots - the blast radius of a
        change, across every supplier, on one screen. Run a snapshot before and
        after any change to the reader.

    python scripts/corpus.py add <corpus> <pdf path inside corpus>
        Bring in a new supplier's invoice: our reading next to an independent
        AI reading (needs GEMINI_API_KEY). Values they agree on are pinned;
        disagreements go to "todo" for a person to settle against the paper.

Run from the backend directory.
"""
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

HEADER = [
    ("supplier.name", "name"), ("supplier.gstin", "exact"), ("bill_to.name", "name"),
    ("bill_to.gstin", "exact"), ("invoice.invoice_no", "ref"), ("invoice.invoice_date", "date"),
    ("invoice.total_amount", "money"), ("invoice.total_taxable_amount", "money"),
    ("invoice.total_gst_amount", "money"), ("invoice.po_no", "ref"), ("invoice.lr_no", "ref"),
    ("invoice.irn", "exact"), ("invoice.due_date", "date"),
]
LINE = [("batch_no", "ref"), ("quantity", "money"), ("amount", "money"), ("expiry", "exp")]


def val(fields, path):
    node = fields
    for part in path.split("."):
        node = (node or {}).get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node = node.get("value")
    return None if node in ("", None) else str(node).strip()


def num(s):
    m = re.search(r"-?[\d,]*\.?\d+", str(s or ""))
    try:
        return float(m.group().replace(",", "")) if m else None
    except ValueError:
        return None


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def day(s):
    s = (s or "").strip().lower()
    m = re.search(r"(\d{1,2})[\s/\-.]+([a-z]{3})[a-z]*[\s/\-.']+(\d{2,4})", s)
    if m:
        y = int(m.group(3))
        return (y + 2000 if y < 100 else y, _MONTHS.get(m.group(2)[:3]), int(m.group(1)))
    m = re.search(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (a, b, c) if a > 31 else (c + 2000 if c < 100 else c, b, a)
    return s


def same(kind, a, b):
    if kind == "money":
        x, y = num(a), num(b)
        return x == y if x is None or y is None else abs(x - y) <= max(1.0, 0.002 * abs(x))
    if kind == "date":
        return day(a) == day(b)
    if kind == "name":
        a2 = re.sub(r"(private|pvt|limited|ltd|the|ms)", "", key(a))
        b2 = re.sub(r"(private|pvt|limited|ltd|the|ms)", "", key(b))
        # Same start AND about the same length: a run-on name never agrees.
        return bool(a2) and bool(b2) and (a2.startswith(b2[:10]) or b2.startswith(a2[:10])) \
            and max(len(a2), len(b2)) <= 1.5 * min(len(a2), len(b2)) + 4
    if kind == "ref":
        return key(a) == key(b)  # exact: a cut-off number must never count as agreeing
    if kind == "exp":
        return key(a)[-2:] == key(b)[-2:] if a and b else a == b
    return key(a) == key(b)


def read_free(data: bytes, own_gstins=()) -> dict:
    """The full pipeline with the AI switched off."""
    from app.config import settings
    from app.services.ocr import process_document

    # The AI is never called here: a bill the free reader declines goes to the
    # stand-in reader, so "declined" is visible without a key or a network.
    saved = (settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr)
    settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = None, None, True
    try:
        return process_document("corpus", data, "application/pdf", doc_type="invoice", own_gstins=own_gstins)
    finally:
        settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = saved


def summary(result: dict) -> dict:
    meta, fields = result.get("meta") or {}, result.get("fields") or {}
    lines = fields.get("line_items") or []
    ver = meta.get("verification") or {}
    return {
        "reader": meta.get("pipeline"),
        "values": {p: val(fields, p) for p, _ in HEADER},
        "lines": len(lines),
        "sum_amount": round(sum(num(val(i, "amount")) or 0 for i in lines), 2),
        "first_lines": [{k: val(i, k) for k, _ in LINE} for i in lines[:3]],
        "reconciles": meta.get("total_reconciles"),
        "failed": sorted(c["id"] for c in ver.get("checks") or [] if c["status"] == "fail"),
    }


def snapshot(corpus: pathlib.Path, out: pathlib.Path) -> None:
    import logging
    logging.disable(logging.WARNING)
    manifest = json.loads((corpus / "corpus.json").read_text(encoding="utf-8"))
    entries, own = manifest["invoices"], tuple(manifest.get("shop_gstins") or ())
    snap = {}
    for n, e in enumerate(entries, 1):
        snap[e["file"]] = summary(read_free((corpus / e["file"]).read_bytes(), own))
        print(f"\r{n}/{len(entries)}", end="", flush=True)
    out.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {len(snap)} readings to {out}")


def diff(before: pathlib.Path, after: pathlib.Path) -> int:
    a = json.loads(before.read_text(encoding="utf-8"))
    b = json.loads(after.read_text(encoding="utf-8"))
    changed = 0
    for name in sorted(set(a) | set(b)):
        x, y = a.get(name) or {}, b.get(name) or {}
        out = []
        for field in ("reader", "lines", "sum_amount", "reconciles"):
            if x.get(field) != y.get(field):
                out.append(f"{field}: {x.get(field)!r} -> {y.get(field)!r}")
        for path in sorted(set(x.get("values") or {}) | set(y.get("values") or {})):
            p, q = (x.get("values") or {}).get(path), (y.get("values") or {}).get(path)
            if p != q:
                out.append(f"{path}: {p!r} -> {q!r}")
        if x.get("first_lines") != y.get("first_lines"):
            out.append(f"first lines: {x.get('first_lines')} -> {y.get('first_lines')}")
        gone, new = set(x.get("failed") or []) - set(y.get("failed") or []), \
            set(y.get("failed") or []) - set(x.get("failed") or [])
        if gone or new:
            out.append(f"checks: now passing {sorted(gone)}, now failing {sorted(new)}")
        if out:
            changed += 1
            print(f"== {name}")
            for line in out:
                print("   ", line)
    print(f"\n{changed} of {len(set(a) | set(b))} invoices changed")
    return changed


def add(corpus: pathlib.Path, rel: str) -> None:
    from app.services.ocr.gemini import GeminiProvider

    data = (corpus / rel).read_bytes()
    manifest_path = corpus / "corpus.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"invoices": []}
    ours = read_free(data, tuple(manifest.get("shop_gstins") or ()))
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
        sys.exit("set GEMINI_API_KEY for the independent reading")
    ai = GeminiProvider().extract(data, "application/pdf", "invoice")
    fields = ours.get("fields") or {}
    parser = (ours.get("meta") or {}).get("pipeline") in ("pdf_parser", "tesseract")
    entry = {"file": rel, "reader": "parser" if parser else "ai", "expect": {}, "todo": [],
             "verified": "our reading and an independent AI reading agree"}
    for path, kind in HEADER:
        a, b = val(fields, path), val(ai, path)
        if a and b and same(kind, a, b):
            entry["expect"][path] = a
        elif a or b:
            entry["todo"].append(f"{path}: ours={a!r} ai={b!r}")
    ol, al = fields.get("line_items") or [], ai.get("line_items") or []
    sa = round(sum(num(val(i, "amount")) or 0 for i in ol), 2)
    sb = round(sum(num(val(i, "amount")) or 0 for i in al), 2)
    if len(ol) == len(al) and abs(sa - sb) <= max(1.0, 0.002 * sb):
        entry["lines"] = {"count": len(ol), "sum_amount": f"{sa:.2f}"}
    else:
        entry["todo"].append(f"lines: ours={len(ol)} (sum {sa}) ai={len(al)} (sum {sb})")
    ver = (ours.get("meta") or {}).get("verification") or {}
    entry["expected_failed"] = sorted(c["id"] for c in ver.get("checks") or [] if c["status"] == "fail")
    manifest["invoices"] = [e for e in manifest["invoices"] if e["file"] != rel] + [entry]
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(entry, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "snapshot":
        snapshot(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    elif cmd == "diff":
        diff(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    elif cmd == "add":
        add(pathlib.Path(sys.argv[2]), sys.argv[3])
    else:
        sys.exit(__doc__)
