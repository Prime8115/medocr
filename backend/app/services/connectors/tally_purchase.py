"""Conservative, opt-in Tally purchase voucher export.

The legacy tally_xml format is unchanged. This path requires explicit master
names and balanced figures before an approved invoice can become a voucher.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


_NUMBER = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _value(field) -> str:
    value = field.get("value") if isinstance(field, dict) else field
    return "" if value is None else str(value).strip()


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _decimal(value: str) -> Decimal | None:
    if not _NUMBER.fullmatch(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _date(value: str) -> str | None:
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def assess(payload: dict, config: dict) -> dict:
    """Return explicit blockers; never infer quantity from a pack like 15's."""
    issues: list[dict] = []

    def block(code: str, path: str, message: str):
        issues.append({"code": code, "path": path, "message": message})

    if payload.get("doc_type") != "invoice":
        block("document_type", "doc_type", "Only purchase invoices can become Tally purchase vouchers.")
    kind = (payload.get("meta") or {}).get("document_kind")
    if kind and kind != "invoice":
        block("document_kind", "meta.document_kind", "Credit notes and other documents need a separate voucher flow.")
    data = payload.get("data") or {}
    header = data.get("invoice") or {}
    supplier = data.get("supplier") or {}
    invoice_no = _value(header.get("invoice_no"))
    if not invoice_no:
        block("invoice_no", "invoice.invoice_no", "Confirm the supplier invoice number.")
    if not _date(_value(header.get("invoice_date"))):
        block("invoice_date", "invoice.invoice_date", "Confirm the invoice date (DD/MM/YYYY).")
    supplier_name = _value(supplier.get("name"))
    if not supplier_name:
        block("supplier", "supplier.name", "Confirm the supplier name.")

    conf = config.get("tally") or {}
    party_ledgers = conf.get("party_ledgers") or {}
    party_ledger = (party_ledgers.get(supplier_name) if isinstance(party_ledgers, dict) else None) or conf.get("party_ledger")
    if not str(party_ledger or "").strip():
        block("mapping", "tally.party_ledgers", f"Map {supplier_name or 'this supplier'} to its Tally ledger.")
    for key, label in (("company", "Tally company"),
                       ("purchase_ledger", "purchase ledger"), ("unit", "stock unit")):
        if not str(conf.get(key) or "").strip():
            block("mapping", f"tally.{key}", f"Choose the {label} used in Tally.")
    items = data.get("line_items") or []
    if not items:
        block("line_items", "line_items", "There are no purchase lines to import.")
    mapped = conf.get("stock_items") or {}
    batch_expiries = conf.get("batch_expiries") or {}
    line_total = Decimal(0)
    for n, item in enumerate(items, 1):
        prefix = f"line_items.{n - 1}"
        name = _value(item.get("description"))
        if not name:
            block("description", f"{prefix}.description", f"Line {n}: confirm the medicine name.")
        elif not isinstance(mapped, dict) or not str(mapped.get(name) or "").strip():
            block("stock_item_mapping", f"{prefix}.description", f"Line {n}: map {name} to a Tally stock item.")
        for field, label, positive in (("quantity", "billed quantity", True),
                                       ("free_quantity", "free quantity", False),
                                       ("rate", "purchase rate", False),
                                       ("amount", "line amount", False)):
            raw = _value(item.get(field))
            if not raw and field == "free_quantity":
                continue
            number = _decimal(raw)
            if number is None or (positive and number <= 0):
                block("invalid_number", f"{prefix}.{field}",
                      f"Line {n}: confirm the numeric {label}; pack text such as 15's is not a quantity.")
            elif field == "amount":
                line_total += number
        batch_no = _value(item.get("batch_no"))
        expiry = _value(item.get("expiry"))
        if not batch_no:
            block("batch", f"{prefix}.batch_no", f"Line {n}: confirm the batch number.")
        if not expiry:
            block("expiry", f"{prefix}.expiry", f"Line {n}: confirm the expiry date.")
        elif name and batch_no and isinstance(mapped, dict):
            stock_name = mapped.get(name)
            key = f"{stock_name}|{batch_no}"
            if not stock_name or not isinstance(batch_expiries, dict) or batch_expiries.get(key) != expiry:
                block("batch_expiry_mapping", f"{prefix}.expiry",
                      f"Line {n}: verify batch {batch_no} and expiry {expiry} in the Tally stock master.")

    total = _decimal(_value(header.get("total_amount")))
    if total is None or total <= 0:
        block("total", "invoice.total_amount", "Confirm the invoice grand total.")
    taxes = Decimal(0)
    for field, ledger in (("total_cgst_amount", "cgst_ledger"),
                          ("total_sgst_amount", "sgst_ledger"),
                          ("total_igst_amount", "igst_ledger"),
                          ("total_utgst_amount", "utgst_ledger")):
        raw = _value(header.get(field))
        if raw:
            amount = _decimal(raw)
            if amount is None:
                block("tax", f"invoice.{field}", f"Confirm the numeric {field.replace('_', ' ')}.")
            elif amount:
                taxes += amount
                if not str(conf.get(ledger) or "").strip():
                    block("mapping", f"tally.{ledger}", f"Choose the {ledger.replace('_', ' ')} in Tally.")
    if total is not None and abs(line_total + taxes - total) > Decimal("0.005"):
        block("reconciliation", "invoice.total_amount",
              "Line amounts plus tax do not match the grand total; review discounts and rounding.")
    return {"ready": not issues, "issues": issues, "line_count": len(items),
            "line_total": _money(line_total), "tax_total": _money(taxes),
            "invoice_total": _money(total) if total is not None else None}


def render(payload: dict, config: dict) -> str:
    """Generate a Tally purchase invoice only after the strict preflight."""
    check = assess(payload, config)
    if not check["ready"]:
        raise ValueError("Tally purchase preflight has unresolved blockers.")
    data, conf = payload["data"], config["tally"]
    invoice = data["invoice"]
    total = _decimal(_value(invoice["total_amount"]))
    root = ET.Element("ENVELOPE")
    header = ET.SubElement(root, "HEADER")
    ET.SubElement(header, "TALLYREQUEST").text = "Import Data"
    body = ET.SubElement(root, "BODY")
    imported = ET.SubElement(body, "IMPORTDATA")
    desc = ET.SubElement(imported, "REQUESTDESC")
    ET.SubElement(desc, "REPORTNAME").text = "Vouchers"
    variables = ET.SubElement(desc, "STATICVARIABLES")
    ET.SubElement(variables, "SVCURRENTCOMPANY").text = conf["company"]
    message = ET.SubElement(ET.SubElement(imported, "REQUESTDATA"), "TALLYMESSAGE")
    voucher = ET.SubElement(message, "VOUCHER", VCHTYPE="Purchase", ACTION="Create", OBJVIEW="Invoice Voucher View")

    def tag(parent, name, value):
        ET.SubElement(parent, name).text = str(value)

    tag(voucher, "DATE", _date(_value(invoice["invoice_date"])))
    tag(voucher, "VOUCHERTYPENAME", "Purchase")
    tag(voucher, "PERSISTEDVIEW", "Invoice Voucher View")
    tag(voucher, "ISINVOICE", "Yes")
    supplier_name = _value(data["supplier"]["name"])
    party_ledger = (conf.get("party_ledgers") or {}).get(supplier_name) or conf.get("party_ledger")
    tag(voucher, "PARTYLEDGERNAME", party_ledger)
    tag(voucher, "REFERENCE", _value(invoice["invoice_no"]))
    tag(voucher, "NARRATION", f"MediScan reviewed invoice {_value(invoice['invoice_no'])}; document {payload.get('document_id', '')}")
    party = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    tag(party, "LEDGERNAME", party_ledger)
    tag(party, "ISDEEMEDPOSITIVE", "No")
    tag(party, "ISPARTYLEDGER", "Yes")
    tag(party, "AMOUNT", _money(total))
    bill = ET.SubElement(party, "BILLALLOCATIONS.LIST")
    tag(bill, "NAME", _value(invoice["invoice_no"]))
    tag(bill, "BILLTYPE", "New Ref")
    tag(bill, "AMOUNT", _money(total))
    unit = conf["unit"]
    for item in data["line_items"]:
        quantity = _decimal(_value(item["quantity"]))
        free = _decimal(_value(item.get("free_quantity"))) or Decimal(0)
        amount = _decimal(_value(item["amount"]))
        name = _value(item["description"])
        entry = ET.SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
        tag(entry, "STOCKITEMNAME", conf["stock_items"][name])
        tag(entry, "ISDEEMEDPOSITIVE", "Yes")
        tag(entry, "ACTUALQTY", f"{quantity + free} {unit}")
        tag(entry, "BILLEDQTY", f"{quantity} {unit}")
        tag(entry, "RATE", f"{_value(item['rate'])}/{unit}")
        tag(entry, "AMOUNT", "-" + _money(amount))
        batch = ET.SubElement(entry, "BATCHALLOCATIONS.LIST")
        godown = conf.get("godown") or "Main Location"
        tag(batch, "GODOWNNAME", godown)
        tag(batch, "DESTINATIONGODOWNNAME", godown)
        tag(batch, "BATCHNAME", _value(item["batch_no"]))
        tag(batch, "AMOUNT", "-" + _money(amount))
        tag(batch, "ACTUALQTY", f"{quantity + free} {unit}")
        tag(batch, "BILLEDQTY", f"{quantity} {unit}")
        allocation = ET.SubElement(entry, "ACCOUNTINGALLOCATIONS.LIST")
        tag(allocation, "LEDGERNAME", conf["purchase_ledger"])
        tag(allocation, "ISDEEMEDPOSITIVE", "Yes")
        tag(allocation, "AMOUNT", "-" + _money(amount))
    for field, ledger in (("total_cgst_amount", "cgst_ledger"),
                          ("total_sgst_amount", "sgst_ledger"),
                          ("total_igst_amount", "igst_ledger"),
                          ("total_utgst_amount", "utgst_ledger")):
        amount = _decimal(_value(invoice.get(field)))
        if amount:
            entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            tag(entry, "LEDGERNAME", conf[ledger])
            tag(entry, "ISDEEMEDPOSITIVE", "Yes")
            tag(entry, "AMOUNT", "-" + _money(amount))
    return ET.tostring(root, encoding="unicode")
