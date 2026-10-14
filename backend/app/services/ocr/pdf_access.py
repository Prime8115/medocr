"""Reuse parsed PDF pages only within one extraction, then release them.

Routing, table extraction and licence checks inspect the same pages. Reopening
them makes PDFMiner decode every character repeatedly. No PDF objects or
customer text are retained globally or shared between worker threads.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import io

_OPEN: ContextVar = ContextVar("extraction_pdfs", default=None)
_MAX_OPEN = 4


@contextmanager
def pdf_session():
    if _OPEN.get() is not None:
        yield
        return
    documents = {}
    token = _OPEN.set(documents)
    try:
        yield
    finally:
        _OPEN.reset(token)
        for document in documents.values():
            document.close()


@contextmanager
def open_pdf(data):
    import pdfplumber

    documents = _OPEN.get()
    if documents is None:
        with pdfplumber.open(io.BytesIO(data)) as document:
            yield document
        return
    key = hashlib.sha256(data).digest()
    if key not in documents:
        if len(documents) >= _MAX_OPEN:
            with pdfplumber.open(io.BytesIO(data)) as document:
                yield document
            return
        documents[key] = pdfplumber.open(io.BytesIO(data))
    yield documents[key]
