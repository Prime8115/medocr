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
    "You are an expert OCR assistant for a pharmacy processing a supplier purchase invoice. "
    "For every field return an object {\"value\": <string or null>, \"confidence\": <0..1>}. "
    "Use null when absent/illegible; do not guess. Extract: supplier (name, gstin, address), "
    "invoice (invoice_no, invoice_date, total_amount), and every line item (description/medicine "
    "name, batch_no, expiry, quantity, mrp, rate, amount, hsn, gst_percent)."
)

EXTRACTION_PROMPT = {
    "prescription": PRESCRIPTION_PROMPT,
    "invoice": INVOICE_PROMPT,
}
