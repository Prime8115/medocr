"""PDF reuse must never outlive an invoice or leak into another worker."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app.services.ocr.pdf_access import open_pdf, pdf_session


class PDF:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_same_request_reuses_pages_and_closes_on_error(monkeypatch):
    import pdfplumber
    opened = []

    def create(_):
        result = PDF()
        opened.append(result)
        return result

    monkeypatch.setattr(pdfplumber, "open", create)
    with pytest.raises(ValueError):
        with pdf_session():
            with open_pdf(b"one") as first:
                pass
            with open_pdf(b"one") as again:
                assert first is again and not first.closed
            raise ValueError("failed extraction")
    assert first.closed
    with pdf_session(), open_pdf(b"one") as fresh:
        assert fresh is not first
    assert fresh.closed and len(opened) == 2


def test_parallel_documents_do_not_share_pdf_objects(monkeypatch):
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda _: PDF())
    barrier = threading.Barrier(2)

    def worker():
        with pdf_session(), open_pdf(b"same bytes") as pdf:
            barrier.wait(timeout=5)
            return pdf

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: worker(), range(2)))
    assert a is not b and a.closed and b.closed
