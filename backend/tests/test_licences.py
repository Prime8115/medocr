"""The supplier's drug licences come from its own block, with their dates."""
from io import BytesIO
from pathlib import Path

import pytest

from app.services.ocr.licences import apply_supplier_licences, licences_beside, parse_licences

SUPPLIER, BUYER = "27ABCDE1234F3ZY", "24PQRST6789K1ZW"


@pytest.mark.parametrize("text,expected", [
    ("D.L. No. :20B 254080 DT.01.01.18 , 21B 254081 DT.01.01.18",
     [("20B 254080", "01.01.18"), ("21B 254081", "01.01.18")]),
    (": 20B-227724 , 21B-227725", [("20B-227724", None), ("21B-227725", None)]),
    ("D.L.No:F20B TZ5-24902 & F21B TZ5-24903", [("F20B TZ5-24902", None), ("F21B TZ5-24903", None)]),
    ("Drug Lic No: MH-TZ2-491363 Valid upto 11.09.2027", [("MH-TZ2-491363", "11.09.2027")]),
    ("D.L. No. :", []),
])
def test_parse_licences(text, expected):
    assert parse_licences(text) == expected


def _bill() -> bytes:
    """Buyer's block at the top with its own licences; the supplier's GSTIN and
    licences in a block at the foot; a godown's licence in the footer."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(20, 780, "Billed To : SOME BUYER")
    c.drawString(20, 768, f"GSTIN : {BUYER}")
    c.drawString(20, 756, "D.L. No. : 20B-227724 , 21B-227725")
    c.drawString(20, 300, f"GSTIN :{SUPPLIER}   P.A.No.:ABCDE1234F")
    c.drawString(20, 288, "D.L. No. :20B 254080 DT.01.01.18 , 21B 254081 DT.01.01.18")
    c.drawString(330, 288, "H. L. MEDICINE")
    c.drawString(300, 40, "D.L.No:F20B TZ5-24902 & F21B TZ5-24903")
    c.save()
    return buf.getvalue()


def test_the_licences_under_a_gstin_are_its_own():
    data = _bill()
    assert licences_beside(data, SUPPLIER) == [("20B 254080", "01.01.18"), ("21B 254081", "01.01.18")]
    assert licences_beside(data, BUYER) == [("20B-227724", None), ("21B-227725", None)]
    assert licences_beside(data, "29LMNOP4321Q1Z0") == []


def test_the_supplier_gets_its_own_licences_and_dates():
    fields = {"supplier": {"gstin": {"value": SUPPLIER}, "dl_no_1": {"value": "F20B TZ5-24902"},
                           "dl_no_2": {"value": "F21B TZ5-24903"}}}
    [warning] = apply_supplier_licences(fields, _bill())
    s = fields["supplier"]
    assert [s["dl_no_1"]["value"], s["dl_date_1"]["value"], s["dl_no_2"]["value"], s["dl_date_2"]["value"]] == \
        ["20B 254080", "01.01.18", "21B 254081", "01.01.18"]
    assert s["dl_no_3"]["value"] is None
    assert "F20B TZ5-24902" in warning


def test_nothing_changes_without_a_licence_line_under_the_gstin():
    fields = {"supplier": {"gstin": {"value": "29LMNOP4321Q1Z0"}, "dl_no_1": {"value": "KA-123"}}}
    assert apply_supplier_licences(fields, _bill()) == []
    assert fields["supplier"]["dl_no_1"]["value"] == "KA-123"


_REAL = Path(__file__).parent / "real_invoices" / "SHREE PARIKH TRADING.pdf"


@pytest.mark.skipif(not _REAL.exists(), reason="customer PDF, kept out of the repo")
def test_v_n_pharma_bill():
    """The client's report: DL numbers wrong, their dates blank."""
    from app.services.ocr.pdf_utils import extract_text_pages
    from app.services.ocr.party_check import page_party_gstins

    data = _REAL.read_bytes()
    text = extract_text_pages(data)[0]
    supplier = next(g for g in page_party_gstins(text)[2] if g.startswith("27"))
    found = licences_beside(data, supplier)
    assert [d for _n, d in found] == ["01.01.18", "01.01.18"]
    assert all(n.split()[0] in ("20B", "21B") for n, _d in found)
