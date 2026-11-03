"""A product's name is its name: not its HSN, maker, flag column or the end
of the line above it (services/ocr/postprocess.py)."""
from app.services.ocr.postprocess import drop_flag_column, rejoin_hyphen_wraps, tidy_line


def _tidy(name, **others):
    item = {"description": {"value": name, "confidence": 1.0}}
    item.update({k: {"value": v} for k, v in others.items()})
    tidy_line(item)
    return item


def _v(item, key):
    return (item.get(key) or {}).get("value")


def test_the_hsn_printed_in_the_name_goes_to_the_hsn():
    item = _tidy("OLMETIME-H 20 TABLETS(HSN- 30049079)")
    assert (_v(item, "description"), _v(item, "hsn")) == ("OLMETIME-H 20 TABLETS", "30049079")
    # ...never over one already read.
    assert _v(_tidy("URIKIND(HSN- 30049099)", hsn="30049011"), "hsn") == "30049011"


def test_the_maker_printed_after_the_pack_goes_to_the_manufacturer():
    item = _tidy("JUPIROS EZ 40 TABLET 10'S Windlass Biotech Ltd")
    assert (_v(item, "description"), _v(item, "manufacturer")) == ("JUPIROS EZ 40 TABLET 10'S", "Windlass Biotech Ltd")
    assert _v(_tidy("ZINCOVIT TAB 15'S Apex Laboratories Pvt Ltd"), "manufacturer") == "Apex Laboratories Pvt Ltd"


def test_the_end_of_a_maker_line_is_not_part_of_the_name():
    assert _v(_tidy("LEXATAL-5C TAB LIMITED. (CNS)"), "description") == "LEXATAL-5C TAB"


def test_ordinary_names_are_left_alone():
    for name in ("DOLO 650 TAB", "SHELCAL 500 TAB 15'S", "GLENMARK PHARMA LIMITED", "BECOSULES CAPSULES",
                 "CALPOL 500 Limited Edition Pack", "NANO LEO TOTAL SACHETS 10X3GM"):
        item = _tidy(name)
        assert _v(item, "description") == name and _v(item, "manufacturer") is None


def _items(*names):
    return [{"description": {"value": n}} for n in names]


def test_a_flag_letter_on_every_name_is_dropped():
    items = _items("N GLUCORED FORTE", "N ISTAMET 50MG", "N ISTAMET 100MG", "N JANUVIA 50")
    drop_flag_column(items)
    assert [i["description"]["value"] for i in items] == ["GLUCORED FORTE", "ISTAMET 50MG", "ISTAMET 100MG",
                                                         "JANUVIA 50"]
    # ...but not a letter a few names happen to start with.
    items = _items("C GEL", "DOLO 650", "PAN 40", "VITAMIN C TAB")
    drop_flag_column(items)
    assert items[0]["description"]["value"] == "C GEL"


def test_a_name_carried_onto_the_next_page_goes_back():
    items = _items("METROGYL TABS 200MG 20's", "-BLISTER METROGYL-O TABLETS 10'S BLISTER")
    rejoin_hyphen_wraps(items)
    assert [i["description"]["value"] for i in items] == ["METROGYL TABS 200MG 20's -BLISTER",
                                                         "METROGYL-O TABLETS 10'S BLISTER"]
