"""Authentication endpoints: register an organisation with its first
workspace and admin, login, current user, switch workspace."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.models.shop import Shop
from app.models.user import User
from app.schemas.auth import PasswordChange, Token, UserCreate, UserOut

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Create an organisation (one seat), its first workspace and its admin."""
    from app.services import licensing

    email = str(payload.email).strip().lower()
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    org = Organization(name=payload.shop_name, plan=licensing.DEFAULT_PLAN, is_active=True)
    db.add(org)
    db.flush()
    shop = Shop(name=payload.shop_name, org_id=org.id)
    db.add(shop)
    db.flush()  # assign shop.id

    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        shop_id=shop.id,
        org_id=org.id,
        role="owner",
    )
    db.add(user)
    db.flush()
    licensing.set_workspaces(db, user, [shop.id])
    db.add(AuditLog(shop_id=shop.id, actor_id=user.id, action="shop.registered", target=shop.id))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == form.username.strip().lower()).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    org = db.get(Organization, user.org_id) if user.org_id else None
    if org and not org.is_active and not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="This organisation is inactive. Contact MediScan support.")
    token = create_access_token(user.id, user.shop_id, user.role)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The user, their organisation, the workspace they are in and the ones
    they may open."""
    from app.services import licensing

    org = db.get(Organization, user.org_id) if user.org_id else None
    spaces = licensing.accessible_workspaces(db, user)
    current = next((s for s in spaces if s.id == user.shop_id), None) or db.get(Shop, user.shop_id)
    return UserOut(id=user.id, email=user.email, role=user.role, shop_id=user.shop_id,
                   is_platform_admin=bool(user.is_platform_admin), org_id=user.org_id,
                   org_name=org.name if org else None, workspace_name=current.name if current else None,
                   workspaces=[{"id": s.id, "name": s.name} for s in spaces])


class WorkspaceSwitch(BaseModel):
    workspace_id: str


class WorkspaceSession(BaseModel):
    access_token: str
    user: UserOut


@router.post("/workspace", response_model=WorkspaceSession)
def switch_workspace(body: WorkspaceSwitch, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Select a workspace for this session without moving the user's other devices."""
    from app.services import licensing

    if body.workspace_id not in {s.id for s in licensing.accessible_workspaces(db, user)}:
        raise HTTPException(status_code=403, detail="You do not have access to that workspace.")
    if body.workspace_id != user.shop_id:
        db.add(AuditLog(shop_id=body.workspace_id, actor_id=user.id, action="user.workspace_switched",
                        target=user.id, detail={"from": user.shop_id}))
        db.commit()
    selected = type(user)(user.record, body.workspace_id)
    return WorkspaceSession(access_token=create_access_token(user.id, body.workspace_id, user.role),
                            user=me(db, selected))


@router.post("/change-password")
def change_password(
    body: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="user.password_changed", target=user.id))
    db.commit()
    return {"status": "ok", "message": "Password changed"}


class ShopGstins(BaseModel):
    gstins: List[str]


@router.get("/shop")
def shop_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The shop and the GSTINs it is known by - stated by the owner, or learned
    from the bills it approved. The shop is the buyer on its own purchase bills,
    so knowing these settles who is who on every bill it scans."""
    from app.services.shop_identity import own_gstins

    shop = db.get(Shop, user.shop_id)
    stated = list((shop.settings or {}).get("gstins") or []) if shop else []
    return {"name": shop.name if shop else None, "gstins": own_gstins(db, user.shop_id), "stated": stated,
            "share_corrections": bool((shop.settings or {}).get("share_corrections")) if shop else False}


class ShareCorrections(BaseModel):
    share: bool


@router.put("/shop/share-corrections")
def set_share_corrections(body: ShareCorrections, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """The owner's consent for this shop's corrections to improve the reader for
    every shop (scripts/learning_report.py). Off until the owner turns it on."""
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the shop owner can change this.")
    shop = db.get(Shop, user.shop_id)
    settings = dict(shop.settings or {})
    settings["share_corrections"] = bool(body.share)
    shop.settings = settings
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="shop.share_corrections_set",
                    target=user.shop_id, detail={"share": bool(body.share)}))
    db.commit()
    return {"share_corrections": bool(body.share)}


@router.put("/shop/gstins")
def set_shop_gstins(body: ShopGstins, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The owner states the shop's GSTINs. Invalid ones (wrong check digit) are refused."""
    from app.services.ocr.invoice_header import gstin_is_valid
    from app.services.shop_identity import set_own_gstins

    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only the shop owner can change its GSTINs.")
    bad = [g for g in body.gstins if not gstin_is_valid(str(g).strip().upper())]
    if bad:
        raise HTTPException(status_code=422, detail=f"Not a valid GSTIN: {', '.join(bad)}")
    saved = set_own_gstins(db, user.shop_id, body.gstins)
    db.add(AuditLog(shop_id=user.shop_id, actor_id=user.id, action="shop.gstins_set", target=user.shop_id,
                    detail={"gstins": saved}))
    db.commit()
    return {"gstins": saved}
