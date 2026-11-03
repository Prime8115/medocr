"""The organisation's own admin: its users, their workspaces, its licence.

See docs/ORG_USER_MANAGEMENT_PRD.md. "owner" is an organisation admin,
"staff" a member. Every change is in the audit log of the workspace it was
made from.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_owner
from app.core.security import hash_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.shop import Shop
from app.models.user import User
from app.services import licensing
from app.services.licensing import ADMIN, MEMBER, LicenceError

router = APIRouter()


def _org(db: Session, user: User) -> Organization:
    org = db.get(Organization, user.org_id) if user.org_id else None
    if not org:
        raise HTTPException(status_code=404, detail="No organisation for this account.")
    return org


def _audit(db: Session, user: User, action: str, target: str, **detail) -> None:
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action=action, target=target, detail=detail or None))


def _user_out(db: Session, u: User, me: User) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": bool(u.is_active),
            "workspace_ids": licensing.workspace_ids_of(db, u), "current_workspace_id": u.shop_id,
            "created_at": u.created_at.isoformat() if u.created_at else None, "is_self": u.id == me.id}


def _workspaces_in_org(db: Session, org: Organization, ids: List[str]) -> List[str]:
    found = {s.id for s in db.query(Shop).filter(Shop.org_id == org.id, Shop.id.in_(ids or [""]))}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=422, detail="A workspace given is not in this organisation.")
    return list(dict.fromkeys(ids))


@router.get("")
def organisation(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The organisation, its plan, what it uses and what it may."""
    org = _org(db, user)
    out = licensing.summary(db, org)
    out["plans"] = licensing.PLANS
    out["is_admin"] = user.role == ADMIN
    return out


# --- users ---------------------------------------------------------------------------

class NewUser(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default=MEMBER, pattern="^(owner|staff)$")
    workspace_ids: List[str] = []


class UserChange(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(owner|staff)$")
    workspace_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None


class NewPassword(BaseModel):
    new_password: str = Field(min_length=8)


@router.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_owner)):
    org = _org(db, user)
    users = db.query(User).filter(User.org_id == org.id).order_by(User.created_at).all()
    return [_user_out(db, u, user) for u in users]


@router.post("/users", status_code=201)
def add_user(body: NewUser, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    org = _org(db, user)
    try:
        licensing.check_can_add_user(db, org)
    except LicenceError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="This email is already in use.")
    ids = _workspaces_in_org(db, org, body.workspace_ids)
    if body.role == MEMBER and not ids:
        raise HTTPException(status_code=422, detail="Give a member at least one workspace.")
    new = User(email=body.email, hashed_password=hash_password(body.password), role=body.role,
               org_id=org.id, shop_id=(ids[0] if ids else user.shop_id), is_active=True)
    db.add(new)
    db.flush()
    licensing.set_workspaces(db, new, ids)
    _audit(db, user, "org.user_added", new.id, email=body.email, role=body.role, workspaces=ids)
    db.commit()
    return _user_out(db, new, user)


@router.patch("/users/{user_id}")
def change_user(user_id: str, body: UserChange, db: Session = Depends(get_db),
                user: User = Depends(require_owner)):
    org = _org(db, user)
    target = db.query(User).filter(User.id == user_id, User.org_id == org.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    losing_admin = target.role == ADMIN and target.is_active and (
        (body.role is not None and body.role != ADMIN) or body.is_active is False)
    if losing_admin and licensing.active_admins(db, org.id) <= 1:
        raise HTTPException(status_code=409, detail="The organisation must keep at least one active admin.")
    if body.is_active is False and target.id == user.id:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account.")
    if body.is_active is True and not target.is_active:
        try:
            licensing.check_can_add_user(db, org)
        except LicenceError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    changed = {}
    if body.role is not None and body.role != target.role:
        changed["role"] = body.role
        target.role = body.role
    if body.workspace_ids is not None:
        ids = _workspaces_in_org(db, org, body.workspace_ids)
        if target.role == MEMBER and not ids:
            raise HTTPException(status_code=422, detail="A member needs at least one workspace.")
        licensing.set_workspaces(db, target, ids)
        changed["workspaces"] = ids
    if target.role == MEMBER:
        # A member's current workspace is always one they may open.
        allowed = licensing.workspace_ids_of(db, target) or [target.shop_id]
        if target.shop_id not in allowed:
            target.shop_id = allowed[0]
    if body.is_active is not None and body.is_active != target.is_active:
        changed["is_active"] = body.is_active
        target.is_active = body.is_active
    if changed:
        _audit(db, user, "org.user_changed", target.id, email=target.email, **changed)
    db.commit()
    return _user_out(db, target, user)


@router.post("/users/{user_id}/password")
def reset_password(user_id: str, body: NewPassword, db: Session = Depends(get_db),
                   user: User = Depends(require_owner)):
    org = _org(db, user)
    target = db.query(User).filter(User.id == user_id, User.org_id == org.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    target.hashed_password = hash_password(body.new_password)
    _audit(db, user, "org.user_password_set", target.id, email=target.email)
    db.commit()
    return {"status": "ok"}


# --- workspaces ----------------------------------------------------------------------

class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


def _workspace_out(db: Session, s: Shop) -> dict:
    from app.models.organization import UserWorkspace

    members = db.query(UserWorkspace).filter(UserWorkspace.shop_id == s.id).count()
    return {"id": s.id, "name": s.name, "member_count": members}


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The workspaces this user may open - every one, for an admin."""
    return [_workspace_out(db, s) for s in licensing.accessible_workspaces(db, user)]


@router.post("/workspaces", status_code=201)
def add_workspace(body: WorkspaceIn, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    org = _org(db, user)
    try:
        licensing.check_can_add_workspace(db, org)
    except LicenceError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    shop = Shop(name=body.name.strip(), org_id=org.id)
    db.add(shop)
    db.flush()
    _audit(db, user, "org.workspace_added", shop.id, name=shop.name)
    db.commit()
    return _workspace_out(db, shop)


@router.patch("/workspaces/{workspace_id}")
def rename_workspace(workspace_id: str, body: WorkspaceIn, db: Session = Depends(get_db),
                     user: User = Depends(require_owner)):
    org = _org(db, user)
    shop = db.query(Shop).filter(Shop.id == workspace_id, Shop.org_id == org.id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    old, shop.name = shop.name, body.name.strip()
    _audit(db, user, "org.workspace_renamed", shop.id, old=old, new=shop.name)
    db.commit()
    return _workspace_out(db, shop)
