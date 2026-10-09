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

    python scripts/corpus.py repin <corpus>
        Read every bill again (in parallel) and re-pin each value our reading
        and the stored independent reading (<corpus>/ai_readings) agree on.
        Hand-checked entries and recorded AI-path pins are kept. Prints every
        pin that changed: review it before committing - a lost or changed pin
        is a regression until shown otherwise.

    python scripts/corpus.py record <corpus> [name ...] [--independent <dir>]
        Read each bill the free parser declines with the REAL AI once (needs
        GEMINI_API_KEY), keeping every answer under <corpus>/ai_answers, and
        pin what the pipeline makes of them ("ai_path"). The corpus test then
        replays those answers - the AI path tested offline, at no cost.

Run from the backend directory.
"""
import json
import os
import pathlib
import re
import sys
from typing import Optional

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
    entries, own = manifest["invoices"], manifest.get("shop_gstins") or ()
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
    ours = read_free(data, manifest.get("shop_gstins") or ())
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


READINGS = "ai_readings"


def _read_entry(args) -> tuple:
    """Worker: one corpus entry read by the free reader (repin)."""
    import logging

    logging.disable(logging.WARNING)
    corpus, entry, own = args
    data = (pathlib.Path(corpus) / entry["file"]).read_bytes()
    if "part" in entry:
        from app.services import intake
        data = intake.pdf_pages(data, intake.invoice_groups(data)[int(entry["part"])])
    try:
        free = read_free(data, own)
    except Exception as exc:  # noqa: BLE001 - reported, never silently skipped
        return entry["file"], entry.get("part"), {"error": str(exc)}, None
    # A bill with recorded AI answers is also read on them (no AI call), so
    # its AI-path pins follow any change to what we do with an answer.
    from app.services.ocr.recorded import ReplayProvider, answers_dir

    store = answers_dir(pathlib.Path(corpus) / ANSWERS, entry["file"], entry.get("part"))
    on_answers = None
    if store.exists():
        try:
            on_answers = read_with(data, ReplayProvider(store), own)
        except Exception as exc:  # noqa: BLE001
            on_answers = {"error": str(exc)}
    return entry["file"], entry.get("part"), free, on_answers


def pins_for(entry: dict, ours: dict, independent: Optional[dict]) -> dict:
    """An entry's pins from our reading: each value an independent reading of
    the same bill agrees with. Where they disagree nothing is pinned - it goes
    to `todo`, to be settled against the paper."""
    meta, fields = ours.get("meta") or {}, ours.get("fields") or {}
    parser = meta.get("pipeline") in ("pdf_parser", "tesseract")
    new = {k: entry[k] for k in ("file", "part") if k in entry}
    new.update({"reader": "parser" if parser else "ai", "expect": {}, "todo": []})
    if not parser:
        # Read by the AI: its recorded AI-path pins stand (record).
        if entry.get("ai_path"):
            new["ai_path"] = entry["ai_path"]
        return new
    if independent is None:
        new["verified"] = "not yet - no second reading"
    else:
        new["verified"] = "our reading and an independent AI reading agree"
        # The independent reading is of the WHOLE file. For one invoice of a
        # split file only the parties compare - the same on every invoice in it.
        part = "part" in entry
        for path, kind in HEADER:
            if part and not path.startswith(("supplier.", "bill_to.")):
                continue
            a, b = val(fields, path), val(independent, path)
            if a and b and same(kind, a, b):
                new["expect"][path] = a
            elif a or b:
                new["todo"].append(f"{path}: ours={a!r} independent={b!r}")
        ol, al = fields.get("line_items") or [], independent.get("line_items") or []
        sa = round(sum(num(val(i, "amount")) or 0 for i in ol), 2)
        sb = round(sum(num(val(i, "amount")) or 0 for i in al), 2)
        if part and meta.get("total_reconciles") is True:
            # Its lines build up to its own printed total: the bill proves both.
            new["lines"] = {"count": len(ol), "sum_amount": f"{sa:.2f}"}
            if val(fields, "invoice.total_amount"):
                new["expect"]["invoice.total_amount"] = val(fields, "invoice.total_amount")
        elif part:
            new["todo"].append("lines: one invoice of a split file, not reconciled - not pinned")
        elif len(ol) == len(al) and abs(sa - sb) <= max(1.0, 0.002 * sb):
            new["lines"] = {"count": len(ol), "sum_amount": f"{sa:.2f}"}
            new["line_expect"] = [{k: val(o, k) for k, kind in LINE
                                   if val(o, k) and val(a, k) and same(kind, val(o, k), val(a, k))}
                                  for o, a in list(zip(ol, al))[:3]]
        else:
            new["todo"].append(f"lines: ours={len(ol)} (sum {sa}) independent={len(al)} (sum {sb})")
    ver = meta.get("verification") or {}
    new["expected_failed"] = sorted(c["id"] for c in ver.get("checks") or [] if c["status"] == "fail")
    if new["expected_failed"]:
        new["todo"].append("checks failing - confirm each is the bill's own: " + ", ".join(new["expected_failed"]))
    return new


def _pin_changes(old: dict, new: dict) -> list:
    out = []
    if old.get("reader") != new.get("reader"):
        out.append(f"READER {old.get('reader')} -> {new.get('reader')}")
    oe, ne = old.get("expect") or {}, new.get("expect") or {}
    for k in sorted(set(oe) | set(ne)):
        if k not in ne:
            out.append(f"- {k} (was {oe[k]!r})")
        elif k not in oe:
            out.append(f"+ {k} = {ne[k]!r}")
        elif oe[k] != ne[k]:
            out.append(f"~ {k}: {oe[k]!r} -> {ne[k]!r}")
    if old.get("lines") != new.get("lines"):
        out.append(f"lines {old.get('lines')} -> {new.get('lines')}")
    oa, na = old.get("ai_path") or {}, new.get("ai_path") or {}
    for k in sorted(set(oa.get("expect") or {}) | set(na.get("expect") or {})):
        a, b = (oa.get("expect") or {}).get(k), (na.get("expect") or {}).get(k)
        if a != b:
            out.append(f"ai path {k}: {a!r} -> {b!r}")
    if oa.get("lines") != na.get("lines"):
        out.append(f"ai path lines {oa.get('lines')} -> {na.get('lines')}")
    if set(oa.get("expected_failed") or []) != set(na.get("expected_failed") or []):
        out.append(f"ai path checks failing {oa.get('expected_failed')} -> {na.get('expected_failed')}")
    of, nf = set(old.get("expected_failed") or []), set(new.get("expected_failed") or [])
    if of - nf:
        out.append(f"checks now passing: {sorted(of - nf)}")
    if nf - of:
        out.append(f"CHECKS NOW FAILING: {sorted(nf - of)}")
    return out


def repin(corpus: pathlib.Path, workers: int = 4) -> None:
    """Read every bill again and re-pin it. Hand-checked entries keep their
    pins; an AI-read bill keeps its recorded AI-path pins. Prints every pin
    that changed - review it before committing corpus.json: a lost or changed
    pin is a regression until shown otherwise."""
    from concurrent.futures import ProcessPoolExecutor

    from app.services import intake

    manifest_path = corpus / "corpus.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    own = manifest.get("shop_gstins") or ()
    # A PDF that upload now splits into several invoices becomes one entry per
    # invoice - the corpus reads each bill as upload hands it over.
    expanded = []
    for e in manifest["invoices"]:
        if "part" not in e and e.get("verified") != "checked by hand":
            groups = intake.invoice_groups((corpus / e["file"]).read_bytes())
            if len(groups) > 1:
                print(f"SPLIT {e['file']}: {len(groups)} invoices")
                expanded += [{"file": e["file"], "part": n} for n in range(len(groups))]
                continue
        expanded.append(e)
    manifest["invoices"] = expanded
    entries = [e for e in expanded if e.get("verified") != "checked by hand"]
    readings, replays = {}, {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (file, part, result, on_answers) in enumerate(
                pool.map(_read_entry, [(str(corpus), e, own) for e in entries]), 1):
            readings[(file, part)] = result
            replays[(file, part)] = on_answers
            print(f"\r{n}/{len(entries)}", end="", flush=True)
    print()
    changed, out = 0, []
    for entry in manifest["invoices"]:
        key_ = (entry["file"], entry.get("part"))
        if key_ not in readings:
            out.append(entry)
            continue
        ours = readings[key_]
        if "error" in ours:
            print(f"!! {entry['file']}: {ours['error']} - kept as it was")
            out.append(entry)
            continue
        path = corpus / READINGS / (pathlib.Path(entry["file"]).name + ".json")
        independent = json.loads(path.read_text(encoding="utf-8")).get("fields") if path.exists() else None
        new = pins_for(entry, ours, independent)
        replay = replays.get(key_)
        if new["reader"] == "ai" and replay and "error" not in replay:
            new["ai_path"] = ai_path_pins(replay, independent if "part" not in entry else None)
        notes = _pin_changes(entry, new)
        if notes:
            changed += 1
            print(f"== {entry['file']}" + (f" #{entry['part'] + 1}" if "part" in entry else ""))
            for note in notes:
                print("   ", note)
        out.append(new)
    manifest["invoices"] = out
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{changed} of {len(entries)} entries changed - review before committing corpus.json")


ANSWERS = "ai_answers"


def read_with(data: bytes, provider, own_gstins=()) -> dict:
    """The full pipeline with `provider` as the AI (recorded.py)."""
    import app.services.ocr as ocr
    from app.config import settings
    from app.services.ocr import process_document

    # The Tesseract cross-read and the AI review are checkers ON TOP of the
    # reading: the first depends on the machine's Tesseract, the second asks
    # about every value read, so any upstream change would leave it unanswered.
    # Both are off when recording and replaying, the same way.
    # Page chunks are read one at a time, so a scan's readings are recorded -
    # and replayed - in page order.
    saved = (ocr.get_provider, settings.allow_mock_ocr, settings.ocr_cross_read, settings.ocr_ai_review,
             settings.ocr_chunk_concurrency)
    ocr.get_provider = lambda: provider
    settings.allow_mock_ocr, settings.ocr_cross_read, settings.ocr_ai_review = False, False, False
    settings.ocr_chunk_concurrency = 1
    try:
        return process_document("corpus", data, "application/pdf", doc_type="invoice", own_gstins=own_gstins)
    finally:
        (ocr.get_provider, settings.allow_mock_ocr, settings.ocr_cross_read, settings.ocr_ai_review,
         settings.ocr_chunk_concurrency) = saved


def ai_path_pins(result: dict, independent: Optional[dict]) -> dict:
    """What the AI path's reading pins: each value an independent AI reading of
    the same bill agrees with, and the lines when they build up to the bill's
    own total. The bill's own failing checks are recorded as expected."""
    fields, meta = result.get("fields") or {}, result.get("meta") or {}
    pins: dict = {"expect": {}, "todo": []}
    for path, kind in HEADER:
        a, b = val(fields, path), val(independent or {}, path)
        if a and b and same(kind, a, b):
            pins["expect"][path] = a
        elif a or b:
            pins["todo"].append(f"{path}: ai path={a!r} independent={b!r}")
    lines = fields.get("line_items") or []
    if meta.get("total_reconciles") is True:
        pins["lines"] = {"count": len(lines),
                         "sum_amount": f"{round(sum(num(val(i, 'amount')) or 0 for i in lines), 2):.2f}"}
    else:
        pins["todo"].append(f"lines: {len(lines)} read, not reconciled - not pinned")
    ver = meta.get("verification") or {}
    pins["expected_failed"] = sorted(c["id"] for c in ver.get("checks") or [] if c["status"] == "fail")
    pins["reader"] = meta.get("pipeline")
    return pins


def record(corpus: pathlib.Path, names: list, independent_dir: Optional[pathlib.Path]) -> None:
    """Read each AI-read bill with the real AI once, keeping every answer under
    <corpus>/ai_answers, and pin what the pipeline makes of them."""
    import logging

    from app.services.ocr.gemini import GeminiProvider
    from app.services.ocr.recorded import RecordingProvider, answers_dir

    # Each AI call is shown as it happens: recording is slow, and the reason
    # (the model resting, a rate limit, a retry) should be visible.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("app.services.ocr.gemini").setLevel(logging.INFO)
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
        sys.exit("set GEMINI_API_KEY - recording asks the real AI")
    manifest_path = corpus / "corpus.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    own = manifest.get("shop_gstins") or ()
    for entry in manifest["invoices"]:
        if entry.get("reader") != "ai" or (names and not any(n.lower() in entry["file"].lower() for n in names)):
            continue
        data = (corpus / entry["file"]).read_bytes()
        if "part" in entry:
            from app.services import intake
            data = intake.pdf_pages(data, intake.invoice_groups(data)[int(entry["part"])])
        provider = RecordingProvider(GeminiProvider(), answers_dir(corpus / ANSWERS, entry["file"], entry.get("part")))
        try:
            result = read_with(data, provider, own)
        except Exception as exc:  # noqa: BLE001 - one bill's failure is reported, the rest go on
            print(f"!! {entry['file']}: {exc}")
            continue
        independent = None
        if independent_dir:
            path = independent_dir / (pathlib.Path(entry["file"]).name + ".json")
            if path.exists() and "part" not in entry:
                independent = json.loads(path.read_text(encoding="utf-8")).get("fields")
        entry["ai_path"] = ai_path_pins(result, independent)
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        meta = result.get("meta") or {}
        print(f"{entry['file'][:50]:50} {meta.get('pipeline')} lines={meta.get('item_count')} "
              f"rec={meta.get('total_reconciles')} pinned={len(entry['ai_path']['expect'])}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "snapshot":
        snapshot(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    elif cmd == "diff":
        diff(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    elif cmd == "add":
        add(pathlib.Path(sys.argv[2]), sys.argv[3])
    elif cmd == "repin":
        repin(pathlib.Path(sys.argv[2]))
    elif cmd == "record":
        args = sys.argv[2:]
        independent = None
        if "--independent" in args:
            at = args.index("--independent")
            independent = pathlib.Path(args[at + 1])
            args = args[:at] + args[at + 2:]
        record(pathlib.Path(args[0]), args[1:], independent)
    else:
        sys.exit(__doc__)
