"""Platform-only triage of customer "Report a problem" audit events."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.documents import render_source_page
from app.core.deps import require_platform_admin
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_correction import DocumentCorrection
from app.models.problem_report_review import ProblemReportReview
from app.models.shop import Shop
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_platform_admin)])
ReportStatus = Literal["open", "in_progress", "closed"]


class ReportUpdate(BaseModel):
    status: ReportStatus
    resolution_note: str | None = Field(default=None, max_length=4000)


def _get_report(db: Session, report_id: str) -> AuditLog:
    report = db.query(AuditLog).filter(
        AuditLog.id == report_id, AuditLog.action == "document.reported"
    ).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


def _serialize(report: AuditLog, review: ProblemReportReview | None, db: Session, *, detail: bool = False) -> dict:
    reporter = db.get(User, report.actor_id) if report.actor_id else None
    shop = db.get(Shop, report.shop_id) if report.shop_id else None
    result = {
        "id": report.id,
        "document_id": report.target,
        "shop_id": report.shop_id,
        "shop_name": shop.name if shop else None,
        "reporter_email": reporter.email if reporter else None,
        "reported_at": report.created_at,
        "note": (report.detail or {}).get("note"),
        "snapshot": report.detail or {},
        "status": review.status if review else "open",
        "resolution_note": review.resolution_note if review else None,
        "updated_at": review.updated_at if review else None,
        "closed_at": review.closed_at if review else None,
    }
    if detail:
        doc = db.get(Document, report.target) if report.target else None
        result["document"] = ({
            "id": doc.id, "status": doc.status, "doc_type": doc.doc_type,
            "source_manifest": doc.source_manifest, "payload": doc.payload,
            "source_available": bool(doc.image_ref),
        } if doc and doc.shop_id == report.shop_id else None)
        updates = db.query(AuditLog).filter(
            AuditLog.target == report.id,
            AuditLog.action == "problem_report.updated",
        ).order_by(AuditLog.created_at.asc()).all()
        result["history"] = [{
            "at": entry.created_at,
            "actor_id": entry.actor_id,
            **(entry.detail or {}),
        } for entry in updates]
        if doc and doc.shop_id == report.shop_id:
            events = db.query(AuditLog).filter(
                AuditLog.target == doc.id,
                AuditLog.shop_id == report.shop_id,
                AuditLog.action.in_(("document.submitted", "document.rechecked", "document.edited", "document.retried")),
            ).order_by(AuditLog.created_at.asc()).limit(100).all()
            result["document_events"] = [{
                "at": event.created_at, "action": event.action, "detail": event.detail or {},
            } for event in events]
            edits = db.query(DocumentCorrection).filter(
                DocumentCorrection.document_id == doc.id,
                DocumentCorrection.shop_id == report.shop_id,
            ).order_by(DocumentCorrection.created_at.asc()).limit(100).all()
            result["saved_corrections"] = [{
                "at": edit.created_at, "field": edit.field, "line": edit.line,
                "read": edit.value_read, "corrected": edit.value_corrected,
            } for edit in edits]
            related = db.query(Document).filter(
                Document.shop_id == report.shop_id,
                Document.content_hash == doc.content_hash,
                Document.id != doc.id,
            ).order_by(Document.created_at.desc()).limit(20).all() if doc.content_hash else []
            result["same_file_scans"] = [{
                "id": other.id, "created_at": other.created_at,
                "status": other.status,
                "replayed_fields": ((other.payload or {}).get("meta") or {}).get("exact_file_corrections") or [],
            } for other in related]
    return result


@router.get("/reports")
def list_reports(
    response: Response,
    status: ReportStatus | None = None,
    q: str | None = Query(None, max_length=255),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    query = db.query(AuditLog, ProblemReportReview).outerjoin(
        ProblemReportReview, ProblemReportReview.report_id == AuditLog.id
    ).outerjoin(User, User.id == AuditLog.actor_id).outerjoin(
        Shop, Shop.id == AuditLog.shop_id
    ).filter(AuditLog.action == "document.reported")
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(or_(User.email.ilike(pattern), Shop.name.ilike(pattern), AuditLog.target.ilike(pattern)))
    if status == "open":
        query = query.filter(or_(ProblemReportReview.report_id.is_(None), ProblemReportReview.status == "open"))
    elif status:
        query = query.filter(ProblemReportReview.status == status)
    total = query.count()
    rows = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [_serialize(report, review, db) for report, review in rows]}


@router.get("/reports/{report_id}")
def get_report(report_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    report = _get_report(db, report_id)
    return _serialize(report, db.get(ProblemReportReview, report_id), db, detail=True)


@router.patch("/reports/{report_id}")
def update_report(
    report_id: str, body: ReportUpdate, response: Response, db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    response.headers["Cache-Control"] = "private, no-store"
    report = _get_report(db, report_id)
    review = db.get(ProblemReportReview, report_id)
    old_status = review.status if review else "open"
    note = body.resolution_note.strip() if body.resolution_note is not None else (review.resolution_note if review else None)
    if body.status == "closed" and not note:
        raise HTTPException(status_code=422, detail="A resolution note is required to close a report")
    if review is None:
        review = ProblemReportReview(report_id=report_id)
        db.add(review)
    review.status = body.status
    review.resolution_note = note or None
    review.updated_by = admin.id
    review.closed_at = datetime.now(timezone.utc) if body.status == "closed" else None
    db.add(AuditLog(
        shop_id=report.shop_id, actor_id=admin.id, action="problem_report.updated",
        target=report.id,
        detail={"from": old_status, "to": body.status, "resolution_note": review.resolution_note},
    ))
    db.commit()
    db.refresh(review)
    return _serialize(report, review, db, detail=True)


@router.get("/reports/{report_id}/source")
def report_source_page(
    report_id: str, page: int = Query(1, ge=1),
    view: Literal["processed", "original"] = "processed",
    db: Session = Depends(get_db),
):
    report = _get_report(db, report_id)
    doc = db.get(Document, report.target) if report.target else None
    if doc is None or doc.shop_id != report.shop_id:
        raise HTTPException(status_code=410, detail="The reported source file is unavailable")
    return render_source_page(doc, page, view)
