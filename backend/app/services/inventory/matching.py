"""Fuzzy matching of extracted medicine names against a shop's inventory catalog.

Pharmacy names are noisy ("PCM 500", "Paracetamol 500mg Tab", "Calpol 500").
We normalize away dosage-form and unit noise, then score similarity with
rapidfuzz (token-set ratio, which is robust to word order and extra tokens).

Returns ranked candidates with a 0-100 match score so the UI can show
"how much it matches" and let the user confirm low-confidence matches — the
same philosophy as OCR confidence. We never silently auto-substitute.
"""
import re
from typing import List, Optional, Sequence

try:
    from rapidfuzz import fuzz, process

    _HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - fallback keeps things working
    _HAVE_RAPIDFUZZ = False

# Dosage forms and filler words that add noise to a name comparison.
_FORM_WORDS = {
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules",
    "syrup", "syp", "susp", "suspension", "inj", "injection", "drop", "drops",
    "cream", "ointment", "gel", "lotion", "solution", "sachet", "strip",
    "mg", "mcg", "ml", "gm", "g", "iu", "%",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize(name: Optional[str]) -> str:
    """Lowercase, drop form/unit noise, keep meaningful tokens (incl. dose numbers)."""
    if not name:
        return ""
    tokens = _TOKEN_RE.findall(name.lower())
    kept = [t for t in tokens if t not in _FORM_WORDS]
    return " ".join(kept) if kept else " ".join(tokens)


def _score(a: str, b: str) -> float:
    if _HAVE_RAPIDFUZZ:
        return float(fuzz.token_set_ratio(a, b))
    # Fallback: token overlap (Jaccard) scaled to 0-100.
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return 100.0 * len(sa & sb) / len(sa | sb)


class Candidate:
    __slots__ = ("id", "name", "sku", "strength", "mrp", "stock_qty", "score", "composition")

    def __init__(self, item, score: float):
        self.id = item.id
        self.name = item.name
        self.sku = item.sku
        self.strength = item.strength
        self.mrp = item.mrp
        self.stock_qty = item.stock_qty
        self.composition = getattr(item, "composition", None)
        self.score = round(score, 1)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "sku": self.sku,
            "strength": self.strength, "mrp": self.mrp, "composition": self.composition,
            "stock_qty": self.stock_qty, "score": self.score,
        }


def match_name(query: str, items: Sequence, limit: int = 3, min_score: float = 40.0) -> List[Candidate]:
    """Return up to `limit` best-matching inventory items for a raw query name.

    `items` is a sequence of InventoryItem (each with .normalized_name). Items
    below `min_score` are dropped.
    """
    q = normalize(query)
    if not q or not items:
        return []

    scored = []
    if _HAVE_RAPIDFUZZ:
        # Use process.extract for speed over large catalogs.
        choices = {i: it.normalized_name for i, it in enumerate(items)}
        for _, score, idx in process.extract(
            q, choices, scorer=fuzz.token_set_ratio, limit=limit
        ):
            if score >= min_score:
                scored.append(Candidate(items[idx], score))
    else:  # pragma: no cover
        ranked = sorted(
            ((it, _score(q, it.normalized_name)) for it in items),
            key=lambda t: t[1], reverse=True,
        )
        for it, sc in ranked[:limit]:
            if sc >= min_score:
                scored.append(Candidate(it, sc))
    return scored


def base_molecule(name: Optional[str]) -> str:
    """Primary active-ingredient token(s): the alphabetic words, dropping dose
    numbers and form/unit noise. 'Calpol 500mg Tab' -> 'calpol';
    'Paracetamol 500' -> 'paracetamol'. First-token heuristic (good enough for a
    first version; combination drugs keep all alpha tokens)."""
    norm = normalize(name)
    alpha = [t for t in norm.split() if not any(ch.isdigit() for ch in t)]
    return " ".join(alpha)


def _molecule_of(item) -> str:
    """An item's molecule: its `composition` if the catalog provides one
    (best — enables true brand-to-brand substitution), else derived from name."""
    comp = getattr(item, "composition", None)
    return normalize(comp) if comp else base_molecule(getattr(item, "name", ""))


def find_alternatives(
    query: str, items: Sequence, limit: int = 5, min_score: float = 75.0,
    query_composition: Optional[str] = None,
) -> List[Candidate]:
    """Substitutes in stock: other items sharing the same molecule/composition.

    Prefers the `composition` field (so different brands of the same salt match,
    e.g. Calpol ~ Crocin ~ Paracetamol). Falls back to the molecule derived from
    the name when composition isn't available. Excludes items with no overlap.
    """
    base = normalize(query_composition) if query_composition else base_molecule(query)
    if not base or not items:
        return []
    primary = base.split()[0]  # main molecule token

    out = []
    for it in items:
        mol = _molecule_of(it)
        if not mol:
            continue
        if primary not in mol.split():
            continue
        score = _score(base, mol)
        if score >= min_score:
            out.append(Candidate(it, score))
    out.sort(key=lambda c: (c.score, c.stock_qty or 0), reverse=True)
    return out[:limit]


def alternatives_for(query: str, items: Sequence, limit: int = 5) -> List[Candidate]:
    """Full substitute lookup for a scanned name.

    Resolves the query's molecule by first name-matching it to a catalog item and
    using that item's `composition` (so scanning a brand like "Calpol" finds other
    Paracetamol brands). Falls back to the name-derived molecule.
    """
    if not items:
        return []
    best = match_name(query, items, limit=1, min_score=55.0)
    comp = None
    if best:
        matched = next((it for it in items if it.id == best[0].id), None)
        comp = getattr(matched, "composition", None) if matched else None
    return find_alternatives(query, items, limit=limit, query_composition=comp)


def enrich_payload_with_matches(payload: dict, items: Sequence, min_score: float = 70.0) -> int:
    """Attach the best inventory match to each line item / medication IN PLACE.

    Adds `inventory_match: {id, sku, name, score}` to every item whose best match
    scores >= min_score, so the pushed data carries the shop's own item codes and
    can update their inventory directly. Returns the number of items linked.
    """
    fields = (payload or {}).get("fields") or {}
    doc_type = (payload or {}).get("doc_type", "prescription")
    if doc_type == "invoice":
        rows = fields.get("line_items", []) or []
        name_of = lambda r: ((r.get("description") or {}).get("value"))
    else:
        rows = fields.get("medications", []) or []
        name_of = lambda r: ((r.get("name") or {}).get("value"))

    linked = 0
    for row in rows:
        cands = match_name(name_of(row) or "", items, limit=1, min_score=min_score)
        if cands:
            c = cands[0]
            row["inventory_match"] = {"id": c.id, "sku": c.sku, "name": c.name, "score": c.score}
            linked += 1
        else:
            row.pop("inventory_match", None)
    return linked


def match_document_items(payload: dict, items: Sequence) -> dict:
    """Attach inventory matches to each medication / invoice line item.

    Returns a summary: {matched, total, items: [{name, best_score, candidates}]}.
    Does not mutate the payload; the caller decides how to present/persist.
    """
    data = (payload or {}).get("fields") or (payload or {}).get("data") or {}
    doc_type = (payload or {}).get("doc_type", "prescription")

    if doc_type == "invoice":
        rows = data.get("line_items", []) or []
        name_of = lambda row: ((row.get("description") or {}).get("value"))
    else:
        rows = data.get("medications", []) or []
        name_of = lambda row: ((row.get("name") or {}).get("value"))

    results = []
    matched = 0
    for row in rows:
        raw = name_of(row)
        candidates = match_name(raw or "", items)
        best = candidates[0].score if candidates else 0.0
        if best >= 70:
            matched += 1
        results.append({
            "name": raw,
            "best_score": best,
            "candidates": [c.as_dict() for c in candidates],
        })
    return {"matched": matched, "total": len(rows), "items": results}
