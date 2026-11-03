"""Test material for the photo and handwriting checks (test_live_photos.py).

- camera(): turns a clean page into what a phone sends - tilted, in
  perspective, unevenly lit, a little blurred and JPEG-compressed.
- handwritten_bill(): a handwritten pharmacy "kachha" bill, written in a
  handwriting font (Kalam, designed after Indian handwriting, or Caveat - both
  SIL Open Font License, samples/fonts/OFL.txt) with each character varied in
  size and position, on ruled paper. Its values are known exactly.

Deterministic: every image is drawn from a fixed seed, so a run that changes
its score changed the reader, not the picture.
"""
import io
import pathlib
import random
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = pathlib.Path(__file__).parent / "samples" / "fonts"


def camera(page: Image.Image, seed: int, strength: float = 1.0) -> bytes:
    """A photo of the page: angle, perspective, light, blur, JPEG."""
    rnd = random.Random(seed)
    page = page.convert("RGB")
    w, h = page.size
    # On a table: a darker border around the paper.
    pad = int(0.06 * max(w, h))
    table = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (70, 60, 52))
    table.paste(page, (pad, pad))
    # Held at a slight angle.
    table = table.rotate(rnd.uniform(-4, 4) * strength, resample=Image.BICUBIC, expand=False,
                         fillcolor=(70, 60, 52))
    # ...and not quite flat on: a perspective pull at the corners.
    W, H = table.size
    d = lambda: rnd.uniform(0, 0.035 * strength) * W  # noqa: E731
    quad = (d(), d(), d(), H - d(), W - d(), H - d(), W - d(), d())
    table = table.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC)
    # Uneven light: a shadow falling across one side.
    shade = Image.linear_gradient("L").rotate(rnd.choice([0, 90, 180, 270])).resize((W, H))
    shade = shade.point(lambda v: int(255 - v * 0.35 * strength))
    table = Image.composite(table, Image.new("RGB", (W, H), (0, 0, 0)), shade)
    # A hand that is not quite still, and a phone's compression.
    table = table.filter(ImageFilter.GaussianBlur(radius=0.9 * strength))
    table.thumbnail((1600, 1600))
    buf = io.BytesIO()
    table.save(buf, format="JPEG", quality=int(70 - 10 * strength))
    return buf.getvalue()


_PRODUCTS = [
    ("Dolo 650 Tab", "15's"), ("Pan 40 Tab", "15's"), ("Azithral 500 Tab", "5's"),
    ("Augmentin 625 Duo", "10's"), ("Telma 40 Tab", "30's"), ("Glycomet GP 1", "15's"),
    ("Shelcal 500 Tab", "15's"), ("Becosules Cap", "20's"), ("Allegra 120 Tab", "10's"),
    ("Montair LC Tab", "10's"), ("Thyronorm 50", "100's"), ("Rosuvas 10 Tab", "15's"),
    ("Ecosprin 75", "14's"), ("Cetzine Tab", "10's"), ("Liv 52 DS Tab", "60's"),
]
_SHOPS = ["SHREE GANESH MEDICAL AGENCIES", "MAHALAXMI PHARMA DISTRIBUTORS", "NEW SAI MEDICOS",
          "OM SHANTI DRUG HOUSE"]
_BUYERS = ["Eastern Agencies Healthcare", "City Care Chemists", "Jeevan Medical Stores", "Apna Pharmacy"]


def _scribble(draw: ImageDraw.ImageDraw, xy: Tuple[float, float], text: str, font_path: pathlib.Path,
              size: int, rnd: random.Random, ink=(25, 35, 90)) -> float:
    """Write `text` by hand: every character its own size, height and spacing.
    Returns the x where the writing ends."""
    x, y = xy
    for ch in text:
        font = ImageFont.truetype(str(font_path), size + rnd.randint(-2, 2))
        draw.text((x, y + rnd.uniform(-1.5, 1.5)), ch, font=font, fill=ink)
        x += draw.textlength(ch, font=font) * rnd.uniform(0.95, 1.08)
    return x


def handwritten_bill(seed: int) -> Tuple[Image.Image, Dict]:
    """A handwritten pharmacy bill and its true values."""
    rnd = random.Random(seed)
    font = FONTS / ("Kalam-Regular.ttf" if seed % 2 == 0 else "Caveat.ttf")
    size = 34 if "Kalam" in font.name else 42
    W, H = 1240, 1754   # A4 at 150 dpi
    page = Image.new("RGB", (W, H), (250, 248, 238))
    draw = ImageDraw.Draw(page)
    for y in range(330, H - 120, 58):   # ruled paper
        draw.line((60, y, W - 60, y), fill=(190, 205, 225), width=2)
    draw.line((150, 300, 150, H - 120), fill=(230, 160, 160), width=2)

    shop, buyer = rnd.choice(_SHOPS), rnd.choice(_BUYERS)
    bill_no = f"{rnd.randint(101, 989)}"
    date = f"{rnd.randint(1, 28):02d}/{rnd.randint(1, 12):02d}/2025"
    lines = []
    for name, pack in rnd.sample(_PRODUCTS, rnd.randint(4, 7)):
        qty = rnd.choice([2, 3, 5, 10, 12, 20, 25])
        rate = round(rnd.uniform(18, 420) * 4) / 4
        lines.append({"name": name, "pack": pack, "batch": f"{rnd.choice('ABDEGKLMPST')}{rnd.randint(1000, 99999)}",
                      "expiry": f"{rnd.randint(1, 12):02d}/{rnd.randint(26, 29)}", "qty": qty,
                      "rate": rate, "amount": round(qty * rate, 2)})
    total = round(sum(l["amount"] for l in lines), 2)

    # Printed letterhead, as on a bill book; the rest is written by hand.
    head = ImageFont.load_default(size=40)
    draw.text((W // 2, 70), shop, font=head, fill=(150, 30, 30), anchor="mm")
    draw.text((W // 2, 120), "Wholesale Chemists & Druggists  -  CASH / CREDIT MEMO",
              font=ImageFont.load_default(size=24), fill=(150, 30, 30), anchor="mm")
    _scribble(draw, (70, 190), f"Bill No. {bill_no}", font, size, rnd)
    _scribble(draw, (W - 400, 190), f"Date {date}", font, size, rnd)
    _scribble(draw, (70, 245), f"M/s {buyer}", font, size, rnd)
    xs = (165, 560, 720, 860, 960, 1090)
    for col, x in zip(("Item", "Batch", "Exp", "Qty", "Rate", "Amount"), xs):
        draw.text((x, 300), col, font=ImageFont.load_default(size=24), fill=(150, 30, 30))
    y = 345
    for i, l in enumerate(lines, 1):
        _scribble(draw, (75, y), f"{i}.", font, size, rnd)
        _scribble(draw, (xs[0], y), f"{l['name']} {l['pack']}", font, size, rnd)
        _scribble(draw, (xs[1], y), l["batch"], font, size - 4, rnd)
        _scribble(draw, (xs[2], y), l["expiry"], font, size - 4, rnd)
        _scribble(draw, (xs[3], y), str(l["qty"]), font, size, rnd)
        _scribble(draw, (xs[4], y), f"{l['rate']:.2f}", font, size - 2, rnd)
        _scribble(draw, (xs[5], y), f"{l['amount']:.2f}", font, size - 2, rnd)
        y += 58
    y += 30
    _scribble(draw, (xs[3] - 40, y), f"Total  Rs. {total:.2f}", font, size + 4, rnd)
    return page, {"bill_no": bill_no, "date": date, "total": total, "lines": lines}
