"""Private, bounded source-page previews."""
import base64
import io

from PIL import Image, ImageOps

from app.services.intake import sniff, PDF


def page_total(data):
    if sniff(data) == PDF:
        from app.services.ocr.pdf_utils import page_count
        return page_count(data)
    return 1


def preview(data, page):
    total = page_total(data)
    if page < 1 or page > total:
        raise ValueError("That page is not in this document.")
    if sniff(data) == PDF:
        from app.services.ocr.pdfium_safe import render_pages
        image = render_pages(data, 2, only=[page - 1], max_side=2200)[0]
    else:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((2200, 2200))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=90)
    return {"page": page, "pages": total, "width": image.width, "height": image.height,
            "image": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
