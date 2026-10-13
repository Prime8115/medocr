"""The field dictionary: one standard format for every supplier's invoice.

Every bill is read into the same fields (app/schemas/extraction.py) whatever
the supplier calls them. This file holds, for each field, the wordings
suppliers print it under - "Qty", "Qty.Sale", "Billed Qty" and "Quantity" are
all `quantity` - and the words that must never be taken for it ("Dist.Price"
is never the PTS, "Discount Rate" never the billed rate). Wording alone does
not decide a value: the readers also check its shape (a date, an HSN, a
GSTIN's check digit) and the bill's own arithmetic, and a supplier's own
headings are learned from reviewers' corrections (services/supplier_profile.py).

A new wording is added here, with the bill it came from added to the private
invoice corpus, and the corpus gate run - so no wording can break another
supplier's bill.

    LINE_COLUMNS      line-item columns: field -> (wordings in preference order,
                      words that rule a heading out)
    REFERENCE_LABELS  header references (IRN, e-way bill, LR, transport, PO)
    DATE_LABELS       header dates (due, LR, PO)
    TOTAL_LABELS      bill totals (taxable, GST and each head, discount)
"""
from typing import Dict, List, Tuple


# field -> (keywords in PREFERENCE order, exclude keywords). Header cells are
# normalized to lowercase with spaces/newlines/dots removed before matching.
# Order of this dict matters: earlier fields claim their column first.
LINE_COLUMNS: Dict[str, Tuple[List[str], List[str]]] = {
    "description": (
        ["productname", "itemname", "description", "proddesc", "particulars", "product",
         "desc", "item", "goods", "medicine"],
        ["hsn", "code", "qty", "rate", "amount"],
    ),
    "hsn": (["hsncode", "hsn"], []),
    "product_code": (["productcode", "prdcode", "itemcode", "prodcode", "code"], ["hsn"]),
    # ...and the short "MFR." / "MFGR" headings (NSV, Troikaa, Parshva).
    "manufacturer": (["mfgname", "manufacturername", "manufacturer", "mfgco", "company", "mfgr", "mfr"],
                     ["code", "date", "cd"]),
    # Excludes "exp": Bharat heads one column "Exp.date / Mfg.date", and the
    # expiry is the field a pharmacist actually needs, so it claims that column.
    # Excludes "batch" too: Menarini stacks "Batch No" over "Mfg.Date" in ONE
    # column, where the record line carries the BATCH and the wrapped line below
    # carries the date. Letting mfg_date claim it left every batch number blank.
    "mfg_date": (["mfgdate", "mfgdt", "manufacturingdate", "mfd"], ["exp", "batch"]),
    # Not "Billed Qty / UoM" (Piramal): the quantity's column, its unit beside it.
    # "Unit & UQC" (Troikaa): the GST unit quantity code is the unit.
    "uom": (["uom", "unitofmeasure", "uqc"], ["qty"]),
    "batch_no": (["batchno", "batch", "lotno", "lot", "bno", "btno"], ["bill", "sr", "inv"]),
    # No "mfg" exclusion: a Mfg-only column never contains "exp", while Bharat
    # heads one column "Exp.date / Mfg.date" - excluding it lost every expiry.
    "expiry": (["expdate", "expiry", "exp"], []),
    # "PKG DETAIL" (Himalaya): "1 X 60".
    "pack": (["packing", "pack", "pkgdetail", "pkg"], []),
    # Free/scheme quantity is claimed BEFORE quantity so a "F.QTY" column can
    # never be mistaken for the billed quantity.
    # "Fr. Qty." (Kanchan) normalises to "frqty", which matched nothing before.
    # "D.Qty" (IPCA): the deal quantity - goods given free on the scheme.
    # Not "Bonus Disc." (Medley): a discount rate, not free goods.
    "free_quantity": (["freeqty", "frqty", "schemeqty", "dealqty", "free", "fqty", "scheme", "bonus"],
                      ["%", "value", "amount", "disc"]),
    # "Billed Qty." before a bare "qty": Cipla prints "Loose Qty" (mostly empty)
    # first, and its billed quantity is the one the stock and the amount follow.
    "quantity": (["quantity", "billedqty", "billqty", "qty", "nos", "units"], ["free", "fqty", "scheme", "bonus", "%"]),
    "mrp": (["mrp"], ["%"]),
    # Lupin heads "Trade Price/Unit | TP/ PTR Value": the price, then the
    # line's value at it - never the PTR.
    "ptr": (["pricetoretailer", "retailerprice", "ptr", "tradeprice/unit"], ["%", "value", "amount"]),
    # Not a "Dist. Price" (Abbott): the distributor's price is not the PTS, and
    # a bill that prints no PTS leaves it blank. It is kept under its own name.
    "pts": (["pricetostockist", "stockistprice", "pts"], ["%"]),
    # An explicit billed-rate column. "NIR" (Net Invoice Rate) is tried first
    # because Bharat prints it beside a bare CGST "Rate" column that would
    # otherwise win. GST rate columns are excluded outright.
    "rate": (
        ["nir", "netinvoicerate", "billrate", "netrate", "purchaserate", "salerate",
         "unitprice", "prate", "rate"],
        # ...and never a "Discount Rate" (Cipla Health): the discount's percentage.
        ["%", "mrp", "ptr", "pts", "cgst", "sgst", "igst", "gst", "tax", "disc"],
    ),
    # Bare "DIS" (Marg): the line's discount rate.
    # Never a price: "Dist.Price" (Abbott) starts with "dis" too.
    "discount_percent": (["disc%", "discount%", "discount", "disc", "dis"],
                         ["amt", "amount", "value", "rs", "price"]),
    # "Spl.Dis. Amount" (Ajanta) - "Dis." as well as "Disc.".
    "discount_amount": (["discamt", "discountamt", "discountamount", "discamount", "disamount", "disamt"], ["%"]),
    "cd_percent": (["cd%", "cashdisc%", "cashdiscount%"], ["amt", "amount", "rs"]),
    "cd_amount": (["cdamt", "cdamount", "cashdiscamt", "cashdiscountamt"], ["%"]),
    "wp_percent": (["wp%", "wpdisc%"], ["amt", "amount"]),
    "wp_amount": (["wpamt", "wpamount", "wpvalue"], ["%"]),
    "scheme": (["schemedesc", "schemedescription", "schemename", "scheme"], ["%", "qty", "amt", "value"]),
    "scheme_value": (["schemevalue", "schemeamt", "schemeamount"], ["%"]),
    # "Sale Value" (Overseas) is the line BEFORE its discount - a gross figure.
    # Left to `amount`, it overstated every line by the discount.
    # ...and Dr. Reddy's "Total Amount (base price)", before its discount and tax.
    "gross_amount": (["grossamount", "grossamt", "grossvalue", "grosstotal", "totalbasic", "gross",
                      "salevalue", "baseprice", "basevalue", "ptrvalue"], ["%"]),
    # "Total Amount" (Menarini) is the line INCLUDING tax - a net amount, not
    # the taxable one. Excluded from anything taxable so the column the bill
    # actually sums still goes to `amount`.
    "net_amount": (["netamount", "netamt", "netvalue", "totalamount"], ["%", "taxable"]),
    "utgst_percent": (["utgst%", "utgstrate"], ["amt", "amount"]),
    "utgst_amount": (["utgstamt", "utgstamount"], ["%", "rate"]),
    "scheme_percent": (["sch%", "scheme%", "schemediscount"], ["amt", "qty"]),
    "total_quantity": (["totalqty", "totqty", "netqty"], ["%", "free"]),
    # Tax columns are captured per head so the amounts reach the accounts, and so
    # a zero-rated line can be told apart from an unreadable one.
    "cgst_percent": (["cgst%", "cgstrate"], ["amt", "amount", "rs/"]),
    "cgst_amount": (["cgstamt", "cgstamount"], ["%", "rate"]),
    # Not "SGST % Rs. / %" (Piramal): the tax and its rate in one cell, split
    # like every merged tax cell (_gst_columns).
    "sgst_percent": (["sgst%", "sgstrate", "utgst%"], ["amt", "amount", "rs/"]),
    "sgst_amount": (["sgstamt", "sgstamount", "utgstamt"], ["%", "rate"]),
    "igst_percent": (["igst%", "igstrate"], ["amt", "amount", "rs/"]),
    "igst_amount": (["igstamt", "igstamount"], ["%", "rate"]),
    # The net/taxable column is what the bill actually sums; a plain "Amount"
    # column (Kanchan) is the figure BEFORE the line discount.
    # The taxable value - what GST is charged on, and what the lines are summed
    # against. `net_amount` and `gross_amount` are separate columns above, so
    # they are no longer allowed to stand in for this one.
    "amount": (
        # Bare "TAXABLE" (the Marg-style ERP layout: "AMOUNT | DISC | TAXABLE")
        # names the taxable value; its "AMOUNT" is the figure before discount.
        ["taxableamount", "taxablevalue", "taxableamt", "taxable", "assessablevalue", "assessable",
         "amount", "value", "total"],
        # A tax column is not the line's own amount, and neither is a discount
        # column. Overseas prints "Disc Value" and "CGST Amount" beside its
        # taxable "Trans. Value"; without these, the sum of the invoice was the
        # sum of its discounts.
        ["%", "gross", "net", "disc", "disamount", "disamt", "cgst", "sgst", "igst", "utgst"],
    ),
}


REFERENCE_LABELS: Dict[str, List[str]] = {
    # "IRN No.: DDE3F8..." (Bharat), "IRN No. af84ef..." (Overseas): the "No."
    # stopped a bare "IRN" label reaching the value.
    "irn": ["IRN No", "IRN Number", "IRN"],
    "eway_bill_no": ["E Way Bill No", "EwayBill No", "E Way Bill", "Eway Bill"],
    # The number before its date or vehicle: "LR No / Dt : 49853" (Cipla Health),
    # "L.R.No/Date/: 772463" (Micropark), "LR No./Vehicle No. : 2069083"
    # (Zuventus); "L.R.Number : LOCAL" (Raptakos, Sanofi).
    "lr_no": ["LR No Dt", "LR No Date", "L R No Date", "LR No Vehicle No", "LR Number", "L R Number", "LR No", "L R No", "LR RR No",
               "LR/RR No", "Lorry Receipt No"],
    # A transporter's name first; the mode only where no name is printed -
    # Overseas states just "Transportation Mode : BY HAND DELIVERY".
    # "Transporter Name : SIDDHI ROADWAYS" (Dr. Reddy's, Parekh, Sanofi), "Dispatch
    # Thru : HEERAL LOGISTICS" (Ajanta), "DESP. THRU:" (Blue Cross), "Transported
    # by :" (Lupin), "Carrier Name:" (Cipla), "Trpt :" (Graciera), "Tpt-"
    # (Micropark), "Through:" (Piramal).
    "transport": ["Transporter Name", "Transport Name", "Transporter", "Name of Carrier", "Carrier Name",
                  "Carrier", "Transportation Mode", "Transport Mode", "Mode of Transport", "Transportation",
                  "Transported by", "Dispatch Thru", "Desp Thru", "Transport", "Trpt", "Tpt", "Through"],
    # The shop's own order reference before the supplier's order number: "Customer
    # PO : 347" (Eris), "Cust. PO Ref. :" (Lupin), "Cust. Order Ref :5105" (Mylan,
    # Piramal), "Party Order No. : 4779" (Medley), "Order Reference No.: MAIL
    # ORDER" (Cipla Health), "PO No / Ref no: WA" (Dr. Krich), "Order Ref. & Date
    # 5336" (Centaur) - then the order number itself, "Order number & Date:"
    # (Sun).
    "po_no": ["Customer PO", "CustomerPO", "Cust PO Ref", "Cust Order Ref", "Customer Ord No",
               "Party Order No", "Order Reference No", "PO No Ref no", "Order Ref Date", "PO No", "P O No",
               "Order No", "Purchase Order No", "Ord Ref No", "Order number Date"],
}


DATE_LABELS: Dict[str, List[str]] = {
    # ...and the Marg "DUE DT 20-09-2025", Coxswain's "Payment Day :", Parshva's "Due :".
    "due_date": ["Due Date", "Payment Due", "Due On", "Due Dt", "Payment Day", "Due"],
    # Kanchan prints "LR/RR No. : 2035873 Date : 11/07/2025" - the number sits
    # between the label and the date, so a plain "LR Date" never matches.
    # Menarini prints "L.R. No. : LOCAL Date : 22 Sep 25".
    "lr_date": ["LR Date", "L R Date", "LR No Dt", "LR Dt", "LR RR No", "LR/RR No",
                "LR No", "L R No", "L.R. Date", "L.R. No", "LR/RR Date"],
    "po_date": ["PO Date", "P O Date", "Ord Ref Date", "Order Date", "Order No",
                "PO No", "P O No", "Cust Ord Rec Dt", "Cust Ord Dt", "Cust.Ord.Rec.Dt", "Cust Ord No"],
}


TOTAL_LABELS: Dict[str, List[str]] = {
    "total_gst_amount": ["Total GST Amount", "Total GST Amt", "Total GST", "GST Total",
                         "Total Tax Amount", "Total Tax"],
    "total_taxable_amount": ["Total Taxable Value", "Total Taxable Amount", "Total Taxable", "Total Amount Before Tax",
                             "Taxable Value Total", "Basic Amount", "Total Basic"],
    "total_discount_amount": ["Total Discount Amount", "Total Discount Amt", "Total Discount", "Discount Total", "SD Discount",
                              "Total Disc Amt", "Total Disc", "Less Discount", "Less Special Disc", "Less Scheme Disc", "Less Disc",
                              # The Marg summary's "DISC AMOUNT 1374.28" / "DIS AMT. 111.08" (Aurowin,
                              # Ceptor, Gobinda); "Trade Discount 14,193.24" (Prabodhan); "Less :
                              # Trade Disc. -15947.15" (Lupin).
                              "Disc Amount", "Dis Amt", "Trade Discount", "Less Trade Disc"],
    "total_cgst_amount": ["Total CGST Amt", "Total CGST Amount", "Total CGST", "CGST Total"],
    "total_sgst_amount": ["Total SGST Amt", "Total SGST Amount", "Total SGST", "SGST Total",
                          "Total UTGST Amt"],
    "total_igst_amount": ["Total IGST Amt", "Total IGST Amount", "Total IGST", "IGST Total"],
    "total_utgst_amount": ["Total UTGST Amount", "Total UTGST", "UTGST Total"],
}
