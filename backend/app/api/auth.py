"""Authentication endpoints: register a shop+owner, login, current user."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.shop import Shop
from app.models.user import User
from app.schemas.auth import Token, UserCreate, UserOut

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
