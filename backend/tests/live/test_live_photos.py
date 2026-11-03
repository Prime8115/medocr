"""Photos and handwriting, read by the REAL AI - scored field by field.

Three sets, each run through process_document exactly as an upload is:

1. Real phone photos: 10 receipts from CORD v2 (naver-clova-ix/cord-v2, test
   split, CC BY 4.0 - samples/cord/answers.json holds their published
   answers). Blurred, curled, photographed on a table.
2. Handwritten pharmacy bills: a "kachha" bill written in a handwriting font
   (photo_kit.py), photographed. Every value is known exactly.
3. Photos of the client's own invoices: each single-page bill of the private
   corpus, rendered and photographed (photo_kit.camera), checked against its
   confirmed values. Run on a developer's machine only (INVOICE_CORPUS and a
   key there) - never in this public repository's CI - and even so only
   SCORES are printed, never a value off a bill.

Each set's score is written as a GitHub annotation (readable on the run's page
and through the public API) and must stay above its floor. Skipped unless
RUN_LIVE_AI=1 with a key, and RUN_PHOTO_SUITE=1 (about 16 AI reads; 28 with the corpus).
"""
import json
import os
import pathlib
import re
import sys
from collections import Counter
from typing import Dict, List, Optional

import pytest

from app.config import settings
from app.services.ocr.base import OCRError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_AI") != "1" or not settings.gemini_keys_list
    or os.environ.get("RUN_PHOTO_SUITE") != "1",
    reason="live photo checks run only with RUN_LIVE_AI=1, RUN_PHOTO_SUITE=1 and a GEMINI_API_KEY",
)

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import photo_kit  # noqa: E402


# ------------------------------------------------------------------ helpers

_BUSY: List[str] = []   # why the AI refused, reported once per set


def _read(image: bytes) -> Optional[dict]:
    """The whole pipeline on a photo; None when the AI stays busy (not scored).
    Paced and retried: one photo is several AI calls, and a burst of them
    trips a key's per-minute limit."""
    import time

    from app.services.ocr import process_document

    for attempt in range(3):
        time.sleep(8 if attempt == 0 else 45)
        try:
            return process_document("live-photo", image, "image/jpeg", "invoice")
        except OCRError as exc:
            if exc.kind != "busy":
                raise
            _BUSY.append(str(exc)[:200])
    return None


def _v(node, key):
    leaf = (node or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _num(text) -> Optional[float]:
    text = re.sub(r"[^\d.,-]", "", str(text or ""))
    if not text:
        return None
    # "17.500" / "17,500" (thousands) and "1,234.50" (decimals) alike.
    if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", text):
        return float(re.sub(r"[.,]", "", text))
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _same_num(a, b) -> bool:
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(x - y) < 0.011


def _squash(text) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _same_date(a, b) -> bool:
    da = [int(p) for p in re.findall(r"\d+", str(a or ""))]
    db = [int(p) for p in re.findall(r"\d+", str(b or ""))]
    norm = lambda d: [x % 100 if i == len(d) - 1 else x for i, x in enumerate(d)]  # noqa: E731
    return bool(da) and norm(da) == norm(db)


class Score:
    """Right / asked, per field, with the misses kept for the report."""

    def __init__(self, name: str):
        self.name, self.right, self.asked, self.misses = name, Counter(), Counter(), []
        self.unread = 0

    def check(self, field: str, ok: bool, miss: str = "") -> None:
        self.asked[field] += 1
        self.right[field] += bool(ok)
        if not ok and miss:
            self.misses.append(f"{field}: {miss}")

    def rate(self) -> float:
        asked = sum(self.asked.values())
        return sum(self.right.values()) / asked if asked else 0.0

    def report(self, show_misses: bool = True) -> None:
        """GitHub annotations, written past pytest's capture."""
        parts = ", ".join(f"{f} {self.right[f]}/{self.asked[f]}" for f in sorted(self.asked))
        out = sys.__stdout__
        out.write(f"::notice title={self.name}::{self.rate():.1%} of fields right ({parts}); "
                  f"{self.unread} not read (AI busy)\n")
        for reason in sorted(set(_BUSY))[:5]:
            out.write(f"::warning title={self.name} AI busy::{reason}\n")
        _BUSY.clear()
        if show_misses:
            for m in self.misses[:40]:
                out.write(f"::warning title={self.name} miss::{m[:300]}\n")
        out.flush()


def _match_lines(items: List[dict], names: List[str]) -> Dict[int, dict]:
    """Each true line's read line, by name (one read line per true line)."""
    used, out = set(), {}
    for i, name in enumerate(names):
        want = _squash(name)
        best = None
        for j, it in enumerate(items):
            got = _squash(_v(it, "description"))
            if j in used or not got:
                continue
            if want == got or (len(want) >= 4 and (want in got or got in want)):
                best = j
                break
        if best is not None:
            used.add(best)
            out[i] = items[best]
    return out


# --------------------------------------------------------- 1. real photos

def test_real_phone_photos_of_receipts():
    answers = json.loads((HERE / "samples" / "cord" / "answers.json").read_text(encoding="utf-8"))
    score = Score("Real phone photos (CORD)")
    for receipt in answers["receipts"]:
        image = (HERE / "samples" / "cord" / receipt["file"]).read_bytes()
        result = _read(image)
        if result is None:
            score.unread += 1
            continue
        fields = result["fields"]
        items = fields.get("line_items") or []
        tag = receipt["file"]
        score.check("total", _same_num(_v(fields.get("invoice"), "total_amount"), receipt["total"]),
                    f"{tag} read {_v(fields.get('invoice'), 'total_amount')!r}, is {receipt['total']}")
        score.check("line count", len(items) == len(receipt["lines"]),
                    f"{tag} read {len(items)}, is {len(receipt['lines'])}")
        found = _match_lines(items, [l["name"] for l in receipt["lines"]])
        for i, line in enumerate(receipt["lines"]):
            it = found.get(i)
            score.check("line name", it is not None, f"{tag} {line['name']!r} not found")
            if it is None:
                continue
            # CORD's price is the line's total.
            score.check("line amount", any(_same_num(_v(it, k), line["price"])
                                           for k in ("amount", "net_amount", "gross_amount")),
                        f"{tag} {line['name']!r} amount {_v(it, 'amount')!r}, is {line['price']}")
            if line.get("qty"):
                score.check("line qty", _same_num(_v(it, "quantity"), line["qty"]),
                            f"{tag} {line['name']!r} qty {_v(it, 'quantity')!r}, is {line['qty']}")
    score.report()
    assert score.unread < len(answers["receipts"]), "the AI read none of the photos"
    assert score.rate() >= 0.70, f"{score.name}: {score.rate():.1%} of fields right"


# ------------------------------------------------------- 2. handwriting

def test_handwritten_pharmacy_bills():
    score = Score("Handwritten bills (photographed)")
    for seed in range(6):
        page, truth = photo_kit.handwritten_bill(seed)
        result = _read(photo_kit.camera(page, seed))
        if result is None:
            score.unread += 1
            continue
        fields = result["fields"]
        inv = fields.get("invoice") or {}
        items = fields.get("line_items") or []
        tag = f"bill {seed}"
        score.check("bill no", _squash(_v(inv, "invoice_no")) == _squash(truth["bill_no"]),
                    f"{tag} read {_v(inv, 'invoice_no')!r}, is {truth['bill_no']}")
        score.check("date", _same_date(_v(inv, "invoice_date"), truth["date"]),
                    f"{tag} read {_v(inv, 'invoice_date')!r}, is {truth['date']}")
        score.check("total", _same_num(_v(inv, "total_amount"), truth["total"]),
                    f"{tag} read {_v(inv, 'total_amount')!r}, is {truth['total']}")
        score.check("line count", len(items) == len(truth["lines"]),
                    f"{tag} read {len(items)}, is {len(truth['lines'])}")
        found = _match_lines(items, [l["name"] for l in truth["lines"]])
        for i, line in enumerate(truth["lines"]):
            it = found.get(i)
            score.check("name", it is not None, f"{tag} {line['name']!r} not found")
            if it is None:
                continue
            got = {k: _v(it, k) for k in ("batch_no", "expiry", "quantity", "rate", "amount")}
            score.check("batch", _squash(got["batch_no"]) == _squash(line["batch"]),
                        f"{tag} {line['name']} batch {got['batch_no']!r}, is {line['batch']}")
            score.check("expiry", _same_date(got["expiry"], line["expiry"]),
                        f"{tag} {line['name']} expiry {got['expiry']!r}, is {line['expiry']}")
            score.check("qty", _same_num(got["quantity"], line["qty"]),
                        f"{tag} {line['name']} qty {got['quantity']!r}, is {line['qty']}")
            score.check("rate", _same_num(got["rate"], line["rate"]),
                        f"{tag} {line['name']} rate {got['rate']!r}, is {line['rate']}")
            score.check("amount", _same_num(got["amount"], line["amount"]),
                        f"{tag} {line['name']} amount {got['amount']!r}, is {line['amount']}")
    score.report()
    assert score.unread < 6, "the AI read none of the bills"
    assert score.rate() >= 0.80, f"{score.name}: {score.rate():.1%} of fields right"


# --------------------------------------- 3. photos of the client's invoices

_CORPUS = os.environ.get("INVOICE_CORPUS")
_HEADER_PINS = ("invoice.invoice_no", "invoice.invoice_date", "invoice.total_amount",
                "supplier.gstin", "bill_to.gstin")
_LINE_PINS = ("description", "batch_no", "expiry", "quantity", "rate", "amount", "mrp")


@pytest.mark.skipif(not _CORPUS, reason="needs the private invoice corpus (INVOICE_CORPUS)")
def test_photos_of_the_clients_printed_invoices():
    """Customer bills: scores only, never a value (the logs are public)."""
    from app.services.ocr.pdf_utils import page_count
    from app.services.ocr.pdfium_safe import render_pages

    root = pathlib.Path(_CORPUS)
    entries = json.loads((root / "corpus.json").read_text(encoding="utf-8"))["invoices"]
    chosen = []
    for e in entries:
        if e.get("reader", "parser") != "parser" or "part" in e:
            continue
        data = (root / e["file"]).read_bytes()
        if page_count(data) == 1:
            chosen.append((e, data))
        if len(chosen) == 12:
            break
    score = Score("Photos of the client's invoices")
    for n, (entry, data) in enumerate(chosen):
        page = next(iter(render_pages(data, 150 / 72, max_pages=1)))
        result = _read(photo_kit.camera(page, 100 + n))
        if result is None:
            score.unread += 1
            continue
        fields = result["fields"]
        expect = entry.get("expect") or {}
        for path in _HEADER_PINS:
            if path in expect and expect[path] is not None:
                section, _, key = path.partition(".")
                got, want = _v(fields.get(section), key), expect[path]
                ok = (_same_num(got, want) if key == "total_amount" else
                      _same_date(got, want) if key == "invoice_date" else _squash(got) == _squash(want))
                score.check(path, ok)
        items = fields.get("line_items") or []
        count = (entry.get("lines") or {}).get("count")
        if count:
            score.check("line count", len(items) == count)
        for i, want_line in enumerate(entry.get("line_expect") or []):
            got_line = items[i] if i < len(items) else {}
            for key in _LINE_PINS:
                if key in want_line and want_line[key] is not None:
                    got, want = _v(got_line, key), want_line[key]
                    ok = (_same_num(got, want) if key in ("quantity", "rate", "amount", "mrp") else
                          _same_date(got, want) if key == "expiry" else _squash(got) == _squash(want))
                    score.check(f"line {key}", ok)
    score.report(show_misses=False)
    assert chosen, "no single-page corpus bills found"
    assert score.unread < len(chosen), "the AI read none of the photos"
    assert score.rate() >= 0.80, f"{score.name}: {score.rate():.1%} of fields right"
