"""Document endpoints — DB-backed, authenticated, shop-scoped, with a real
lifecycle state machine and human-correction (PATCH) support.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
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
from app.core.deps import get_current_user, require_owner
from app.database import SessionLocal, get_db
from app.models.audit_log import AuditLog
from app.models.connector import Connector
from app.models.document import Document
from app.models.inventory import InventoryItem
from app.models.user import User
from app.schemas.connector import DeliveryOut
from app.schemas.document import (
    DocumentOut,
    DocumentReport,
    DocumentReportAck,
    DocumentResponse,
    DocumentUpdate,
)
from app.schemas.extraction import validate_fields
from app.services import lifecycle
from app.services.connectors import service as connector_service
from app.services.inventory.matching import enrich_payload_with_matches
from app.services.ocr import OCRError, process_document
from app.services.ocr.postprocess import postprocess_fields
from app.services.telemetry import extraction_health, health_warnings
from app.services.storage import storage

router = APIRouter()

log = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
ALLOWED_DOC_TYPES = {"prescription", "invoice"}

# Regulate concurrent outbound OCR processing (smooth pacing for simultaneous user scans)
_ocr_semaphore = threading.Semaphore(settings.ocr_max_concurrent_jobs)


# Marks a scan put back in the queue because the AI was busy, so the app can say
# it is waiting rather than reading.
WAITING_PROGRESS = "waiting"


def _schedule(delay: float, fn, *args) -> None:
    """Run fn(*args) after `delay` seconds, off the request thread. A seam for
    tests. A scan waiting here when the server restarts is caught by recovery
    and offered for retry, like any other interrupted scan."""
    timer = threading.Timer(delay, fn, args=args)
    timer.daemon = True
    timer.start()


def _requeue_delay(attempt: int) -> float:
    return min(
        settings.ocr_busy_requeue_max_delay,
        settings.ocr_busy_requeue_base_delay * (2 ** attempt),
    )


def _requeue_when_busy(db: Session, document_id: str, args: tuple, attempt: int) -> bool:
    """Put a scan the AI was too busy to read back in the queue. Returns False
    when it has already waited as long as we allow, so it should fail."""
    if attempt >= settings.ocr_busy_requeue_attempts:
        return False
    doc = db.get(Document, document_id)
    if not doc or not lifecycle.can_transition(doc.status, lifecycle.QUEUED):
        return False
    doc.status = lifecycle.QUEUED
    doc.progress = WAITING_PROGRESS
    db.commit()
    delay = _requeue_delay(attempt)
    log.info("document %s: AI busy, re-queued for %.0fs (attempt %d)", document_id, delay, attempt + 1)
    _schedule(delay, _run_ocr_job, *args, attempt + 1)
    return True


def _run_ocr_job(
    document_id: str, data: bytes, content_type: str, doc_type: Optional[str], attempt: int = 0
):
    """Background OCR task with its own DB session and concurrency regulation.

    `attempt` counts how many times the AI was too busy for this scan already."""
    db = SessionLocal()
    acquired = False
    try:
        # Bounded concurrency: wait for a processing slot without crashing or overloading the AI API
        acquired = _ocr_semaphore.acquire(timeout=120)
        doc = db.get(Document, document_id)
        if not doc:
            return
        if lifecycle.can_transition(doc.status, lifecycle.PROCESSING):
            doc.status = lifecycle.PROCESSING
            doc.progress = None  # no longer waiting
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
        if exc.kind == "busy" and _requeue_when_busy(
            db, document_id, (document_id, data, content_type, doc_type), attempt
        ):
            return
        log.warning("document %s failed: %s", document_id, exc)
        _mark_failed(db, document_id, _public_message(exc), cause=str(exc))
    except Exception as exc:  # noqa: BLE001 — never leave "processing"
        log.exception("document %s failed unexpectedly", document_id)
        _mark_failed(db, document_id, _public_message(exc), cause=f"Unexpected error: {exc}")
    finally:
        if acquired:
            _ocr_semaphore.release()
        db.close()


# What the pharmacist sees when a scan fails. The real cause - Gemini's own
# error, a quota, a key - means nothing to them and is not theirs to act on, so
# it goes to the server log and the audit log, never to the app.
# By the time a scan fails as busy it has already waited in the queue for about
# ten minutes, so this is most likely the day's AI quota running out.
BUSY_MESSAGE = "The AI service is busy right now. Please try again in a few minutes."
FAILED_MESSAGE = "We couldn't read this document. Please try again."


def _public_message(exc: Exception) -> str:
    if isinstance(exc, OCRError) and exc.kind == "busy":
        return BUSY_MESSAGE
    return FAILED_MESSAGE


def _mark_failed(db: Session, document_id: str, message: str, cause: Optional[str] = None):
    doc = db.get(Document, document_id)
    if doc:
        doc.status = lifecycle.FAILED
        doc.progress = None
        doc.error = message
        if cause:
            # Kept where an operator can query it after the server log rotates.
            db.add(AuditLog(shop_id=doc.shop_id, actor_id=None, action="document.failed",
                            target=doc.id, detail={"cause": cause[:2000]}))
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
        requested_doc_type=doc_type,
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
        # The user's own choice, or None to detect again - never doc_type, which
        # for an Auto upload that failed is only a placeholder.
        _run_ocr_job, doc.id, data, _infer_content_type(doc.image_ref), doc.requested_doc_type
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


@router.get("/stats")
def extraction_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """How the extraction pipeline is doing for this shop, over a window.

    Declared before `/{document_id}` so the literal path wins the route match.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    docs = (
        db.query(Document)
        .filter(Document.shop_id == user.shop_id, Document.created_at >= since)
        .all()
    )
    reported = {
        row.target
        for row in db.query(AuditLog)
        .filter(
            AuditLog.shop_id == user.shop_id,
            AuditLog.action == "document.reported",
            AuditLog.created_at >= since,
        )
        .all()
        if row.target
    }
    health = extraction_health(docs, reported)
    health["window_days"] = days
    health["warnings"] = health_warnings(health)
    return health


def _get_owned_document(document_id: str, db: Session, user: User) -> Document:
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.shop_id == user.shop_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}/diagnostics")
def document_diagnostics(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
):
    """Why a scan failed, for whoever is diagnosing it - never shown in the app.

    The pharmacist only ever sees a plain message; the real cause (the AI's own
    error, a quota, a validation failure) is kept in the audit log. Reading it
    used to need a shell on the server; this returns it to the shop's owner over
    the API they already log in to. Owner-only, and scoped to their own shop.
    """
    doc = _get_owned_document(document_id, db, user)
    failures = (
        db.query(AuditLog)
        .filter(AuditLog.action == "document.failed", AuditLog.target == doc.id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    )
    meta = (doc.payload or {}).get("meta") or {}
    return {
        "document_id": doc.id,
        "status": doc.status,
        "doc_type": doc.doc_type,
        "requested_doc_type": doc.requested_doc_type,
        "image_ref": doc.image_ref,
        "progress": doc.progress,
        "pipeline": meta.get("pipeline"),
        "failures": [
            {"at": f.created_at.isoformat() if f.created_at else None, "cause": (f.detail or {}).get("cause")}
            for f in failures
        ],
    }


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


@router.post("/{document_id}/report", response_model=DocumentReportAck)
def report_document(
    document_id: str,
    body: DocumentReport,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A user telling us this extraction is wrong.

    Every field complaint so far arrived over WhatsApp and had to be reproduced
    from a description. This records what the pipeline actually produced next to
    the stored file, so a report can be turned straight into a test fixture.
    """
    doc = _get_owned_document(document_id, db, user)
    meta = (doc.payload or {}).get("meta") or {}
    fields = (doc.payload or {}).get("fields") or {}
    note = (body.note or "").strip()[:2000] or None

    detail = {
        "note": note,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "image_ref": doc.image_ref,
        "pipeline": meta.get("pipeline"),
        "pages": meta.get("pages"),
        "item_count": meta.get("item_count"),
        "copies_detected": meta.get("copies_detected"),
        "duplicates_removed": meta.get("duplicates_removed"),
        "stated_item_count": meta.get("stated_item_count"),
        "line_items_total": meta.get("line_items_total"),
        "total_reconciles": meta.get("total_reconciles"),
        "printed_total": ((fields.get("invoice") or {}).get("total_amount") or {}).get("value"),
        "overall_confidence": doc.overall_confidence,
    }
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.reported",
                    target=doc.id, detail=detail))
    db.commit()

    # Loud on purpose: this is the signal we were missing.
    log.warning("document reported as wrong: %s detail=%s", doc.id, detail)
    return DocumentReportAck(
        document_id=doc.id,
        message="Thanks - we've logged this invoice for review.",
    )


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
