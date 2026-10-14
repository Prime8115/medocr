from scripts.audit_corpus import audit, HEADERS, LINE_FIELDS


def _labelled():
    return {"file": "synthetic.pdf", "reader": "parser", "verified": "checked by hand",
            "expect": {p: None for p in HEADERS}, "lines": {"count": 1},
            "line_expect": [{p: None for p in LINE_FIELDS}]}


def test_explicit_absence_is_labelled_but_omission_is_not():
    entry = _labelled()
    assert audit({"invoices": [entry]})["complete"] is True
    del entry["line_expect"][0]["batch_no"]
    report = audit({"invoices": [entry]})
    assert report["complete"] is False
    assert report["queue"][0]["missing_line_fields"] == [{"line": 1, "fields": ["batch_no"]}]


def test_ai_agreement_and_unresolved_comparisons_are_not_human_ground_truth():
    entry = _labelled()
    entry["verified"] = "our reading and an independent AI reading agree"
    entry["todo"] = [{"field": "quantity"}]
    report = audit({"invoices": [entry]})
    assert set(report["queue"][0]["gaps"]) == {"human_validation_not_recorded", "unresolved_comparisons"}


def test_missing_rows_and_ai_replay_are_visible_and_empty_corpus_is_not_complete():
    assert audit({})["complete"] is False
    entry = _labelled()
    entry["lines"]["count"] = 2
    assert "line_rows_not_fully_labelled" in audit({"invoices": [entry]})["queue"][0]["gaps"]
    entry["reader"] = "ai"
    assert audit({"invoices": [entry]})["queue"][0]["gaps"] == ["ai_path_not_recorded"]
