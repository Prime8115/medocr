"""Auth dependencies: current user resolution, role checks, tenancy scoping."""
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise _CRED_EXC
    except jwt.PyJWTError:
        raise _CRED_EXC

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise _CRED_EXC
    if user.org_id and not user.is_platform_admin:
        from app.models.organization import Organization

        org = db.get(Organization, user.org_id)
        if org is not None and not org.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="This organisation is inactive. Contact MediScan support.")
    return user


def require_scan_licence(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """The user, when their organisation's licence lets them scan a bill."""
    from app.models.organization import Organization
    from app.services.licensing import LicenceError, check_can_scan

    try:
        check_can_scan(db.get(Organization, user.org_id) if user.org_id else None)
    except LicenceError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return user


def require_owner(user: User = Depends(get_current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")
    return user


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin access required")
    return user
