"""Work done once per scan, and nothing that holds up other uploads."""
from app.services.ocr import pdf_utils
from tests.fixtures.invoice_pdf import build_invoice_pdf


def test_a_pdf_is_judged_digital_or_scanned_once(monkeypatch):
    data = build_invoice_pdf(n_items=3)
    calls = {"digital": 0, "scanned": 0}
    real_digital, real_scanned = pdf_utils._is_digital_pdf, pdf_utils._is_scanned_pdf

    def digital(*args):
        calls["digital"] += 1
        return real_digital(*args)

    def scanned(*args):
        calls["scanned"] += 1
        return real_scanned(*args)

    monkeypatch.setattr(pdf_utils, "_is_digital_pdf", digital)
    monkeypatch.setattr(pdf_utils, "_is_scanned_pdf", scanned)
    monkeypatch.setattr(pdf_utils, "_ANSWERS", type(pdf_utils._ANSWERS)())
    answers = {pdf_utils.is_digital_pdf(data) for _ in range(5)} | {not pdf_utils.is_scanned_pdf(data)}
    assert answers == {True}
    assert calls == {"digital": 1, "scanned": 1}
    # Another file is its own question.
    pdf_utils.is_digital_pdf(build_invoice_pdf(n_items=4))
    assert calls["digital"] == 2


def test_tesseract_runs_on_one_thread_per_page():
    from app.services.ocr.tesseract_table import single_threaded_env

    assert single_threaded_env()["OMP_THREAD_LIMIT"] == "1"


def test_the_upload_handler_leaves_pdf_work_to_a_thread():
    import inspect

    from app.api import documents

    source = inspect.getsource(documents.submit_document)
    assert "run_in_threadpool(intake.prepare" in source
    assert "run_in_threadpool(intake.invoice_groups" in source
