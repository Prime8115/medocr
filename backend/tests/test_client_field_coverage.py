"""Every field the OCR fills must be visible in the app.

"Some values are not getting fetched" turned out, twice, to mean a field that
WAS fetched, checked and exported - and then never rendered. `free_supply` was
read from the invoice, flagged by the integrity checks and written to the CSV,
while the review screen had no row for it. From the shop floor that is
indistinguishable from not reading it at all.

Nothing in the clients knows the backend schema, so adding a field to
`extraction.py` cannot make the apps show it. This test is that missing link:
add a field and it fails until both clients render it.
"""
import re
from pathlib import Path

import pytest

from app.schemas.extraction import InvoiceLineItem, InvoiceMeta, Party, Supplier

_REPO = Path(__file__).resolve().parents[2]
_CLIENTS = (
    _REPO / "mobile" / "src" / "lib" / "payload.ts",
    _REPO / "web" / "src" / "lib" / "payload.ts",
)

# Not invoice data: `extras` is the catch-all of columns we never promoted (it
# is rendered as label/value rows, not as a named field), and `rate_source`
# records WHICH column the rate came from - it titles the Rate row rather than
# being a row itself.
_NOT_FIELDS = {"extras", "rate_source"}

_SECTIONS = (
    (InvoiceLineItem, "line_items"),
    (InvoiceMeta, "invoice"),
    (Supplier, "supplier"),
    (Party, "bill_to"),
    (Party, "ship_to"),
)


def _is_rendered(source: str, prefix: str, name: str) -> bool:
    """Whether the client renders this field, in either spelling it uses.

    Header fields are written as full paths (`'invoice.invoice_no'`); line
    fields go through a helper that prepends the index (`p('batch_no', ...)`).
    """
    if f"{prefix}.{name}" in source:
        return True
    return prefix == "line_items" and re.search(rf"p\('{re.escape(name)}'", source) is not None


@pytest.mark.parametrize("client", _CLIENTS, ids=lambda p: p.parts[-4])
def test_the_client_renders_every_extracted_field(client: Path) -> None:
    if not client.exists():
        # The backend's own Docker build context carries no client source.
        pytest.skip(f"{client} is not in this checkout")
    source = client.read_text(encoding="utf-8")
    missing = [
        f"{prefix}.{name}"
        for model, prefix in _SECTIONS
        for name in model.model_fields
        if name not in _NOT_FIELDS and not _is_rendered(source, prefix, name)
    ]
    assert not missing, (
        f"{client.name} never shows: {', '.join(missing)}. "
        "A field the OCR fills but the app hides reads as a value we failed to fetch."
    )
