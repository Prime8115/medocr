"""Every real invoice we have ever been sent, still read exactly as verified.

Suppliers print their bills every way there is, and a fix for one layout has
broken another before. So every real invoice is kept, with the values a person
(or two independent readers agreeing) confirmed against the paper, and each
one is read again on every change. A change that alters any confirmed value
fails here - no fix for one supplier can quietly break another.

The PDFs are customer documents, so they live in a PRIVATE repository
(Prime8115/medocr-invoices), never in this public one. CI fetches it with a
read-only key; locally, point INVOICE_CORPUS at a clone of it. Without it these
tests skip - except where INVOICE_CORPUS_REQUIRED=1 (CI on main), where a
missing corpus is a failure, so the gate can never be silently switched off.

corpus.json, per invoice:
  file            path of the PDF inside the corpus
  reader          "parser" - read by the free deterministic reader, or
                  "ai" - the parser must DECLINE it (so the AI reads it)
  expect          {"supplier.gstin": ..., "invoice.total_amount": ...}
  lines           {"count": n, "sum_amount": "..."} (optional)
  line_expect     [{"batch_no": ..., "quantity": ..., "amount": ...}, ...]
  expected_failed checks the bill itself fails (its own disagreements)
"""
import json
import os
import pathlib
import re

import pytest

CORPUS = os.environ.get("INVOICE_CORPUS")
REQUIRED = os.environ.get("INVOICE_CORPUS_REQUIRED") == "1"
ROOT = pathlib.Path(CORPUS) if CORPUS else None
MANIFEST = ROOT / "corpus.json" if ROOT else None

_MONEY = {"invoice.total_amount", "invoice.total_taxable_amount", "invoice.total_gst_amount",
          "invoice.total_cgst_amount", "invoice.total_sgst_amount", "invoice.total_igst_amount",
          "invoice.total_discount_amount", "amount", "quantity", "free_quantity", "mrp", "rate",
          "gst_percent", "net_amount", "cgst_amount", "sgst_amount", "igst_amount"}


def _manifest() -> dict:
    if not MANIFEST or not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


ENTRIES = _manifest().get("invoices") or []
# The GSTINs of the shops these bills were sent to - read as production reads a
# known shop's bills (services/shop_identity.py).
SHOP_GSTINS = tuple(_manifest().get("shop_gstins") or ())


def test_the_corpus_is_present_where_it_is_required():
    if REQUIRED:
        assert ENTRIES, f"INVOICE_CORPUS_REQUIRED is set but no corpus.json at {MANIFEST}"
    elif not ENTRIES:
        pytest.skip("no invoice corpus (set INVOICE_CORPUS to a clone of medocr-invoices)")


def _ids(entries):
    return [pathlib.Path(e["file"]).stem[:40] for e in entries]


_cache: dict = {}


def _read(entry: dict) -> dict:
    """The full pipeline, AI switched off - what the free reader makes of it."""
    if entry["file"] not in _cache:
        from app.config import settings
        from app.services.ocr import process_document

        # The AI is never called here: a bill the free reader declines goes to the
        # stand-in reader, so "declined" is visible without a key or a network.
        saved = (settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr)
        settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = None, None, True
        try:
            data = (ROOT / entry["file"]).read_bytes()
            _cache[entry["file"]] = process_document("corpus", data, "application/pdf", doc_type="invoice",
                                                     own_gstins=SHOP_GSTINS)
        finally:
            settings.gemini_api_key, settings.gemini_api_keys, settings.allow_mock_ocr = saved
    return _cache[entry["file"]]


def _value(fields: dict, path: str):
    node = fields
    for part in path.split("."):
        node = (node or {}).get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node = node.get("value")
    return node


def _same(key: str, got, want) -> bool:
    if want is None:
        return got in (None, "")
    if got in (None, ""):
        return False
    if key.split(".")[-1] in {k.split(".")[-1] for k in _MONEY}:
        try:
            return abs(float(str(got).replace(",", "")) - float(str(want).replace(",", ""))) < 0.011
        except ValueError:
            pass
    return re.sub(r"\s+", " ", str(got)).strip().upper() == re.sub(r"\s+", " ", str(want)).strip().upper()


def _parser_read(result: dict) -> bool:
    return (result.get("meta") or {}).get("pipeline") in ("pdf_parser", "tesseract")


@pytest.mark.parametrize("entry", ENTRIES, ids=_ids(ENTRIES))
def test_read_by_the_right_reader(entry):
    """A bill the parser reads correctly stays with it; one it cannot read
    must be DECLINED, never half-read - so the AI gets it whole."""
    read_by_parser = _parser_read(_read(entry))
    assert read_by_parser == (entry.get("reader", "parser") == "parser"), (
        f"expected the {entry.get('reader', 'parser')} to read it")


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e.get("reader", "parser") == "parser"],
                         ids=_ids([e for e in ENTRIES if e.get("reader", "parser") == "parser"]))
def test_confirmed_values_are_read_as_confirmed(entry):
    result = _read(entry)
    fields = result.get("fields") or {}
    wrong = {}
    for path, want in (entry.get("expect") or {}).items():
        got = _value(fields, path)
        if not _same(path, got, want):
            wrong[path] = {"read": got, "confirmed": want}
    lines = fields.get("line_items") or []
    spec = entry.get("lines") or {}
    if "count" in spec and len(lines) != spec["count"]:
        wrong["lines.count"] = {"read": len(lines), "confirmed": spec["count"]}
    if "sum_amount" in spec:
        total = round(sum(float(_value(i, "amount") or 0) for i in lines), 2)
        if not _same("amount", total, spec["sum_amount"]):
            wrong["lines.sum_amount"] = {"read": total, "confirmed": spec["sum_amount"]}
    for n, want_line in enumerate(entry.get("line_expect") or []):
        got_line = lines[n] if n < len(lines) else {}
        for key, want in want_line.items():
            if not _same(key, _value(got_line, key), want):
                wrong[f"line {n + 1}.{key}"] = {"read": _value(got_line, key), "confirmed": want}
    assert not wrong, wrong


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e.get("reader", "parser") == "parser"],
                         ids=_ids([e for e in ENTRIES if e.get("reader", "parser") == "parser"]))
def test_only_the_bills_own_disagreements_fail_a_check(entry):
    verification = (_read(entry).get("meta") or {}).get("verification") or {}
    failed = {c["id"] for c in verification.get("checks") or [] if c["status"] == "fail"}
    assert failed == set(entry.get("expected_failed") or []), [
        c for c in verification.get("checks") or [] if c["status"] == "fail"]
