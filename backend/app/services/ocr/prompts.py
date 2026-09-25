"""Prompt text for classification and per-type extraction."""

CLASSIFY_PROMPT = (
    "You are a medical document classifier for a pharmacy. Look at the document and "
    "decide whether it is a doctor's PRESCRIPTION (patient + medicines prescribed) or a "
    "supplier PURCHASE INVOICE / bill (line items with quantities, batch, expiry, price). "
    "Respond with a single lowercase word: 'prescription' or 'invoice'."
)

PRESCRIPTION_PROMPT = (
    "You are an expert medical OCR assistant for a pharmacy. Extract information from this "
    "prescription. For every field return an object {\"value\": <string or null>, "
    "\"confidence\": <0..1>}. Use null when a field is absent or illegible. Do not guess; "
    "reflect your true confidence. Extract: patient (name, age, gender), prescriber (name, "
    "registration_no), and every medication (name, strength e.g. '500 mg', form e.g. "
    "'tablet', frequency e.g. '1-0-1' or 'TDS', duration e.g. '5 days', instructions e.g. "
    "'after food')."
)

INVOICE_PROMPT = (
    "You are an expert OCR assistant for a pharmacy processing an Indian supplier purchase "
    "invoice. For every field return an object {\"value\": <string or null>, "
    "\"confidence\": <0..1>}. Use null when absent/illegible; do not guess.\n"
    "\n"
    "Extract: supplier (name, gstin, address); invoice (invoice_no, invoice_date, "
    "total_amount - the final payable amount printed at the foot of the bill); and every "
    "line item.\n"
    "\n"
    "IMPORTANT - a pharmacy invoice prints SEVERAL different prices per line and they are "
    "NOT interchangeable. Read each into its own field, copying the number from the column "
    "with that exact heading, and leave a field null when that column is absent:\n"
    "  mrp  = the MRP / Maximum Retail Price column\n"
    "  ptr  = the PTR / Price To Retailer column\n"
    "  pts  = the PTS / Price To Stockist column\n"
    "  rate = the RATE / Bill Rate / Net Rate column, i.e. the rate the line was actually "
    "billed at. If the invoice has no such column, copy PTR here (or PTS when there is no "
    "PTR).\n"
    "  rate_source = the literal column heading you took `rate` from, e.g. \"RATE\", "
    "\"PTR\" or \"PTS\". Set its confidence to null.\n"
    "\n"
    "Also per line item: description (the medicine name), pack, batch_no, expiry, quantity "
    "(the billed quantity ONLY - never the free/scheme quantity), free_quantity (the Free / "
    "Scheme / Bonus qty column), discount_percent, amount (the line's net value), hsn, "
    "gst_percent.\n"
    "\n"
    "A GST tax invoice is often printed more than once inside the same file (Original for "
    "Recipient, Duplicate for Transporter, Triplicate for Supplier). Those are copies of ONE "
    "invoice: extract each line item ONCE, from the first copy only. Never repeat a line "
    "because it also appears on another copy."
)

EXTRACTION_PROMPT = {
    "prescription": PRESCRIPTION_PROMPT,
    "invoice": INVOICE_PROMPT,
}
