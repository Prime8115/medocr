"""MediScan's platform admin: every organisation, its plan and its status.
(docs/ORG_USER_MANAGEMENT_PRD.md, section 5.3)"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.deps import require_platform_admin
from app.core.security import hash_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.shop import Shop
from app.models.user import User
from app.services import licensing
from app.services.licensing import ADMIN

router = APIRouter()


class NewOrg(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    plan: str = licensing.DEFAULT_PLAN
    valid_until: Optional[date] = None
    workspace_name: Optional[str] = None
    admin_email: EmailStr
    admin_password: str = Field(min_length=8)


class OrgChange(BaseModel):
    plan: Optional[str] = None
    valid_until: Optional[date] = None
    clear_valid_until: bool = False
    is_active: Optional[bool] = None


def _check_plan(plan: Optional[str]) -> None:
    if plan is not None and plan not in licensing.PLANS:
        raise HTTPException(status_code=422, detail=f"Unknown plan. Plans: {', '.join(licensing.PLANS)}.")


@router.get("/plans")
def plans(_: User = Depends(require_platform_admin)):
    return licensing.PLANS


@router.get("")
def list_orgs(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    out = []
    for org in db.query(Organization).order_by(Organization.created_at.desc()).all():
        row = licensing.summary(db, org)
        admin = db.query(User).filter(User.org_id == org.id, User.role == ADMIN).order_by(User.created_at).first()
        row["admin_email"] = admin.email if admin else None
        row["created_at"] = org.created_at.isoformat() if org.created_at else None
        out.append(row)
    return out


@router.post("", status_code=201)
def create_org(body: NewOrg, db: Session = Depends(get_db), me: User = Depends(require_platform_admin)):
    _check_plan(body.plan)
    if db.query(User).filter(User.email == body.admin_email).first():
        raise HTTPException(status_code=400, detail="This email is already in use.")
    org = Organization(name=body.name.strip(), plan=body.plan, valid_until=body.valid_until, is_active=True)
    db.add(org)
    db.flush()
    shop = Shop(name=(body.workspace_name or body.name).strip(), org_id=org.id)
    db.add(shop)
    db.flush()
    admin = User(email=body.admin_email, hashed_password=hash_password(body.admin_password), role=ADMIN,
                 org_id=org.id, shop_id=shop.id, is_active=True)
    db.add(admin)
    db.flush()
    licensing.set_workspaces(db, admin, [shop.id])
    db.add(AuditLog(shop_id=shop.id, actor_id=me.id, action="platform.org_created", target=org.id,
                    detail={"name": org.name, "plan": org.plan, "admin": body.admin_email}))
    db.commit()
    return licensing.summary(db, org)


@router.patch("/{org_id}")
def change_org(org_id: str, body: OrgChange, db: Session = Depends(get_db),
               me: User = Depends(require_platform_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    _check_plan(body.plan)
    changed = {}
    if body.plan is not None and body.plan != org.plan:
        changed["plan"] = [org.plan, body.plan]
        org.plan = body.plan
    if body.clear_valid_until and org.valid_until is not None:
        changed["valid_until"] = [org.valid_until.isoformat(), None]
        org.valid_until = None
    elif body.valid_until is not None and body.valid_until != org.valid_until:
        changed["valid_until"] = [org.valid_until.isoformat() if org.valid_until else None,
                                  body.valid_until.isoformat()]
        org.valid_until = body.valid_until
    if body.is_active is not None and body.is_active != org.is_active:
        changed["is_active"] = body.is_active
        org.is_active = body.is_active
    if changed:
        first = db.query(Shop).filter(Shop.org_id == org.id).first()
        db.add(AuditLog(shop_id=first.id if first else me.shop_id, actor_id=me.id, action="platform.org_changed",
                        target=org.id, detail=changed))
    db.commit()
    return licensing.summary(db, org)
