"""Shared test fixtures: in-memory DB + authenticated TestClient."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.documents as documents_module
from app.config import settings
from app.database import Base, get_db
from app.main import app


@pytest.fixture()
def db_session(monkeypatch):
    """Fresh in-memory SQLite shared across the app for one test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # The background OCR job opens its own session via SessionLocal — point it at
    # the same in-memory engine so tests observe its writes.
    monkeypatch.setattr(documents_module, "SessionLocal", TestingSession)
    # No background worker in tests: jobs run inline from the upload, and tests
    # that need time to pass call documents_module.run_due_jobs() themselves.
    monkeypatch.setattr(settings, "ocr_worker_enabled", False)

    yield TestingSession

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mock_ocr(monkeypatch):
    """Enable the deterministic mock OCR provider for this test."""
    monkeypatch.setattr(settings, "allow_mock_ocr", True)
    yield


def register_and_login(client, email="owner@shop.com", password="password123", shop="Shop A"):
    """Helper: create a shop+owner and return auth headers."""
    client.post("/v1/auth/register", json={"email": email, "password": password, "shop_name": shop})
    resp = client.post("/v1/auth/login", data={"username": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def sample_image(marker: str = "") -> bytes:
    """A real, readable PNG - uploads are checked now, so fake bytes are
    refused. Each call gives a different file (uploading the same file twice
    opens the earlier scan). `marker` (e.g. "INVOICE") is written into the
    file where the mock OCR provider looks for it."""
    import io
    import uuid

    from PIL import Image, ImageDraw
    from PIL.PngImagePlugin import PngInfo

    img = Image.new("RGB", (400, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), f"{marker or 'Rx'} {uuid.uuid4().hex[:8]}", fill="black")
    draw.rectangle((20, 60, 380, 280), outline="black", width=3)
    info = PngInfo()
    info.add_text("m", f"{marker} {uuid.uuid4().hex}")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def sample_pdf(*pages: str) -> bytes:
    """A real PDF with a page of text per argument (one unique page if none)."""
    import io
    import uuid

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages or (f"Document {uuid.uuid4().hex}",):
        y = 800
        for line in text.splitlines() or [""]:
            c.drawString(40, y, line)
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()
