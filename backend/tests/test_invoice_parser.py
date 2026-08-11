"""Deterministic invoice table parser — column mapping and row extraction."""
from app.services.ocr import invoice_parser as ip


# A synthetic distributor-invoice table (list of rows, cells may be None).
HEADER = ["SR.\nNO.", "PRD.\nCODE", "PRODUCT NAME", None, "HSN\nCODE", "PACK",
          "BATCH", "MFG.\nDATE", "EXP.D\nATE", "MRP", "PTR", "PTR%",
          "CGST/\nIGST %", "SGST/\nUTGST %", "QTY.", "FREE\nQTY", "VALUE"]
ROW1 = ["001", "5035460", "ESPRA 40 TAB", None, "30049039", "10 X 10",
        "TB-022501", "02/2025", "01/2027", "108.90", "77.79", "10.00",
        "6.00", "6.00", "25", "5", "1,750.25"]
ROW2 = ["002", "5036784", "IMOL PLUS TAB", None, "30049063", "20 X 2",
        "BEB1106", "05/2025", "04/2027", "23.70", "16.93", "10.00",
        "6.00", "6.00", "240", "", "3,657.60"]
TOTAL = ["", "", "TOTAL", None, "", "", "", "", "", "", "", "", "", "", "", "", "9,999.00"]


def test_find_header_row():
    table = [["Zydus TAX INVOICE"] + [None] * 16, HEADER, ROW1]
    assert ip._find_header_row(table) == 1


def test_map_columns():
    cols = ip._map_columns(HEADER)
    assert cols["description"] == 2
    assert cols["hsn"] == 4
    assert cols["batch_no"] == 6
    assert cols["expiry"] == 8          # exp, not mfg date (7)
    assert cols["mrp"] == 9
    assert cols["rate"] == 10           # PTR, not PTR% (11)
    assert cols["quantity"] == 14       # QTY, not FREE QTY (15)
    assert cols["amount"] == 16


def test_gst_columns_sum():
    gst = ip._gst_columns(HEADER)
    assert gst == [12, 13]              # CGST% + SGST%


def test_num_cleaning():
    assert ip._num("1,750.25") == "1750.25"
    assert ip._num("qty 25 nos") == "25"
    assert ip._num("abc") is None


def test_parse_rows_via_mapping():
    # Exercise the row-building the same way parse_invoice_pdf does, without a PDF.
    table = [HEADER, ROW1, ROW2, TOTAL]
    hi = ip._find_header_row(table)
    assert hi == 0
    cols = ip._map_columns(table[hi])
    gst_cols = ip._gst_columns(table[hi])

    items = []
    for row in table[hi + 1:]:
        desc = ip._clean(row[cols["description"]])
        if not desc or desc.upper() == "TOTAL":
            continue
        item = {"description": desc}
        item["batch_no"] = ip._clean(row[cols["batch_no"]])
        item["expiry"] = ip._clean(row[cols["expiry"]])
        item["quantity"] = ip._num(row[cols["quantity"]])
        item["rate"] = ip._num(row[cols["rate"]])
        item["amount"] = ip._num(row[cols["amount"]])
        item["gst"] = sum(float(ip._num(row[g])) for g in gst_cols if ip._num(row[g]))
        items.append(item)

    assert len(items) == 2  # TOTAL row skipped
    assert items[0] == {
        "description": "ESPRA 40 TAB", "batch_no": "TB-022501", "expiry": "01/2027",
        "quantity": "25", "rate": "77.79", "amount": "1750.25", "gst": 12.0,
    }
    assert items[1]["description"] == "IMOL PLUS TAB"
    assert items[1]["quantity"] == "240"


def test_header_meta_extraction():
    text = ("Zydus Healthcare Limited TAX INVOICE\nOriginal\n"
            "GSTIN: 27AAACG1895Q1ZY\nInvoice No: 2299707688 Dt: 30.06.2025\n")
    meta = ip._extract_header_meta(text)
    assert meta["supplier"]["name"]["value"] == "Zydus Healthcare Limited"
    assert meta["invoice"]["invoice_no"]["value"] == "2299707688"
    assert meta["supplier"]["gstin"]["value"] == "27AAACG1895Q1ZY"
