"""Who is the supplier and who is the buyer - decided from what the page prints.

A tax invoice names two businesses, each with its GSTIN: the supplier and the
buyer. Suppliers print them every way there is - the buyer under "Bill To",
"Sold to Party", "Payer", "M/s", or under no heading at all (Adroit prints the
pharmacy's block straight after its own) - and a reader keyed on headings files
a purchase under the wrong party: Pfizer's "Bill To: ... GSTIN: 27AAECD..." sits
ABOVE Pfizer's own GSTIN, so the first GSTIN on the page, which we took as the
supplier's, was the pharmacy's.

The GSTINs settle it. Only those whose check character is right are counted -
a valid GSTIN is, beyond reasonable doubt, exactly what is printed. A GSTIN
carries its holder's PAN (characters 3-12), so GSTINs group into businesses by
PAN; a business registered in two states is still one party. Then:

* the buyer is the business printed NEAREST below a buyer label - strictly
  nearer than any other - or, failing a label, the one the reading gave the
  buyer, or the one left when the supplier is known and the page names two;
* the supplier is whichever business is not the buyer, when that leaves one.

Names follow their GSTINs: a party's name is the company printed just above
its own GSTIN. A supplier name that is printed above the BUYER's GSTIN is the
buyer's name (Adroit), and is replaced; the company the bill is signed for
("For ADROIT BIOMED LIMITED") confirms a supplier when its block cannot.

Anything ambiguous - three businesses and no label to tell them apart, a C&F
agent's GSTIN as well - is left exactly as read. Nothing is guessed.
"""
import re
from collections import OrderedDict
from contextvars import ContextVar
from typing import Dict, Iterable, List, Optional, Tuple

from app.services.ocr.invoice_header import gstin_is_valid

# Not \b-anchored: Adroit prints "GSTIN27AASCA3306L1ZE", label and number run
# together. The check character keeps a stray match out.
_GSTIN_ANY = re.compile(r"(?<![0-9])([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z])(?![A-Z0-9])")
_BUYER_LABEL = re.compile(
    r"\b(bill(?:ed)?\s*to|ship(?:p?ed)?\s*to|sold\s*to|buyer|consignee|"
    r"customer(?!\s*(?:care|service|support|copy))|payer|receiver|recipient(?!\s*copy)|"
    r"deliver(?:y|ed)?\s*(?:to|address)|party\s*(?:name|details)|m\s*/\s*s\b)",
    re.I,
)
# The GSTINs of the shop the bill is being read for (services/shop_identity.py),
# set by process_document for the length of one reading. The shop is always
# the buyer on its own purchase bills - when it is known, nothing is guessed.
OWN_GSTINS: ContextVar[tuple] = ContextVar("own_gstins", default=())

# How far below a buyer label its GSTIN may be printed (address lines between).
_LABEL_REACH_LINES = 9
# Held a little below a direct reading: right beyond reasonable doubt, but the
# review screen should still show where it came from.
RESOLVED_CONFIDENCE = 0.9

# A business's name: a run ending in a legal or trade suffix.
_NAME = re.compile(
    r"([A-Z][A-Za-z0-9&.,'()\- ]{2,}?\b(?:PRIVATE\s+LIMITED|PVT\.?\s*LTD\.?|LIMITED|LTD\.?|LLP|"
    r"CHEMISTS?|AGENC(?:Y|IES)|PHARMACY|PHARMA(?:CEUTICALS?)?|LABORATORIES|LIFE\s*SCIENCES?|"
    r"MEDICAL(?:S| STORES?)?|ENTERPRISES?|DISTRIBUTORS?|"
    r"HEALTH\s*CARE(?:\s+(?:PVT\.?\s*LTD\.?|PRIVATE\s+LIMITED))?|TRADERS|& CO\.?))(?![A-Za-z])",
)
_NOT_NAME = re.compile(r"(gstin|gst\s*no|pan\b|d\.?l\.?\s*no|e-?mail|phone|mob|tel\b|fssai|"
                       r"bank|ifsc|a/c|road|marg|nagar|floor|gala|plot|compound|bldg|building)", re.I)
_ADDRESS_LINE = re.compile(r"\b(road|rd\.|marg|nagar|floor|flr|gala|plot|compound|bldg|building|"
                           r"house|complex|estate|park|lane|street|sector|near|opp\.?)\b", re.I)
# Document words a title line runs into the supplier's name: "T AX INVOICEADROIT".
_DOC_PREFIX = re.compile(r"^.*?(?:T\s*AX\s*INVOICE|INVOICE|ORIGINAL(?:\s+FOR\s+\w+)?|DUPLICATE|"
                         r"TRIPLICATE|CREDIT\s*NOTE)\s*", re.I)
_PARTY_PREFIX = re.compile(r"^(?:M\s*/\s*S\.?|TO|BILL(?:ED)?\s*TO|SOLD\s*TO|SHIP\s*TO|BUYER|PAYER|"
                           r"CUSTOMER(?:\s*NAME)?|PARTY\s*NAME)\s*[:\-.]?\s*", re.I)


def _value(fields: dict, party: str, key: str) -> str:
    leaf = (fields.get(party) or {}).get(key)
    return str((leaf or {}).get("value") or "").strip() if isinstance(leaf, dict) else ""


def _key(name: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    a, b = _key(a), _key(b)
    if len(a) < 6 or len(b) < 6:
        return False
    n = min(12, len(a), len(b))
    return a[:n] == b[:n]


def gstins_on_page(text: str) -> List[Tuple[int, str]]:
    """(position, GSTIN) for every valid GSTIN printed, in page order."""
    out = []
    for m in _GSTIN_ANY.finditer((text or "").upper()):
        if gstin_is_valid(m.group(1)):
            out.append((m.start(), m.group(1)))
    return out


def _businesses(found: List[Tuple[int, str]]) -> "OrderedDict[str, List[str]]":
    """GSTINs grouped by the PAN inside them, in order of first appearance."""
    out: "OrderedDict[str, List[str]]" = OrderedDict()
    for _, gstin in found:
        group = out.setdefault(gstin[2:12], [])
        if gstin not in group:
            group.append(gstin)
    return out


def _line_index(text: str):
    breaks = [i for i, ch in enumerate(text or "") if ch == "\n"]

    def line_of(pos: int) -> int:
        lo, hi = 0, len(breaks)
        while lo < hi:
            mid = (lo + hi) // 2
            if breaks[mid] < pos:
                lo = mid + 1
            else:
                hi = mid
        return lo
    return line_of


# "ORIGINAL FOR BUYER" / "Duplicate for Recipient" name the COPY, and "from the
# buyer" is prose - neither heads the buyer's block. Raptakos and NSV print the
# copy marker right above their own GSTIN, which then read as the buyer's.
_NOT_A_HEADING = re.compile(r"\b(?:for|the|to\s+the|of\s+the)\s*$", re.I)


def _buyer_labels(text: str) -> List[int]:
    """Where the buyer labels start. "M/s" counts only on a page with no other
    label: Prabodhan prints "M/S PRABODHAN..." over its OWN block, and its
    "Bill To Party" below it is the label that means the buyer."""
    primary, secondary = [], []
    for m in _BUYER_LABEL.finditer(text or ""):
        line_start = (text or "").rfind("\n", 0, m.start()) + 1
        if _NOT_A_HEADING.search((text or "")[line_start:m.start()]):
            continue
        (secondary if re.match(r"m\s*/\s*s", m.group(1), re.I) else primary).append(m.start())
    return primary or secondary


def _column(text: str, pos: int) -> int:
    return pos - ((text or "").rfind("\n", 0, pos) + 1)


def _nearest_below_label(text: str, found: List[Tuple[int, str]]) -> Optional[str]:
    """The PAN of the business printed nearest below a buyer label, when it is
    strictly nearer than every other business. None otherwise.

    A label printed mid-line heads the column it starts: side-by-side blocks
    interleave line by line in the text ("GSTIN 27AABCM... GOLDEN TOBACCO ...
    / PAN ... GSTIN 27AAECD..."), so a GSTIN to the LEFT of the label's column
    belongs to the block beside it - Mylan's own GSTIN sat a line nearer its
    "Bill To:" than the buyer's did.
    """
    starts = _buyer_labels(text)
    if not starts:
        return None
    line_of = _line_index(text)
    label_lines = [(s, line_of(s), _column(text, s)) for s in starts]
    best: Dict[str, int] = {}
    for pos, gstin in found:
        here, col = line_of(pos), _column(text, pos)
        gaps = [here - ln for s, ln, lcol in label_lines
                if s <= pos and 0 <= here - ln <= _LABEL_REACH_LINES and (lcol < 8 or col >= lcol - 8)]
        if gaps:
            pan = gstin[2:12]
            best[pan] = min(best.get(pan, 99), min(gaps))
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def _side(line: str, role: str) -> str:
    """The part of a line that belongs to `role` when a buyer label sits in its
    middle: "AARAF PHARMA M/s EASTERN AGENCIES" is the supplier's name, then
    the buyer's."""
    m = _BUYER_LABEL.search(line)
    if not m or m.start() == 0:
        return line
    return line[m.end():] if role == "buyer" else line[:m.start()]


def name_above(text: str, gstin: str, exclude: str = "", role: str = "buyer") -> Optional[str]:
    """The business name printed above a GSTIN, within its block."""
    if not gstin:
        return None
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if gstin not in line.upper():
            continue
        for back in range(0, 9):
            if i - back < 0:
                break
            segment = _side(lines[i - back], role)
            # An address line names buildings and estates, not the party:
            # "A-2 FIRST FLOOR TOBACCO HOUSE, GOLDEN TOBACCO LTD S.V.ROAD".
            if _ADDRESS_LINE.search(segment):
                continue
            for m in _NAME.finditer(segment):
                name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-:")
                name = _DOC_PREFIX.sub("", name).strip(" ,.-:")
                name = _PARTY_PREFIX.sub("", name).strip(" ,.-:")
                if len(_key(name)) < 6 or _NOT_NAME.search(name):
                    continue
                if exclude and _same_name(name, exclude):
                    continue
                return name
        break
    return None


# Kept for callers that name the old helper.
buyer_name_near = name_above

_SIGNED_FOR = re.compile(r"(?:^|[\s,])For\s*[,:.]?\s*(?:M\s*/\s*S\.?\s*)?([A-Z][A-Za-z0-9&.,'()\- ]{3,80})")
_SIGNATURE_STOP = re.compile(r"(terms|authori[sz]|recipient|signatory|e\s*&\s*o|subject|jurisdiction|"
                             r"supply of|sale in|payment|any |credit note|consignee)", re.I)
_LEGAL = re.compile(r"\b(PVT|PRIVATE|LTD|LIMITED|LLP|INC|ENTERPRISES?|AGENC(?:Y|IES)|PHARMA\w*|"
                    r"HEALTH\s*CARE|DISTRIBUTORS?|TRADERS|CHEM\w*|LAB\w*|LIFE\s*SCIENCES?|& CO)\b", re.I)


def signed_for(text: str) -> Optional[str]:
    """The company the bill is signed for - "For ADROIT BIOMED LIMITED" above
    the authorised signatory - when it names a company the page also prints
    elsewhere. None when there is no such line or it names more than one."""
    found = set()
    page_key = _key(text)
    for m in _SIGNED_FOR.finditer(text or ""):
        name = _SIGNATURE_STOP.split(m.group(1))[0]
        name = re.split(r"\s{2,}|\n", name)[0].strip(" ,.-:")
        # Ends at the company's suffix: "For AIOCD PHARMA LTD CN Ref" is AIOCD.
        cut = _NAME.match(name)
        if cut:
            name = cut.group(1).strip(" ,.-:")
        k = _key(name)
        if len(k) < 6 or not _LEGAL.search(name) or page_key.count(k) < 2:
            continue
        found.add(name)
    if len({_key(n) for n in found}) != 1:
        return None
    return sorted(found, key=len)[-1]


def _set(fields: dict, party: str, key: str, value, confidence: float = RESOLVED_CONFIDENCE) -> None:
    fields.setdefault(party, {})[key] = {"value": value, "confidence": confidence}


def resolve(fields: dict, text: str, stream_text: str = "") -> List[str]:
    """Correct and complete the parties' GSTINs and names from the page.

    `text` is the page as laid out; `stream_text`, when there is one, the
    page in the PDF's own drawing order - which keeps each party's block
    together where the layout text interleaves two side-by-side blocks line by
    line. Names are looked for in it first.

    Changes `fields` in place; returns a note for each correction, for the
    reviewer. Never raises.
    """
    try:
        notes = _resolve_gstins(fields, text or stream_text, stream_text)
        notes = notes + _resolve_names(fields, stream_text or text, text)
        _tidy_names(fields)
        _complete_own_name(fields)
        return notes
    except Exception:  # noqa: BLE001 - a second opinion never breaks a reading
        return []


def _signed_business(text: str, stream_text: str, businesses) -> Optional[str]:
    """The PAN of the business the bill is signed for: the one whose name,
    printed above its GSTIN, is the company in "For <company>". None unless
    exactly one business matches."""
    signed = signed_for(text) or (signed_for(stream_text) if stream_text else None)
    if not signed:
        return None
    # Each business's distance (lines) below the signed company's name; the
    # nearest wins, so a name line printed above both blocks still decides.
    nearest: Dict[str, int] = {}
    for pan, gstins in businesses.items():
        for source in (stream_text, text):
            gap = _lines_below_name(source, gstins[0], signed) if source else None
            if gap is not None:
                nearest[pan] = min(nearest.get(pan, 99), gap)
    ranked = sorted(nearest.items(), key=lambda kv: kv[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def _lines_below_name(text: str, gstin: str, name: str) -> Optional[int]:
    """How many lines below a line carrying `name` the GSTIN is printed."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if gstin not in line.upper():
            continue
        for back in range(0, 9):
            if i - back < 0:
                break
            for m in _NAME.finditer(_side(lines[i - back], "supplier")):
                if _same_name(m.group(1), name):
                    return back
        return None
    return None


_NAME_RUN_ON = re.compile(
    r"\s+(?:invoiced\s+at\b|customer\s+ord|ord(?:er)?\.?\s*no\b|gstin\b|gst\s*no\b|pan\s*(?:no)?\s*[:.]|"
    r"d\.?l\.?\s*no\b|[0-9a-f]{32,})", re.I)


def _complete_own_name(fields: dict) -> None:
    """The shop's own name for a buyer the bill names cut short, or not at
    all. "EASTERN" is the start of "EASTERN AGENCIES HEALTHCARE PVT. LTD.", the
    shop this bill was sent to; a name that is not a start of it - a trade
    name like "SHREE SIMBA CHEMIST" - is the bill's own and is kept."""
    own = OWN_GSTINS.get()
    if not isinstance(own, dict):
        return
    for party in ("bill_to", "ship_to"):
        gstin = _value(fields, party, "gstin").upper()
        known = own.get(gstin)
        if not known:
            continue
        current = _value(fields, party, "name")
        ck, kk = _key(current), _key(known)
        if not ck or (len(ck) < len(kk) and kk.startswith(ck)):
            _set(fields, party, "name", known, confidence=0.9)


def _tidy_names(fields: dict) -> None:
    """Cut a party's name where something that is not its name runs on:
    "BLUE CROSS LABORATORIES PVT LTD. INVOICED AT: ... 29b56488..." and
    "SHREE SIMBA CHEMIST CUSTOMER ORD. NO : 17185 ..." (Blue Cross)."""
    for party in ("supplier", "bill_to", "ship_to"):
        name = _value(fields, party, "name")
        if not name:
            continue
        cut = _NAME_RUN_ON.split(name)[0].strip(" ,.:-")
        if cut and cut != name and len(_key(cut)) >= 4:
            leaf = (fields.get(party) or {}).get("name") or {}
            _set(fields, party, "name", cut, confidence=leaf.get("confidence") or RESOLVED_CONFIDENCE)


def _resolve_gstins(fields: dict, text: str, stream_text: str = "") -> List[str]:
    found = gstins_on_page(text)
    # A GSTIN the layout text garbles can still be whole in the drawing-order
    # text (Troikaa). It joins the businesses, placed past the end of the page
    # so no label is taken to head it.
    seen = {g for _, g in found}
    for _, g in gstins_on_page(stream_text):
        if g not in seen:
            seen.add(g)
            found.append((len(text or "") + 1, g))
    businesses = _businesses(found)
    pans = list(businesses)
    supplier = _value(fields, "supplier", "gstin").upper()
    buyer = _value(fields, "bill_to", "gstin").upper()
    if len(businesses) < 2:
        own = {g[2:12] for g in OWN_GSTINS.get() if g}
        if pans and pans[0] in own and not buyer and supplier[2:12] != pans[0]:
            # The shop's GSTIN is the one valid GSTIN printed (Mednosis prints
            # its own with a wrong check digit, which its check reports).
            _set(fields, "bill_to", "gstin", businesses[pans[0]][0])
            return []
        if supplier and supplier[2:12] in own:
            # Only the shop's own GSTIN reads cleanly: it is the buyer, and the
            # supplier's is unread - not the shop's.
            if not buyer:
                _set(fields, "bill_to", "gstin", supplier)
            _set(fields, "supplier", "gstin", None, confidence=None)
            return [f"The supplier GSTIN was read as {supplier}, which is this shop's own - "
                    "please enter the supplier's."]
        return []
    supplier_pan = supplier[2:12] if gstin_is_valid(supplier) and supplier[2:12] in businesses else None
    buyer_pan = buyer[2:12] if gstin_is_valid(buyer) and buyer[2:12] in businesses else None

    # The shop reading its own bill: it is the buyer. Certain, not inferred.
    own = {g[2:12] for g in OWN_GSTINS.get() if g}
    own_here = [p for p in pans if p in own]
    if len(own_here) == 1:
        buyer_pan = own_here[0]
        others = [p for p in pans if p != buyer_pan]
        if len(others) == 1:
            supplier_pan = others[0]
        elif supplier_pan == buyer_pan or supplier_pan not in others:
            # Several other businesses (a C&F agent's GSTIN as well): the one
            # the bill is signed for, or the PAN printed as the supplier's.
            signed = _signed_business(text, stream_text, OrderedDict((p, businesses[p]) for p in others))
            printed_pan = _value(fields, "supplier", "pan").upper()
            supplier_pan = signed or (printed_pan if printed_pan in others else None)
        notes = _apply(fields, businesses, supplier, buyer, supplier_pan, buyer_pan)
        if not supplier_pan and supplier[2:12] == buyer_pan:
            # The shop's own GSTIN is never the supplier's. Blank and flagged
            # beats wrong and trusted.
            _set(fields, "supplier", "gstin", None, confidence=None)
            notes.append(f"The supplier GSTIN was read as {supplier}, which is this shop's own; "
                         "the supplier's could not be told apart on the page - please enter it.")
        return notes

    # Whose bill it is, in order of strength: the company it is signed for;
    # then the supplier block the reader found at the head of the page; labels
    # only ever say who the BUYER is.
    signed = _signed_business(text, stream_text, businesses)
    if signed:
        supplier_pan = signed
        if buyer_pan == signed:
            buyer_pan = None
    labelled = _nearest_below_label(text, found)
    if supplier_pan and supplier_pan == buyer_pan:
        # The same business on both sides: one of them is wrong.
        if labelled == supplier_pan:
            supplier_pan = None
        else:
            buyer_pan = None
    if not buyer_pan:
        if labelled and labelled != supplier_pan:
            buyer_pan = labelled
        elif supplier_pan and len(pans) == 2:
            buyer_pan = next(p for p in pans if p != supplier_pan)
        elif labelled:
            buyer_pan = labelled
    if not buyer_pan:
        return []
    if not supplier_pan or supplier_pan == buyer_pan:
        others = [p for p in pans if p != buyer_pan]
        printed_pan = _value(fields, "supplier", "pan").upper()
        if len(others) == 1:
            supplier_pan = others[0]
        elif printed_pan in others:
            supplier_pan = printed_pan
        else:
            supplier_pan = None
    return _apply(fields, businesses, supplier, buyer, supplier_pan, buyer_pan)


def _apply(fields: dict, businesses, supplier: str, buyer: str,
           supplier_pan: Optional[str], buyer_pan: Optional[str]) -> List[str]:
    notes: List[str] = []
    if supplier_pan and supplier[2:12] != supplier_pan:
        new = businesses[supplier_pan][0]
        if supplier:
            notes.append(f"The supplier GSTIN was read as {supplier}, which is the buyer's; "
                         f"the supplier's own, {new}, is printed on the invoice.")
        _set(fields, "supplier", "gstin", new)
        if _value(fields, "supplier", "pan").upper() == buyer_pan:
            # The buyer's PAN goes with its GSTIN. A PAN is never derived from
            # a GSTIN: what the bill does not print stays blank.
            _set(fields, "supplier", "pan", None)
    if buyer_pan and buyer[2:12] != buyer_pan:
        _set(fields, "bill_to", "gstin", businesses[buyer_pan][0])
        if _value(fields, "bill_to", "pan").upper() == (supplier_pan or ""):
            _set(fields, "bill_to", "pan", None)
    return notes


def _resolve_names(fields: dict, text: str, layout_text: str = "") -> List[str]:
    supplier_gstin = _value(fields, "supplier", "gstin").upper()
    buyer_gstin = _value(fields, "bill_to", "gstin").upper()
    if supplier_gstin and supplier_gstin[2:12] == buyer_gstin[2:12]:
        return []

    def above(gstin: str, role: str) -> Optional[str]:
        if not gstin_is_valid(gstin):
            return None
        return name_above(text, gstin, role=role) or (
            name_above(layout_text, gstin, role=role) if layout_text and layout_text != text else None)

    s_above = above(supplier_gstin, "supplier")
    b_above = above(buyer_gstin, "buyer")
    if s_above and b_above and _same_name(s_above, b_above):
        # Both GSTINs sit under the same name - interleaved columns; the page
        # cannot say whose name it is.
        s_above = b_above = None
    signed = signed_for(layout_text or text) or signed_for(text)
    if signed and b_above and _same_name(signed, b_above):
        signed = None
    current_s = _value(fields, "supplier", "name")
    current_b = _value(fields, "bill_to", "name")
    notes: List[str] = []

    # A supplier name that is the buyer's, or that ran on into the buyer's
    # column ("AARAF PHARMA M/s EASTERN AGENCIES...").
    trimmed = _side(current_s, "supplier").strip(" ,.-:") if current_s else ""
    run_on = bool(current_s) and trimmed != current_s and len(_key(trimmed)) >= 4
    is_buyers = bool(current_s) and (_same_name(current_s, b_above) or _same_name(current_s, current_b) and b_above
                                     and _same_name(current_b, b_above))
    if not current_s or is_buyers or run_on:
        if run_on and not is_buyers:
            # Cut where the buyer's column begins - or, better, the company the
            # bill is signed for when that is the same name.
            better = signed if signed and _key(trimmed).startswith(_key(signed)[:6]) else trimmed
        else:
            better = signed or s_above
        if better and not _same_name(better, b_above):
            _set(fields, "supplier", "name", better, confidence=0.85)
            if current_s and is_buyers:
                notes.append(f"The supplier was read as \"{current_s}\", which is the buyer's name; "
                             f"the invoice is from {better}.")
    new_s = _value(fields, "supplier", "name")
    # A buyer name that is the supplier's.
    if current_b and b_above and _same_name(current_b, new_s) and not _same_name(current_b, b_above):
        _set(fields, "bill_to", "name", b_above, confidence=0.85)
    elif not current_b and b_above and not _same_name(b_above, new_s):
        _set(fields, "bill_to", "name", b_above, confidence=0.8)
    return notes
