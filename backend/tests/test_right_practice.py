"""Rate, buyer name and drug licences, the way a purchase register keeps them."""
from io import BytesIO

import pytest

from app.services.ocr import parties
from app.services.ocr.invoice_checks import resolve_billed_rate


def _f(value):
    return {"value": value, "confidence": 0.9}


# --- the rate: the price the bill charges, found by its arithmetic --------------------

def test_a_net_selling_price_column_is_the_rate_it_charges():
    """Mylan charges "NSP/ Unit" 131.79: 160 x 131.79 = 21,085.71. Its PTR is
    146.43, and is not the rate."""
    item = {"quantity": _f("160"), "amount": _f("21085.71"), "ptr": _f("146.43"),
            "extras": [{"label": "NSP/ Unit", "value": "131.79", "confidence": 1.0}]}
    resolve_billed_rate([item])
    assert item["rate"]["value"] == "131.79" and item["rate_source"]["value"] == "NSP/ Unit"


def test_a_price_inside_a_merged_cell_is_taken_only_when_its_arithmetic_holds():
    """East India: "25.71 6427.50 0.00 0.00" under one long heading naming PTW."""
    label = "Total Value(PTW) Product Discount* Rate Amount Rate(%) Amount"
    item = {"quantity": _f("250"), "amount": _f("6427.50"), "ptr": _f("28.57"),
            "extras": [{"label": label, "value": "25.71 6427.50 0.00 0.00", "confidence": 1.0}]}
    resolve_billed_rate([item])
    assert item["rate"]["value"] == "25.71" and item["rate_source"]["value"] == "PTW"
    # A figure that is not what the line was charged is never picked out.
    other = {"quantity": _f("250"), "amount": _f("6427.50"), "ptr": _f("28.57"),
             "extras": [{"label": label, "value": "19.00 6427.50 0.00 0.00", "confidence": 1.0}]}
    resolve_billed_rate([other])
    assert other["rate"]["value"] == "28.57"


# --- the buyer: the shop itself, under the name its GSTIN goes by -------------------------

@pytest.fixture
def shop():
    token = parties.OWN_GSTINS.set({"27ABCDE1234F1Z5": "SUNRISE MEDICAL DISTRIBUTORS PVT LTD",
                                    "27PQRST5678K1Z2": "SHREE GANESH CHEMIST"})
    yield
    parties.OWN_GSTINS.reset(token)


@pytest.mark.parametrize("printed, gstin, want", [
    ("SUNRISE MEDICAL DISTRIBUTORS PRIVATE LIMITED", "27ABCDE1234F1Z5", "SUNRISE MEDICAL DISTRIBUTORS PVT LTD"),
    ("M/s SUNRISE MEDICAL DISTRIBUTORS", "27ABCDE1234F1Z5", "SUNRISE MEDICAL DISTRIBUTORS PVT LTD"),
    ("GST NO : 27PQRST5678K1Z2 SHREE GANESH CHEMIST (ABCPL D L No : 20B-MH-MZ4", "27PQRST5678K1Z2",
     "SHREE GANESH CHEMIST"),
    # A code or a label around the shop's name.
    ("MUM025 SUNRISE", "27ABCDE1234F1Z5", "SUNRISE MEDICAL DISTRIBUTORS PVT LTD"),
    ("[7381] SUNRISE MEDICAL DISTRIBUTORS PVT. LTD", "27ABCDE1234F1Z5", "SUNRISE MEDICAL DISTRIBUTORS PVT LTD"),
    ("S. SUNRISE MEDICAL", "27ABCDE1234F1Z5", "SUNRISE MEDICAL DISTRIBUTORS PVT LTD"),
    # A trade name of its own stays as the bill prints it.
    ("SHREE GANESH CHEMIST", "27ABCDE1234F1Z5", "SHREE GANESH CHEMIST"),
])
def test_the_buyer_is_named_as_its_gstin_goes_by(shop, printed, gstin, want):
    fields = {"bill_to": {"name": _f(printed), "gstin": _f(gstin)}}
    parties._tidy_names(fields)
    parties._complete_own_name(fields)
    assert fields["bill_to"]["name"]["value"] == want


# --- drug licences: the supplier's, never the buyer's -------------------------------------

def _bill_pdf():
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 400))
    c.setFont("Helvetica", 8)
    c.drawString(20, 360, "GSTIN : 27LMNOP9012Q1ZX")
    c.drawString(20, 350, "D.L.No. : 20B-MH-MZ6-456701, 21B-MH-MZ6-456702")
    # The buyer's licence printed on its GSTIN's own line (Agresco).
    c.drawString(300, 360, "GST : 27ABCDE1234F1Z5 D.L.No. : MH-MZ5-123401/MH-MZ5-123402")
    c.save()
    return buf.getvalue()


def test_the_buyers_licence_is_never_filed_as_the_suppliers(shop):
    from app.services.ocr.licences import apply_supplier_licences

    # Before the parties are settled the "supplier" GSTIN is still the buyer's.
    fields = {"supplier": {"gstin": _f("27ABCDE1234F1Z5"), "dl_no_1": _f("MH-MZ5-123401"),
                           "dl_no_2": _f("20B-MH-MZ6-456701")}}
    apply_supplier_licences(fields, _bill_pdf())
    numbers = [(fields["supplier"].get(f"dl_no_{n}") or {}).get("value") for n in (1, 2, 3)]
    assert numbers == ["20B-MH-MZ6-456701", None, None]


def test_the_suppliers_own_licences_are_taken_from_beside_its_gstin(shop):
    from app.services.ocr.licences import apply_supplier_licences

    fields = {"supplier": {"gstin": _f("27LMNOP9012Q1ZX"), "dl_no_1": _f("MH-MZ5-123401")}}
    apply_supplier_licences(fields, _bill_pdf())
    numbers = [(fields["supplier"].get(f"dl_no_{n}") or {}).get("value") for n in (1, 2, 3)]
    assert numbers == ["20B-MH-MZ6-456701", "21B-MH-MZ6-456702", None]
