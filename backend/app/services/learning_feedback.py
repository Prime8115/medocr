"""Safe explanations for why a saved correction did or did not teach a rule."""
from app.services.ocr.learned_labels import LEARNABLE
from app.services.supplier_labels import _value, changed_paths


def for_edit(old_payload, before, after, labels, columns):
    meta = (old_payload or {}).get("meta") or {}
    supplier = _value(after, "supplier.gstin")
    page = meta.get("page_text") or ""
    original = meta.get("original_fields") or before
    old_rows = (original or {}).get("line_items") or []
    new_rows = (after or {}).get("line_items") or []
    layouts = {l.get("signature") for l in meta.get("table_layouts") or [] if l.get("signature")}
    owned = {field for choice in meta.get("choices") or [] for field in choice.get("fields") or []}
    out = []
    for path in changed_paths(before, after):
        if path == "line_items":
            status = "learned" if columns else "not_learned"
            if columns:
                reason = "A consistent column mapping was saved for this supplier layout."
            elif len(layouts) != 1:
                reason = "No single reliable table layout was available for a reusable column rule."
            elif len(old_rows) != len(new_rows):
                reason = "Rows were added or removed, so column positions could not be compared safely."
            elif _value(original, "supplier.gstin") != supplier:
                reason = "The supplier identity changed during review; no column rule was made."
            else:
                reason = "The changes did not provide enough matching column evidence for a safe rule."
        elif path in labels:
            status, reason = "learned", "A printed label was saved for this supplier."
        elif path.endswith(".address"):
            status, reason = "not_learned", "An address needs a verified printed block before it can teach a supplier rule."
        elif path in owned:
            status, reason = "not_learned", "This field is handled by a separate choice shown on the invoice."
        elif path not in LEARNABLE:
            status, reason = "not_learned", "This field is not eligible for automatic supplier rules."
        elif not _value(after, path):
            status, reason = "not_learned", "A blank value cannot teach a reusable rule."
        elif len(supplier) != 15:
            status, reason = "not_learned", "The supplier GSTIN must be identified before a rule can be scoped safely."
        elif not page:
            status, reason = "not_learned", "No readable source text was available for a printed-label rule."
        else:
            status, reason = "not_learned", "No single printed label uniquely matched the corrected value."
        out.append({"path": path, "status": status, "reason": reason})
    return out
