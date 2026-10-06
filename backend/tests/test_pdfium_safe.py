"""PDFium is not thread-safe; every use of it goes through one lock.

Deploys failed at random with exit code 139 - a segmentation fault - when the
scan cross-read rendered pages in a background thread while the main thread
rendered the same scan for the AI. In production the same race could kill the
worker handling an upload, and concurrent OCR jobs could trigger it too.
"""
import concurrent.futures as cf
import io
import pathlib
import re

from PIL import Image

from app.services.ocr.pdfium_safe import page_total, render_pages, resave

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _pdf(pages: int = 3) -> bytes:
    images = [Image.new("RGB", (600, 800), (255, 255, 255)) for _ in range(pages)]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def test_nothing_but_the_safe_module_touches_pdfium():
    """A direct import anywhere else would bypass the lock and bring the crash back."""
    offenders = [
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if path.name != "pdfium_safe.py"
        and re.search(r"^\s*(import pypdfium2|from pypdfium2)", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == [], f"use app.services.ocr.pdfium_safe instead: {offenders}"


def test_rendering_from_many_threads_at_once_does_not_crash():
    data = _pdf(3)

    def work(_):
        images = render_pages(data, 1.0)
        assert page_total(data) == 3
        assert resave(data)
        return len(images)

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(work, range(48)))
    assert counts == [3] * 48


def test_rendered_pages_are_independent_of_pdfium():
    # Copied out, so an image stays usable after its document is closed.
    [image] = render_pages(_pdf(1), 0.5)
    assert image.size == (300, 400)
    assert image.getpixel((10, 10)) in ((255, 255, 255), 255)


def test_only_and_max_pages_select_pages():
    data = _pdf(4)
    assert len(render_pages(data, 0.2, max_pages=2)) == 2
    assert len(render_pages(data, 0.2, only=[1, 3, 9])) == 2
