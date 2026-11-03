"""Accuracy reports must use complete human labels and preserve denominators."""
from copy import deepcopy
import json
import sys

import pytest

from scripts.score_holdout import HEADERS, LINE_FIELDS, main, score


def _case():
    label = {
        "id": "one", "split": "holdout", "verified": "checked by hand",
        "cohort": "printed_pdf", "source_pages": 2, "content_pages": [1, 2],
        "expect": {key: None for key in HEADERS},
        "lines": {"count": 1},
        "line_expect": [{key: None for key in LINE_FIELDS}],
    }
    label["expect"].update({"supplier.name": "Sample Pharma", "invoice.invoice_no": "S-1",
                            "invoice.invoice_date": "16-10-2026", "invoice.total_amount": "100.00"})
    label["line_expect"][0].update({"description": "Sample tablets", "batch_no": "B-1",
                                    "expiry": "03-2028", "quantity": "10", "rate": "10.00",
                                    "amount": "100.00", "gst_percent": "0"})
    prediction = {
        "id": "one", "status": "needs_review",
        "source_manifest": {"page_map": [1, 2], "removed_pages": []},
        "payload": {"fields": {
            "supplier": {"name": {"value": "Sample Pharma"}},
            "invoice": {"invoice_no": {"value": "S-1"},
                        "invoice_date": {"value": "2026-10-16"},
                        "total_amount": {"value": "100"}},
            "line_items": [{key: {"value": value} for key, value in label["line_expect"][0].items()}],
        }},
    }
    return label, prediction


def test_complete_human_holdout_scores_pages_rows_and_fields():
    label, prediction = _case()
    report = score({"cases": [label]}, {"cases": [prediction]})
    assert report["eligible_cases"] == 1 and report["excluded_cases"] == 0
    overall = report["overall"]
    assert overall["page_recall"] == overall["row_recall"] == 1.0
    assert overall["header_field_accuracy"] == overall["line_field_accuracy"] == 1.0
    assert overall["accepted_with_critical_error"] == 0


def test_partial_or_nonhuman_labels_never_become_accuracy_claims():
    label, prediction = _case()
    label["verified"] = "our reading and an independent AI reading agree"
    del label["line_expect"][0]["quantity"]
    report = score({"cases": [label]}, {"cases": [prediction]})
    assert report["eligible_cases"] == 0 and report["overall"] is None
    assert report["exclusion_reasons"]["not_human_checked"] == 1
    assert report["exclusion_reasons"]["incomplete_line_fields"] == 1


def test_missing_row_and_page_count_as_accepted_critical_error():
    label, prediction = _case()
    prediction["status"] = "approved"
    prediction["payload"]["fields"]["line_items"] = []
    prediction["source_manifest"]["page_map"] = [1]
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["page_recall"] == 0.5
    assert report["row_recall"] == 0.0
    assert report["line_field_accuracy"] == 0.0
    assert report["accepted_with_critical_error"] == 1


def test_removed_blank_page_counts_as_accounted_for():
    label, prediction = _case()
    label["content_pages"] = [1]
    prediction["source_manifest"] = {"page_map": [1], "removed_pages": [2]}
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["page_recall"] == 1.0 and report["complete_page_sets"] == 1


def test_content_page_mislabelled_as_blank_is_not_credited():
    label, prediction = _case()
    prediction["status"] = "approved"
    prediction["source_manifest"] = {"page_map": [1], "removed_pages": [2]}
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["page_recall"] == 0.5
    assert report["complete_page_sets"] == 0
    assert report["accepted_with_critical_error"] == 1


def test_approved_missing_page_is_a_silent_critical_error_even_if_fields_match():
    label, prediction = _case()
    prediction["status"] = "approved"
    prediction["source_manifest"]["page_map"] = [1]
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["header_field_accuracy"] == report["line_field_accuracy"] == 1.0
    assert report["accepted_with_critical_error"] == 1


def test_duplicate_prediction_row_is_counted_as_unexpected_and_critical():
    label, prediction = _case()
    prediction["status"] = "approved"
    rows = prediction["payload"]["fields"]["line_items"]
    rows.append(deepcopy(rows[0]))
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["row_recall"] == 1.0
    assert report["unexpected_rows"] == 1
    assert report["exact_row_count_documents"] == 0
    assert report["accepted_with_critical_error"] == 1


def test_wrong_row_identity_is_unmatched_even_when_row_count_matches():
    label, prediction = _case()
    prediction["payload"]["fields"]["line_items"][0]["description"]["value"] = "Other tablets"
    report = score({"cases": [label]}, {"cases": [prediction]})["overall"]
    assert report["row_recall"] == 0.0
    assert report["unexpected_rows"] == 1
    assert report["exact_row_count_documents"] == 1


def test_missing_prediction_cannot_be_silently_excluded():
    label, _ = _case()
    with pytest.raises(ValueError, match="Missing prediction"):
        score({"cases": [label]}, {"cases": []})


def test_duplicate_prediction_ids_are_rejected():
    label, prediction = _case()
    with pytest.raises(ValueError, match="unique id"):
        score({"cases": [label]}, {"cases": [prediction, deepcopy(prediction)]})


def test_release_gate_rejects_partial_labels_and_small_samples(tmp_path, monkeypatch):
    label, prediction = _case()
    labels = tmp_path / "labels.json"
    predictions = tmp_path / "predictions.json"
    labels.write_text(json.dumps({"cases": [label]}), encoding="utf-8")
    predictions.write_text(json.dumps({"cases": [prediction]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["score_holdout.py", str(labels), str(predictions), "--min-cases", "2"])
    assert main() == 1
    label["verified"] = "AI agreement"
    labels.write_text(json.dumps({"cases": [label]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["score_holdout.py", str(labels), str(predictions), "--require-complete"])
    assert main() == 1
