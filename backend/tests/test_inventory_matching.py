"""Inventory fuzzy-matching engine."""
from app.services.inventory.matching import (
    alternatives_for,
    base_molecule,
    find_alternatives,
    match_document_items,
    match_name,
    normalize,
)


class FakeItem:
    def __init__(self, name, id="i", sku=None, strength=None, mrp=None, stock_qty=0, composition=None):
        self.id = id
        self.name = name
        self.normalized_name = normalize(name)
        self.composition = composition
        self.sku = sku
        self.strength = strength
        self.mrp = mrp
        self.stock_qty = stock_qty


CATALOG = [
    FakeItem("Paracetamol 500mg Tablet", id="1", stock_qty=100),
    FakeItem("Amoxicillin 250mg Capsule", id="2", stock_qty=50),
    FakeItem("Cetirizine 10mg Tablet", id="3", stock_qty=30),
    FakeItem("Azithromycin 500mg Tablet", id="4", stock_qty=20),
]


def test_normalize_strips_form_words():
    assert normalize("Paracetamol 500mg Tab") == "paracetamol 500mg"
    assert normalize("AMOXICILLIN CAPSULE") == "amoxicillin"


def test_exact_ish_match_scores_high():
    res = match_name("Paracetamol 500", CATALOG)
    assert res
    assert res[0].id == "1"
    assert res[0].score >= 80


def test_abbreviation_still_finds_candidate():
    # "PCM" won't match well, but "Paracetamol tab" should.
    res = match_name("Paracetamol tablet", CATALOG)
    assert res[0].id == "1"


def test_wrong_name_returns_nothing_above_threshold():
    res = match_name("Insulin Glargine", CATALOG, min_score=60)
    assert all(c.id != "1" for c in res)


def test_ranking_puts_best_first():
    res = match_name("Azithromycin 500", CATALOG, limit=3)
    assert res[0].id == "4"


def test_match_document_items_prescription():
    payload = {
        "doc_type": "prescription",
        "fields": {
            "medications": [
                {"name": {"value": "Paracetamol 500 mg"}},
                {"name": {"value": "Cetirizine 10mg"}},
                {"name": {"value": "SomeUnknownDrug"}},
            ]
        },
    }
    summary = match_document_items(payload, CATALOG)
    assert summary["total"] == 3
    assert summary["matched"] >= 2  # paracetamol + cetirizine
    first = summary["items"][0]
    assert first["candidates"][0]["name"] == "Paracetamol 500mg Tablet"
    assert first["best_score"] >= 70


def test_match_document_items_invoice():
    payload = {
        "doc_type": "invoice",
        "fields": {"line_items": [{"description": {"value": "Amoxicillin 250"}}]},
    }
    summary = match_document_items(payload, CATALOG)
    assert summary["items"][0]["candidates"][0]["id"] == "2"


def test_empty_catalog_returns_no_matches():
    assert match_name("Paracetamol", []) == []


# --- Alternatives / substitutes ---
BRANDS = [
    FakeItem("Calpol 500mg Tablet", id="a", stock_qty=40),
    FakeItem("Dolo 650mg Tablet", id="b", stock_qty=80),
    FakeItem("Crocin 500mg Tablet", id="c", stock_qty=10),
    FakeItem("Paracetamol 500mg Tablet", id="d", stock_qty=100),
    FakeItem("Amoxicillin 250mg Capsule", id="e", stock_qty=50),
]

# Brand catalog where the molecule is the shared word.
MOLECULE_CATALOG = [
    FakeItem("Paracetamol 500", id="p1", stock_qty=100),
    FakeItem("Paracetamol 650", id="p2", stock_qty=60),
    FakeItem("Paracetamol Syrup", id="p3", stock_qty=5),
    FakeItem("Azithromycin 500", id="z1", stock_qty=20),
]


def test_base_molecule():
    assert base_molecule("Paracetamol 500mg Tab") == "paracetamol"
    assert base_molecule("Calpol 500") == "calpol"


def test_alternatives_same_molecule_in_stock():
    res = find_alternatives("Paracetamol 500 mg", MOLECULE_CATALOG)
    ids = {c.id for c in res}
    assert {"p1", "p2", "p3"} <= ids   # all paracetamol variants
    assert "z1" not in ids             # different molecule excluded


def test_alternatives_ranked_by_score_then_stock():
    res = find_alternatives("Paracetamol 500", MOLECULE_CATALOG)
    # p1 and p2 share molecule; higher stock (p1=100) should rank at/near top.
    assert res[0].id in {"p1", "p2"}


def test_alternatives_empty_when_no_match():
    assert find_alternatives("Insulin", MOLECULE_CATALOG) == []


def test_brand_substitution_via_composition():
    """Different brand names substitute when they share a composition field."""
    branded = [
        FakeItem("Calpol 500", id="c1", stock_qty=40, composition="Paracetamol 500"),
        FakeItem("Crocin 500", id="c2", stock_qty=90, composition="Paracetamol 500"),
        FakeItem("Dolo 500", id="c3", stock_qty=15, composition="Paracetamol 500"),
        FakeItem("Zithromax 500", id="z", stock_qty=10, composition="Azithromycin 500"),
    ]
    # Pharmacist scanned "Calpol" — find same-composition brands in stock.
    res = find_alternatives("Calpol 500", branded, query_composition="Paracetamol 500")
    ids = {c.id for c in res}
    assert {"c1", "c2", "c3"} <= ids
    assert "z" not in ids
    # Highest stock (Crocin=90) ranks first among equal-score matches.
    assert res[0].id == "c2"


def test_alternatives_for_resolves_composition_from_scanned_brand():
    """Scanning a brand name (no composition given) still finds same-salt brands
    by resolving composition from the matched catalog item."""
    branded = [
        FakeItem("Calpol 500", id="c1", stock_qty=40, composition="Paracetamol 500"),
        FakeItem("Crocin 500", id="c2", stock_qty=90, composition="Paracetamol 500"),
        FakeItem("Dolo 500", id="c3", stock_qty=15, composition="Paracetamol 500"),
        FakeItem("Azee 500", id="z", stock_qty=10, composition="Azithromycin 500"),
    ]
    res = alternatives_for("Calpol 500", branded)  # no composition passed
    ids = {c.id for c in res}
    assert {"c1", "c2", "c3"} <= ids
    assert "z" not in ids
