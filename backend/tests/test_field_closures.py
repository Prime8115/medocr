"""The header fields closed in the "nothing open" pass: the document title, the
buyer's address under its name, references swept into an address, and the
carrier's name."""
from app.services.ocr.document_kind import title_from_page
from app.services.ocr.invoice_header import _carrier_name
from app.services.ocr.party_address import address_below, without_references


def test_title_anywhere_on_the_page():
    # A layout's text order puts the heading far down.
    text = "\n".join(["Total Invoice Amount in Words:"] + ["x"] * 200 + ["TAX-INVOICE", "INVOICE DATE :"])
    assert title_from_page(text) == "TAX INVOICE"
    assert title_from_page("Bill To :\nTax Invoice-GST\nInvoice No. 12") == "Tax Invoice"
    assert title_from_page("T AX INVOICE T AX INVOICE AIOCD PHARMA") == "TAX INVOICE"
    assert title_from_page("E-Mail : a@b.com GST INVOICE") == "GST INVOICE"


def test_title_never_a_label():
    assert title_from_page("Invoice No : 123\nInvoice Date : 01/01/2025") is None
    assert title_from_page("Tax Invoice No. 55") is None


def _line(y, *cells):
    """Words on one line: (x, text) pairs, each word 6pt per letter."""
    words = []
    for x, text in cells:
        for w in text.split():
            words.append((x, x + 6 * len(w), y, y + 8, w))
            x += 6 * len(w) + 4
    return words


def test_buyer_address_below_its_name():
    words = (_line(100, (40, "EASTERN AGENCIES HEALTHCARE PVT LTD"), (400, "Invoice No : 123"))
             + _line(112, (40, "A-2 FIRST FLOOR TOBACCO HOUSE"), (400, "Order No : 55"))
             + _line(124, (40, "S.V. ROAD VILE PARLE MUMBAI 400056"))
             + _line(136, (40, "GSTIN : 27AAECD7847H1ZC")))
    assert address_below(words, "EASTERN AGENCIES HEALTHCARE PVT LTD", 600) == (
        "A-2 FIRST FLOOR TOBACCO HOUSE, S.V. ROAD VILE PARLE MUMBAI 400056")


def test_buyer_address_stops_at_a_phone_bank_or_the_supplier():
    words = (_line(100, (40, "EASTERN AGENCIES HEALTHCARE"))
             + _line(112, (40, "A-2 FIRST FLOOR 9702905038"))
             + _line(124, (40, "MUMBAI 400056")))
    assert address_below(words, "EASTERN AGENCIES HEALTHCARE", 600) == "A-2 FIRST FLOOR"
    words = (_line(100, (40, "EASTERN AGENCIES HEALTHCARE"))
             + _line(112, (40, "Bank Name : HDFC A/c 50200012345678")))
    assert address_below(words, "EASTERN AGENCIES HEALTHCARE", 600) is None
    words = (_line(100, (40, "EASTERN AGENCIES HEALTHCARE"))
             + _line(112, (40, "FLOOR TOBACCO HOUSE MUMBAI"))
             + _line(124, (40, "Wockhardt Ltd")))
    assert address_below(words, "EASTERN AGENCIES HEALTHCARE", 600, "WOCKHARDT LTD") == "FLOOR TOBACCO HOUSE MUMBAI"


def test_buyer_address_keeps_to_its_own_block():
    # The ship-to block printed beside it, past a wide gap.
    words = (_line(100, (40, "EASTERN AGENCIES HEALTHCARE"))
             + _line(112, (40, "13, Krystal Blue, 1st Floor"), (250, "13, Krystal Blue"))
             + _line(124, (40, "MUMBAI 400055"), (250, "MUMBAI 400055")))
    assert address_below(words, "EASTERN AGENCIES HEALTHCARE", 600) == "13, Krystal Blue, 1st Floor, MUMBAI 400055"


def test_buyer_address_needs_its_name_and_an_address():
    words = _line(100, (40, "EASTERN AGENCIES")) + _line(112, (40, "Thank you for your business"))
    assert address_below(words, "EASTERN AGENCIES", 600) is None
    assert address_below(words, "SOMEONE ELSE", 600) is None
    assert address_below(words, None, 600) is None


def test_references_taken_out_of_an_address():
    printed = ("Bldg. No. E-4, Gala 101 to Order No.: 1017143491 Order Date:12.08.2025, 115, Pimplas, "
               "Delivery No.:2021869597 Delivery Date:14.08.2025, Bhiwandi Payment Due Date:22.08.2025")
    assert without_references(printed) == "Bldg. No. E-4, Gala 101 to, 115, Pimplas, Bhiwandi"
    assert without_references("A-2 FIRST FLOOR, Plot No. 5, VILE PARLE") == "A-2 FIRST FLOOR, Plot No. 5, VILE PARLE"


def test_carrier_name_cut_where_the_next_thing_starts():
    assert _carrier_name("SHINDE TRANSPORT Total Cases 4", "") == "SHINDE TRANSPORT"
    assert _carrier_name("REDSUN 12386.90", "") == "REDSUN"
    assert _carrier_name("SIDDHI ROADWAYS LOCATION", "Transport : SIDDHI ROADWAYS LOCATION: PUNE") == "SIDDHI ROADWAYS"
    assert _carrier_name("BY HAND DELIVERY  LR No 5", "") == "BY HAND DELIVERY"
