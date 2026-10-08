"""Authentication endpoints: register a shop+owner, login, current user."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.shop import Shop
from app.models.user import User
from app.schemas.auth import PasswordChange, Token, UserCreate, UserOut

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Create a new shop and its owner account."""
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    shop = Shop(name=payload.shop_name)
    db.add(shop)
    db.flush()  # assign shop.id

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        shop_id=shop.id,
        role="owner",
    )
    db.add(user)
    db.add(AuditLog(shop_id=shop.id, actor_id=user.id, action="shop.registered", target=shop.id))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    token = create_access_token(user.id, user.shop_id, user.role)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


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
    return {"name": shop.name if shop else None, "gstins": own_gstins(db, user.shop_id), "stated": stated}


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
