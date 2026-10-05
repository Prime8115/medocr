"""Read an invoice total that is printed only in words.

Bharat Serums prints no machine-readable grand total at all - just
"Total Amount in Words : FOUR LAKH THIRTEEN THOUSAND THREE HUNDRED SIXTY SIX
ONLY". Without this the invoice can never be reconciled, and the pharmacist is
asked to type the one number the bill does state.

This is reading what is printed, in the notation it is printed in - not guessing
a figure. It returns None whenever the words don't parse cleanly, so a bad read
becomes "no total" rather than a wrong total.

Indian numbering: crore (10^7), lakh (10^5), then thousand / hundred.
"""
import re
from typing import Optional

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# Multipliers, largest first so "lakh" is applied before "thousand".
_SCALES = (("crore", 10_000_000), ("lakh", 100_000), ("lac", 100_000), ("thousand", 1_000))

_NOISE = re.compile(
    r"\b(rupees?|rs|inr|only|and|paise|paisa|total|amount|in|words?|net|payable)\b",
    re.I,
)
# Ten crore. Comfortably above any distributor invoice we would ever see.
_MAX_PLAUSIBLE_TOTAL = 100_000_000

_LABEL = re.compile(
    # A COLON is required after the label, not a colon-or-dash. A bare hyphen is
    # never a label separator on these bills, but it is everywhere inside the
    # numbers themselves: on "RUPEES FIFTY-FOUR THOUSAND FIFTY-EIGHT ONLY" the
    # old pattern took the hyphen in "FIFTY-FOUR" for the separator and read the
    # remainder, "EIGHT ONLY", as the amount - 8 rupees for a 54,058 bill.
    r"(?:amount|total|rupees)[^:\n]{0,30}(?:in\s*words|words)?\s*:-?\s*(?P<words>[A-Za-z \-]{10,200})"
    # Tally prints "Amount Chargeable (in words)" with the figure on the NEXT
    # line and no colon - MSV Lifesciences - and a scanner may garble the
    # words before "(in words)". The bracketed label alone is enough.
    r"|\(\s*in\s*words\s*\)\s*[:\-;]?\s*(?P<words2>[A-Za-z \-]{10,200})"
    # ...and with scanner noise after it on the same line ("oo an 7 E&OE").
    r"|\(\s*in\s*words\s*\)[^\n]{0,40}\n\s*(?P<words3>[A-Za-z \-]{10,200})"
    # "RUPEES FIFTY-FOUR THOUSAND FIFTY-EIGHT ONLY" (Menarini) - the commonest
    # Indian form, carrying no "in words" label and no colon at all. Without it
    # the first alternative matched "rupees", then took the hyphen inside
    # "FIFTY-FOUR" for the label separator, and the line parsed as 8.
    r"|\brupees?\s+(?P<words4>[A-Za-z][A-Za-z \-]{9,200}?)\s+only\b",
    re.I,
)

# "... Fifteen and Ninety Two paise Only": the paise are not rupees. Counting
# them in turned 715.92 into 807.
_PAISE = re.compile(r"\b(?:and|rupees?)\s+(?:[a-z]+[\s\-]+){0,4}pais[ae]\b.*$", re.I)
# The page often spells out its tax too ("Tax Amount (in words)"). That is not
# the invoice total, however large.
_TAX_LABEL = re.compile(r"tax\s*(?:amount|amt)?\s*$", re.I)


_NOISE_WORDS = ("rupees", "rupee", "only", "and", "paise", "paisa", "rs", "inr")

# Longest first, so "sixty" is matched before "six" and "thirteen" before "three".
_VOCABULARY = sorted(
    set(_UNITS) | set(_TENS) | {"hundred"} | {w for w, _ in _SCALES} | set(_NOISE_WORDS),
    key=len,
    reverse=True,
)


def _segment(blob: str) -> Optional[list]:
    """Split a run-together words blob into number words.

    Bharat's PDF draws each glyph separately with no space characters, so its
    amount in words extracts as "FOURLAKHTHIRTEENTHOUSANDTHREEHUNDREDSIXTYSIX".
    Greedy longest-match splits that back into words; an unknown fragment means
    this was never a number, and we give up rather than guess.
    """
    out: list = []
    i = 0
    low = blob.lower()
    while i < len(low):
        for word in _VOCABULARY:
            if low.startswith(word, i):
                out.append(word)
                i += len(word)
                break
        else:
            return None
    return out


def _words_to_int(text: str) -> Optional[int]:
    """Turn a run of English number words into an integer, or None."""
    known = set(_UNITS) | set(_TENS) | {"hundred"} | {w for w, _ in _SCALES}
    tokens: list = []
    text = _PAISE.sub("", text)
    for raw in re.split(r"[\s\-]+", _NOISE.sub(" ", text).lower()):
        if not raw:
            continue
        if raw in known or raw in _NOISE_WORDS:
            tokens.append(raw)
            continue
        pieces = _segment(raw)
        if pieces is None:
            return None
        tokens.extend(pieces)
    tokens = [t for t in tokens if t not in _NOISE_WORDS]
    if not tokens:
        return None

    total = 0          # everything already closed off by a scale word
    chunk = 0          # the group being built up
    seen_number = False

    for token in tokens:
        if token in _UNITS:
            chunk += _UNITS[token]
            seen_number = True
        elif token in _TENS:
            chunk += _TENS[token]
            seen_number = True
        elif token == "hundred":
            chunk = (chunk or 1) * 100
            seen_number = True
        else:
            scale = next((mult for word, mult in _SCALES if token == word), None)
            if scale is None:
                # An unknown word means this is prose, not a number.
                return None
            total += (chunk or 1) * scale
            chunk = 0
            seen_number = True

    if not seen_number:
        return None
    return total + chunk


def total_from_words(text: str) -> Optional[str]:
    """The invoice total taken from its amount-in-words line, as a string.

    Tries every "... in words: ..." style label on the page and keeps the
    largest parse, since the same page often spells out a tax figure too.
    """
    best: Optional[int] = None
    for match in _LABEL.finditer(text or ""):
        before = (text or "")[max(0, match.start() - 16):match.start()]
        if _TAX_LABEL.search(before):
            continue
        words = (match.group("words") or match.group("words2")
                 or match.group("words3") or match.group("words4"))
        value = _words_to_int(words)
        # A pharmacy invoice below a rupee, or above ten crore, is a misparse
        # rather than a total. A wrong total is worse than no total: it would
        # let a bad extraction reconcile and be approved.
        if value and 1 <= value <= _MAX_PLAUSIBLE_TOTAL and (best is None or value > best):
            best = value
    return f"{best}.00" if best is not None else None
