"""Deterministic invoice table parser — column mapping and row extraction."""
from app.services.ocr import invoice_parser as ip


# A synthetic distributor-invoice table (list of rows, cells may be None).
# This is the real Zydus column layout: it has MRP and PTR but NO "Rate" column.
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

# A layout that carries BOTH a PTR column and an explicit RATE column, with PTR
# printed first. The billed rate must come from RATE, not from whichever price
# column happens to sit furthest left.
HEADER_WITH_RATE = ["PRODUCT NAME", "BATCH", "EXP", "MRP", "PTR", "RATE", "QTY", "AMOUNT"]
ROW_WITH_RATE = ["CALPOL 650", "C-991", "05/2027", "30.50", "21.35", "19.80", "10", "198.00"]

# A layout whose only price-to-trade column is PTS.
HEADER_PTS = ["ITEM NAME", "BATCH", "EXP", "MRP", "PTS", "QTY", "AMOUNT"]
ROW_PTS = ["AZEE 500", "AZ-77", "11/2027", "130.00", "98.40", "6", "590.40"]


def test_find_header_row():
    table = [["Zydus TAX INVOICE"] + [None] * 16, HEADER, ROW1]
    assert ip._find_header_row(table) == 1


def test_map_columns():
    cols = ip._map_columns(HEADER)
    assert cols["description"] == 2
    assert cols["hsn"] == 4
    assert cols["pack"] == 5
    assert cols["batch_no"] == 6
    assert cols["expiry"] == 8          # exp, not mfg date (7)
    assert cols["mrp"] == 9
    assert cols["ptr"] == 10            # PTR, not PTR% (11)
    assert cols["quantity"] == 14       # QTY, not FREE QTY (15)
    assert cols["free_quantity"] == 15
    assert cols["amount"] == 16
    # This invoice has no billed-rate column at all; `rate` must not be invented
    # by grabbing PTR at mapping time. The fallback happens per row, labelled.
    assert "rate" not in cols


def test_explicit_rate_column_wins_over_ptr():
    """The client's complaint: 'rate field available but displays PTR'."""
    cols = ip._map_columns(HEADER_WITH_RATE)
    assert cols["mrp"] == 3
    assert cols["ptr"] == 4
    assert cols["rate"] == 5            # RATE, even though PTR is printed first

    item = ip._build_item(ROW_WITH_RATE, cols, HEADER_WITH_RATE, [])
    assert item["rate"]["value"] == "19.80"
    assert item["ptr"]["value"] == "21.35"
    assert item["mrp"]["value"] == "30.50"
    assert item["rate_source"]["value"] == "RATE"


def test_rate_falls_back_to_ptr_and_says_so():
    cols = ip._map_columns(HEADER)
    item = ip._build_item(ROW1, cols, HEADER, ip._gst_columns(HEADER))
    assert item["ptr"]["value"] == "77.79"
    assert item["rate"]["value"] == "77.79"     # still populated for connectors
    assert item["rate_source"]["value"] == "PTR"  # ...but honest about the source
    # A label, not a measurement: it must not dilute overall confidence.
    assert item["rate_source"]["confidence"] is None


def test_rate_falls_back_to_pts_when_that_is_all_there_is():
    cols = ip._map_columns(HEADER_PTS)
    assert cols["pts"] == 4
    item = ip._build_item(ROW_PTS, cols, HEADER_PTS, [])
    assert item["pts"]["value"] == "98.40"
    assert item["rate"]["value"] == "98.40"
    assert item["rate_source"]["value"] == "PTS"


def test_free_quantity_is_not_billed_quantity():
    cols = ip._map_columns(HEADER)
    item = ip._build_item(ROW1, cols, HEADER, ip._gst_columns(HEADER))
    assert item["quantity"]["value"] == "25"
    assert item["free_quantity"]["value"] == "5"


def test_gst_columns_sum():
    gst = ip._gst_columns(HEADER)
    assert gst == [12, 13]              # CGST% + SGST%


def test_num_cleaning():
    assert ip._num("1,750.25") == "1750.25"
    assert ip._num("qty 25 nos") == "25"
    assert ip._num("abc") is None


def test_build_item_skips_summary_rows():
    cols = ip._map_columns(HEADER)
    assert ip._build_item(TOTAL, cols, HEADER, []) is None


def test_parse_rows_via_mapping():
    # Exercise the row-building the same way parse_invoice_pdf does, without a PDF.
    table = [HEADER, ROW1, ROW2, TOTAL]
    hi = ip._find_header_row(table)
    assert hi == 0
    cols = ip._map_columns(table[hi])
    gst_cols = ip._gst_columns(table[hi])

    items = [i for i in (ip._build_item(r, cols, table[hi], gst_cols) for r in table[hi + 1:]) if i]

    assert len(items) == 2  # TOTAL row skipped
    assert items[0]["description"]["value"] == "ESPRA 40 TAB"
    assert items[0]["batch_no"]["value"] == "TB-022501"
    assert items[0]["expiry"]["value"] == "01/2027"
    assert items[0]["quantity"]["value"] == "25"
    assert items[0]["amount"]["value"] == "1750.25"
    assert items[0]["gst_percent"]["value"] == "12.0"
    assert items[1]["description"]["value"] == "IMOL PLUS TAB"
    assert items[1]["quantity"]["value"] == "240"


def test_header_meta_extraction():
    text = ("Zydus Healthcare Limited TAX INVOICE\nOriginal\n"
            "GSTIN: 27AAACG1895Q1ZY\nInvoice No: 2299707688 Dt: 30.06.2025\n")
    meta = ip._extract_header_meta(text)
    assert meta["supplier"]["name"]["value"] == "Zydus Healthcare Limited"
    assert meta["invoice"]["invoice_no"]["value"] == "2299707688"
    assert meta["supplier"]["gstin"]["value"] == "27AAACG1895Q1ZY"


# ------------------------------ invoice totals ------------------------------
def test_extract_total_prefers_the_most_specific_wording():
    text = "Total Amount 1,000.00\nGrand Total : 2,14,530.55\n"
    assert ip._extract_total(text) == "214530.55"


def test_extract_total_handles_rupee_prefix():
    assert ip._extract_total("Net Payable Rs. 9,481.00") == "9481.00"


def test_extract_total_returns_none_when_absent():
    assert ip._extract_total("no totals printed here") is None


def test_extract_stated_item_count():
    assert ip._extract_item_count("Total Items : 143   Total Qty : 2,410") == 143
    assert ip._extract_item_count("No. of Items- 57") == 57
    assert ip._extract_item_count("nothing here") is None


# --------------------------- printed copy detection ---------------------------
def test_copy_groups_splits_original_duplicate_triplicate():
    """The 143-items-shown-as-429 bug: one invoice printed three times."""
    labels = ["original", None, "duplicate", None, "triplicate", None]
    groups = ip._copy_groups(labels)
    assert groups == [[0, 1], [2, 3], [4, 5]]


def test_copy_groups_keeps_continuation_pages_with_their_copy():
    labels = ["original", None, None, "duplicate", None, None]
    assert ip._copy_groups(labels) == [[0, 1, 2], [3, 4, 5]]


def test_copy_groups_single_copy_is_one_group():
    assert ip._copy_groups([None, None, None]) == [[0, 1, 2]]
    assert ip._copy_groups(["original", None]) == [[0, 1]]


def test_copy_label_reads_the_marker():
    assert ip._copy_label("TAX INVOICE\nDuplicate for Transporter") == "duplicate"
    assert ip._copy_label("TAX INVOICE\nTriplicate for Supplier") == "triplicate"
    assert ip._copy_label("TAX INVOICE\nsome pharmacy") is None
