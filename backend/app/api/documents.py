"""Document endpoints — DB-backed, authenticated, shop-scoped, with a real
lifecycle state machine and human-correction (PATCH) support.
"""
import time
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user
from app.database import SessionLocal, get_db
from app.models.audit_log import AuditLog
from app.models.connector import Connector
from app.models.document import Document
from app.models.inventory import InventoryItem
from app.models.user import User
from app.schemas.connector import DeliveryOut
from app.schemas.document import DocumentOut, DocumentResponse, DocumentUpdate
from app.schemas.extraction import validate_fields
from app.services import lifecycle
from app.services.connectors import service as connector_service
from app.services.inventory.matching import enrich_payload_with_matches
from app.services.ocr import OCRError, process_document
from app.services.ocr.postprocess import postprocess_fields
from app.services.storage import storage

router = APIRouter()

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
ALLOWED_DOC_TYPES = {"prescription", "invoice"}


def _run_ocr_job(document_id: str, data: bytes, content_type: str, doc_type: Optional[str]):
    """Background OCR task with its own DB session. Never leaves a doc stuck."""
    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if not doc:
            return
        if lifecycle.can_transition(doc.status, lifecycle.PROCESSING):
            doc.status = lifecycle.PROCESSING
            db.commit()

        # Persist progress for long PDFs so the app can show "page 12/60".
        def _on_progress(done: int, total: int):
            if total > 1:
                d = db.get(Document, document_id)
                if d:
                    d.progress = f"{done}/{total}"
                    db.commit()

        result = process_document(document_id, data, content_type, doc_type, on_progress=_on_progress)

        doc = db.get(Document, document_id)
        if not doc:
            return
        doc.payload = result
        doc.doc_type = result.get("doc_type", doc.doc_type)
        doc.overall_confidence = (result.get("meta") or {}).get("overall_confidence")
        doc.status = lifecycle.NEEDS_REVIEW
        doc.progress = None
        doc.error = None
        db.commit()
    except OCRError as exc:
        _mark_failed(db, document_id, str(exc))
    except Exception as exc:  # noqa: BLE001 — never leave "processing"
        _mark_failed(db, document_id, f"Unexpected error: {exc}")
    finally:
        db.close()


def _mark_failed(db: Session, document_id: str, message: str):
    doc = db.get(Document, document_id)
    if doc:
        doc.status = lifecycle.FAILED
        doc.error = message
        db.commit()


@router.post("/", response_model=DocumentResponse)
async def submit_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a document (image/PDF) for OCR. doc_type optional (auto-detected)."""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Use JPEG/PNG/WebP/PDF.")
    if doc_type and doc_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(status_code=400, detail="doc_type must be 'prescription' or 'invoice'.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB.")

    image_ref = storage.save(data, file.filename or "upload", file.content_type)

    doc = Document(
        shop_id=user.shop_id,
        doc_type=doc_type or "prescription",
        status=lifecycle.QUEUED,
        image_ref=image_ref,
        created_by=user.id,
    )
    db.add(doc)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.submitted", target=doc.id))
    db.commit()
    db.refresh(doc)

    background_tasks.add_task(_run_ocr_job, doc.id, data, file.content_type, doc_type)
    return DocumentResponse(document_id=doc.id, status=doc.status)


def _infer_content_type(ref: str) -> str:
    r = (ref or "").lower()
    if r.endswith(".pdf"):
        return "application/pdf"
    if r.endswith(".png"):
        return "image/png"
    if r.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


@router.post("/{document_id}/retry", response_model=DocumentResponse)
def retry_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-run OCR on a document's stored image (e.g. after an 'AI busy' failure)
    without needing to photograph it again."""
    doc = _get_owned_document(document_id, db, user)
    if doc.status not in (lifecycle.FAILED, lifecycle.NEEDS_REVIEW):
        raise HTTPException(status_code=409, detail="Only failed or unreviewed documents can be re-run.")
    if not doc.image_ref:
        raise HTTPException(status_code=400, detail="No stored image to re-process.")

    try:
        data = storage.load(doc.image_ref)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=410, detail="Stored image is no longer available.")

    doc.status = lifecycle.QUEUED
    doc.error = None
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.retried", target=doc.id))
    db.commit()

    background_tasks.add_task(
        _run_ocr_job, doc.id, data, _infer_content_type(doc.image_ref), doc.doc_type
    )
    return DocumentResponse(document_id=doc.id, status=lifecycle.QUEUED)


@router.get("/", response_model=list[DocumentOut])
def list_documents(
    status_filter: Optional[str] = Query(None, alias="status"),
    doc_type: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List documents for the caller's shop only (tenancy enforced)."""
    q = db.query(Document).filter(Document.shop_id == user.shop_id)
    if status_filter:
        q = q.filter(Document.status == status_filter)
    if doc_type:
        q = q.filter(Document.doc_type == doc_type)
    return q.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()


def _get_owned_document(document_id: str, db: Session, user: User) -> Document:
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.shop_id == user.shop_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_owned_document(document_id, db, user)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: str,
    body: DocumentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Persist human corrections to the extracted fields."""
    doc = _get_owned_document(document_id, db, user)
    if doc.status not in (lifecycle.NEEDS_REVIEW, lifecycle.APPROVED, lifecycle.PUSHED):
        raise HTTPException(status_code=409, detail=f"Cannot edit a document in '{doc.status}' state.")

    try:
        clean = validate_fields(doc.doc_type, body.fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    clean = postprocess_fields(doc.doc_type, clean)

    payload = dict(doc.payload or {})
    payload["fields"] = clean
    doc.payload = payload
    # Editing reopens review; the state machine forbids editing from other states above.
    doc.status = lifecycle.NEEDS_REVIEW
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.edited", target=doc.id))
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{document_id}/approve", response_model=DocumentOut)
def approve_document(document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = _get_owned_document(document_id, db, user)
    try:
        lifecycle.ensure_transition(doc.status, lifecycle.APPROVED)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    doc.status = lifecycle.APPROVED

    # Link line items to the shop's inventory (attach matched SKUs) so the pushed
    # data can update their stock directly.
    inv_items = db.query(InventoryItem).filter(InventoryItem.shop_id == user.shop_id).all()
    if inv_items and doc.payload:
        import copy

        payload = copy.deepcopy(doc.payload)  # deep copy so SQLAlchemy detects the change
        linked = enrich_payload_with_matches(payload, inv_items)
        if linked:
            payload.setdefault("meta", {})["inventory_linked"] = linked
        doc.payload = payload

    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.approved", target=doc.id))
    db.commit()
    db.refresh(doc)
    return doc


class PushResult(DocumentOut):
    deliveries: list[DeliveryOut] = []


@router.post("/{document_id}/push", response_model=PushResult)
def push_document(document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Push approved data to the shop's external software via all enabled connectors.

    Idempotent per (document, connector): already-delivered/queued connectors are
    skipped, previously-failed ones are retried. The document becomes 'pushed' only
    when every delivery succeeded or was queued for an agent.
    """
    doc = _get_owned_document(document_id, db, user)
    if doc.status not in (lifecycle.APPROVED, lifecycle.PUSHED):
        raise HTTPException(status_code=409, detail="Document must be approved before pushing.")

    has_connector = (
        db.query(Connector)
        .filter(Connector.shop_id == user.shop_id, Connector.enabled.is_(True))
        .count()
    )
    if has_connector == 0:
        raise HTTPException(
            status_code=400,
            detail="No connectors configured. Add one in Settings before pushing.",
        )

    deliveries = connector_service.push_document(db, doc)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.push_attempted", target=doc.id))

    if connector_service.all_ok(deliveries):
        if lifecycle.can_transition(doc.status, lifecycle.PUSHED) or doc.status == lifecycle.PUSHED:
            doc.status = lifecycle.PUSHED
    db.commit()
    db.refresh(doc)

    out = PushResult.model_validate(doc)
    out.deliveries = [DeliveryOut.model_validate(d) for d in deliveries]
    return out
