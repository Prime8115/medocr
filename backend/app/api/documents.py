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
from app.models.job import OcrJob
from app.models.user import User
from app.schemas.connector import DeliveryOut
from app.schemas.document import (
    DocumentOut,
    DocumentReport,
    DocumentReportAck,
    DocumentResponse,
    DocumentUpdate,
    ApproveRequest,
    ChooseRequest,
)
from app.schemas.extraction import validate_fields
from app.services import intake, jobs, lifecycle
from app.services.connectors import service as connector_service
from app.services.inventory.matching import enrich_payload_with_matches
from app.services.ocr import OCRError, process_document
from app.services.ocr.key_pool import next_quota_reset
from app.services.ocr.postprocess import postprocess_fields
from app.services.ocr.choices import pending as pending_choices
from app.services.ocr.verify import open_checks, reverify
from app.services.supplier_choices import apply_remembered, decide
from app.services.supplier_coverage import arrival as arrival_snapshot
from app.services.supplier_coverage import coverage as supplier_coverage
from app.services.supplier_labels import apply_learned, changed_paths, learn_from_edit
from app.services.telemetry import extraction_health, health_warnings
from app.services.storage import storage

router = APIRouter()

log = logging.getLogger(__name__)

ALLOWED_DOC_TYPES = {"prescription", "invoice"}

# Bounds how many scans are read at once in this process, so a burst of uploads
# queues here instead of hammering the AI all at once.
_ocr_semaphore = threading.Semaphore(settings.ocr_max_concurrent_jobs)


# Marks a scan put back in the queue because the AI was busy, so the app can say
# it is waiting rather than reading.
WAITING_PROGRESS = "waiting"


def _requeue_delay(attempt: int) -> float:
    return min(
        settings.ocr_busy_requeue_max_delay,
        settings.ocr_busy_requeue_base_delay * (2 ** attempt),
    )


def run_job(job_id: str) -> None:
    """Read one queued scan. Safe to call from anywhere, any number of times,
    in any number of processes: only the caller that claims the job does the
    work.

    Called straight after an upload (so a scan starts at once) and by the worker
    for anything still due - a busy-AI retry whose wait is over, or a scan whose
    process died and whose lease ran out. The work lives in the ocr_jobs table,
    so a restart or deploy can no longer lose it.
    """
    # A slot first, then the claim: a job left waiting for a slot stays in the
    # queue for the worker, rather than being held by a thread doing nothing.
    if not _ocr_semaphore.acquire(timeout=120):
        return
    db = SessionLocal()
    try:
        if jobs.claim(db, job_id):
            _process_job(db, db.get(OcrJob, job_id))
    finally:
        jobs.let_go(job_id)
        db.close()
        _ocr_semaphore.release()


def _process_job(db: Session, job: OcrJob) -> None:
    job_id, document_id = job.id, job.document_id
    doc = db.get(Document, document_id)
    if not doc:
        jobs.finish(db, job_id, ok=False, error="document no longer exists")
        db.commit()
        return

    # A scan whose process died on it this many times running is taking the
    # process down with it (out of memory on a huge file, say). Stop retrying it.
    if job.attempts > settings.ocr_job_max_attempts:
        cause = f"Gave up after {job.attempts - 1} attempts that never finished (the process died each time)"
        log.error("document %s: %s", document_id, cause)
        if jobs.finish(db, job_id, ok=False, error=cause):
            _mark_failed(db, document_id, FAILED_MESSAGE, cause=cause)
        else:
            db.rollback()
        return

    try:
        data = storage.load(doc.image_ref)
    except Exception as exc:  # noqa: BLE001 - nothing to read, not even locally
        _fail_job(db, job_id, document_id, FAILED_MESSAGE, f"Stored file could not be loaded: {exc}")
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

    failure: Optional[Exception] = None
    try:
        result = process_document(document_id, data, job.content_type, job.doc_type, on_progress=_on_progress)
    except OCRError as exc:
        db.rollback()
        if exc.kind == "busy" and _requeue_when_busy(db, job_id, exc):
            return
        failure = exc
    except Exception as exc:  # noqa: BLE001 — never leave "processing"
        db.rollback()
        log.exception("document %s: extraction crashed", document_id)
        failure = exc

    cause = None
    if failure is not None:
        # The AI could not read it. Never a dead end: read its text ourselves
        # and hand it to the pharmacist to complete, flagged for manual entry.
        cause = str(failure) if isinstance(failure, OCRError) else f"Unexpected error: {failure}"
        result = _manual_entry(document_id, data, job, cause)
        if result is None:
            _fail_job(db, job_id, document_id, _public_message(failure), cause)
            return

    try:
        # Result and job close in one transaction, and only if this process
        # still holds the job - otherwise whoever took it over owns the outcome.
        if not jobs.finish(db, job_id, ok=True, error=cause):
            db.rollback()
            log.warning("document %s: lease lost while reading; result discarded", document_id)
            return
        doc = db.get(Document, document_id)
        # What this shop has taught us about this supplier: where it prints the
        # fields reviewers had to fill in, and the choices they made.
        if result.get("doc_type") == "invoice":
            result = apply_learned(db, doc.shop_id, "invoice", result)
            result = apply_remembered(db, doc.shop_id, "invoice", result)
            # How it looked before anyone touched it - for the supplier report.
            result.setdefault("meta", {})["arrival"] = arrival_snapshot(result.get("meta"))
        doc.payload = result
        doc.doc_type = result.get("doc_type", doc.doc_type)
        doc.overall_confidence = (result.get("meta") or {}).get("overall_confidence")
        doc.status = lifecycle.NEEDS_REVIEW
        doc.progress = None
        doc.error = None
        if cause:
            db.add(AuditLog(shop_id=doc.shop_id, actor_id=None, action="document.manual_entry",
                            target=doc.id, detail={"cause": cause[:2000]}))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("document %s: could not save the result", document_id)
        _fail_job(db, job_id, document_id, FAILED_MESSAGE, f"Could not save the result: {exc}")


def _manual_entry(document_id: str, data: bytes, job: OcrJob, cause: str) -> Optional[dict]:
    """The manual-entry fallback, or None when it is off or itself fails."""
    if not settings.ocr_fallback_enabled:
        return None
    from app.services.ocr.fallback import fallback_payload

    try:
        return fallback_payload(document_id, data, job.content_type, job.doc_type, cause)
    except Exception:  # noqa: BLE001
        log.exception("document %s: manual-entry fallback failed too", document_id)
        return None


def _fail_job(db: Session, job_id: str, document_id: str, message: str, cause: str) -> None:
    log.warning("document %s failed: %s", document_id, cause)
    if jobs.finish(db, job_id, ok=False, error=cause):
        _mark_failed(db, document_id, message, cause=cause)
    else:
        db.rollback()


def _requeue_when_busy(db: Session, job_id: str, exc: OCRError) -> bool:
    """Put a scan the AI was too busy to read back in the queue, due after a
    wait. Returns False when it has already waited as long as we allow - or
    when the day's AI quota is gone and waiting for it is switched off
    (OCR_DAILY_QUOTA_MODE=manual_entry): the pharmacist gets the scan for manual
    entry now rather than tomorrow."""
    daily = getattr(exc, "daily_quota", False)
    if daily and settings.ocr_daily_quota_mode != "wait":
        return False
    job = db.get(OcrJob, job_id)
    attempt = job.busy_attempts or 0
    if attempt >= settings.ocr_busy_requeue_attempts:
        return False
    doc = db.get(Document, job.document_id)
    if not doc or not lifecycle.can_transition(doc.status, lifecycle.QUEUED):
        return False
    # Never sooner than Gemini said to come back - an earlier try is only refused again.
    delay = max(_requeue_delay(attempt), getattr(exc, "retry_after", None) or 0.0)
    if daily:
        delay = max(delay, next_quota_reset() - time.time())
    if not jobs.defer(db, job_id, delay, str(exc)):
        db.rollback()
        return True  # no longer ours: whoever holds it decides
    doc.status = lifecycle.QUEUED
    doc.progress = WAITING_PROGRESS
    db.commit()
    log.info("document %s: AI busy, re-queued for %.0fs (attempt %d)", doc.id, delay, attempt + 1)
    return True


def run_due_jobs(now=None, limit: int = 50) -> int:
    """Run every job due now (or by `now`), one after another, in this thread.
    How tests stand in for time passing; the worker runs jobs in parallel."""
    db = SessionLocal()
    try:
        ids = jobs.due_job_ids(db, limit, now=now)
    finally:
        db.close()
    ran = 0
    for job_id in ids:
        db = SessionLocal()
        try:
            if jobs.claim(db, job_id, now=now):
                with _ocr_semaphore:
                    _process_job(db, db.get(OcrJob, job_id))
                ran += 1
        finally:
            jobs.let_go(job_id)
            db.close()
    return ran


def _enqueue_and_start(
    db: Session, background_tasks: BackgroundTasks, doc: Document, content_type: str, doc_type: Optional[str]
) -> None:
    job = jobs.enqueue(db, doc.id, content_type, doc_type)
    db.commit()
    # Start at once rather than waiting for the worker's next pass. If this
    # process dies first, the job is still in the table and the worker or the
    # startup recovery picks it up.
    background_tasks.add_task(run_job, job.id)


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
    allow_duplicate: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a document (image/PDF) for OCR. doc_type optional (auto-detected).

    The file is checked first (services/intake.py): one that cannot be read is
    refused now, with the reason, rather than failing later in the queue. The
    same file uploaded again opens the earlier scan unless `allow_duplicate`.
    A PDF holding several invoices becomes one document per invoice.
    """
    if doc_type and doc_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(status_code=400, detail="doc_type must be 'prescription' or 'invoice'.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB.")

    try:
        prepared = intake.prepare(data, file.content_type, file.filename)
    except intake.UploadRejected as exc:
        log.info("upload refused (%s): %s", exc.reason, file.filename)
        raise HTTPException(status_code=400, detail=exc.message)
    if prepared.notes:
        log.info("upload %s: %s", file.filename, "; ".join(prepared.notes))

    if not allow_duplicate:
        earlier = _earlier_scan(db, user.shop_id, prepared.sha256)
        if earlier is not None:
            return DocumentResponse(
                document_id=earlier.id, status=earlier.status, duplicate=True, document_ids=[earlier.id],
                message="This file was already scanned - opening the earlier scan.",
            )

    parts = [prepared.data]
    if prepared.content_type == intake.PDF and settings.upload_split_invoices:
        groups = intake.invoice_groups(prepared.data)
        if len(groups) > 1:
            parts = [intake.pdf_pages(prepared.data, pages) for pages in groups]
            log.info("upload %s: %d invoices in one PDF, one document each", file.filename, len(parts))

    docs = []
    for i, part in enumerate(parts):
        name = prepared.filename if len(parts) == 1 else prepared.filename.replace(".pdf", f"_{i + 1}.pdf")
        doc = Document(
            shop_id=user.shop_id,
            doc_type=doc_type or "prescription",
            requested_doc_type=doc_type,
            status=lifecycle.QUEUED,
            image_ref=storage.save(part, name, prepared.content_type),
            content_hash=prepared.sha256,
            created_by=user.id,
        )
        db.add(doc)
        db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.submitted", target=doc.id))
        docs.append(doc)
    db.commit()

    for doc in docs:
        db.refresh(doc)
        _enqueue_and_start(db, background_tasks, doc, prepared.content_type, doc_type)
    message = None
    if len(docs) > 1:
        message = f"This PDF held {len(docs)} invoices - each is scanned as its own document."
    elif prepared.pages_removed:
        message = f"{prepared.pages_removed} blank page(s) were left out."
    return DocumentResponse(
        document_id=docs[0].id, status=docs[0].status, document_ids=[d.id for d in docs], message=message,
    )


def _earlier_scan(db: Session, shop_id: str, sha256: str) -> Optional[Document]:
    """This shop's earlier scan of the very same file - unless that one failed,
    when scanning it again is the point."""
    return (
        db.query(Document)
        .filter(Document.shop_id == shop_id, Document.content_hash == sha256,
                Document.status != lifecycle.FAILED)
        .order_by(Document.created_at.asc())
        .first()
    )


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
        storage.load(doc.image_ref)  # fail now, not in the background, if it is gone
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=410, detail="Stored image is no longer available.")

    doc.status = lifecycle.QUEUED
    doc.error = None
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.retried", target=doc.id))
    db.commit()

    # The user's own choice, or None to detect again - never doc_type, which for
    # an Auto upload that failed is only a placeholder.
    _enqueue_and_start(
        db, background_tasks, doc, jobs.infer_content_type(doc.image_ref), doc.requested_doc_type
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


@router.get("/suppliers")
def supplier_report(
    days: int = Query(90, ge=1, le=730),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """How well each supplier's bills are read, the ones needing attention first.

    Declared before `/{document_id}` so the literal path wins the route match.
    """
    from app.models.supplier_choice import SupplierChoice
    from app.models.supplier_label import SupplierLabel

    since = datetime.now(timezone.utc) - timedelta(days=days)
    docs = (db.query(Document)
            .filter(Document.shop_id == user.shop_id, Document.doc_type == "invoice",
                    Document.created_at >= since)
            .all())
    edits = (db.query(AuditLog)
             .filter(AuditLog.shop_id == user.shop_id, AuditLog.action == "document.edited",
                     AuditLog.created_at >= since)
             .all())
    labels = db.query(SupplierLabel).filter(SupplierLabel.shop_id == user.shop_id).all()
    choices = db.query(SupplierChoice).filter(SupplierChoice.shop_id == user.shop_id).all()
    return {"window_days": days, "suppliers": supplier_coverage(docs, edits, labels, choices)}


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

    old_payload = doc.payload or {}
    payload = dict(old_payload)
    # Re-run every check against the corrected data, so a fixed figure turns
    # its check green and a mistyped one turns it red.
    payload["meta"] = reverify(doc.doc_type, old_payload, clean)
    payload["fields"] = clean
    # Compared cleaned the same way, so normalising alone is not a "change".
    try:
        old_clean = postprocess_fields(doc.doc_type,
                                       validate_fields(doc.doc_type, old_payload.get("fields") or {}))
    except Exception:  # noqa: BLE001 - an old payload the schema now refuses
        old_clean = old_payload.get("fields") or {}
    changed = changed_paths(old_clean, clean)
    # Where the reviewer found what we missed, so this supplier's next bill is read there.
    learned = (learn_from_edit(db, user.shop_id, old_payload, clean, user.id)
               if doc.doc_type == "invoice" else [])
    doc.payload = payload
    # Editing reopens review; the state machine forbids editing from other states above.
    doc.status = lifecycle.NEEDS_REVIEW
    supplier = (clean.get("supplier") or {})
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.edited", target=doc.id,
                    detail={"changed": changed, "learned": learned,
                            "supplier_gstin": (supplier.get("gstin") or {}).get("value"),
                            "supplier_name": (supplier.get("name") or {}).get("value")}))
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


@router.post("/{document_id}/choose", response_model=DocumentOut)
def choose_option(
    document_id: str,
    body: ChooseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Answer one of the bill's ambiguous fields - see services/ocr/choices.py."""
    doc = _get_owned_document(document_id, db, user)
    if doc.status not in (lifecycle.NEEDS_REVIEW, lifecycle.APPROVED):
        raise HTTPException(status_code=409, detail=f"Cannot change a document in '{doc.status}' state.")
    try:
        doc.payload = decide(db, user.shop_id, doc.doc_type, doc.payload or {}, body.choice,
                             body.option, user.id, remember=body.remember)
    except (StopIteration, IndexError, KeyError):
        raise HTTPException(status_code=422, detail="No such choice or option on this document.")
    doc.status = lifecycle.NEEDS_REVIEW
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="document.choice_made",
                    target=doc.id, detail={"choice": body.choice, "option": body.option,
                                           "remember": body.remember}))
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{document_id}/approve", response_model=DocumentOut)
def approve_document(
    document_id: str,
    body: Optional[ApproveRequest] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _get_owned_document(document_id, db, user)
    try:
        lifecycle.ensure_transition(doc.status, lifecycle.APPROVED)
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Nothing is approved unseen. Every check that failed on this document must
    # be acknowledged by the reviewer - "I checked this against the paper" - and
    # each acknowledgement is recorded with who made it, and when.
    meta = (doc.payload or {}).get("meta") or {}
    # A choice is a decision, not a confirmation: it cannot be ticked away.
    undecided = pending_choices(meta)
    if undecided:
        names = "; ".join(c["label"] for c in undecided)
        raise HTTPException(status_code=409, detail={
            "message": f"Choose before approving: {names}.",
            "open_choices": [{"id": c["id"], "label": c["label"]} for c in undecided],
            "open_checks": [],
        })
    verification = meta.get("verification")
    acknowledged = set((body.acknowledged if body else []) or [])
    still_open = [c for c in open_checks(verification)
                  if c["id"] not in acknowledged and not c["id"].startswith("choice_")]
    if still_open:
        names = "; ".join(c["label"] for c in still_open)
        raise HTTPException(status_code=409, detail={
            "message": f"{len(still_open)} check(s) must be confirmed against the paper "
                       f"before approving: {names}.",
            "open_checks": still_open,
        })
    newly = [c for c in open_checks(verification) if c["id"] in acknowledged]
    if newly:
        import copy

        payload = copy.deepcopy(doc.payload)
        record = payload["meta"]["verification"].setdefault("acknowledged", [])
        stamp = datetime.now(timezone.utc).isoformat()
        for check in newly:
            record.append({"id": check["id"], "by": user.id, "at": stamp})
            db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id,
                            action="document.check_acknowledged",
                            target=doc.id, detail={"check": check["id"], "label": check["label"]}))
        doc.payload = payload
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
