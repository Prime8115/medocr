"""Auth dependencies: current user resolution, role checks, tenancy scoping."""
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.organization import Organization, UserWorkspace
from app.models.shop import Shop
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


class CurrentUser:
    """Database user scoped to the workspace selected by this signed session.

    The login default on users.shop_id is never changed by a workspace switch.
    Two devices can therefore use different workspaces without moving each
    other's documents, supplier lessons or connector requests.
    """

    def __init__(self, record: User, shop_id: str):
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "shop_id", shop_id)

    def __getattr__(self, name):
        return getattr(self.record, name)

    def __setattr__(self, name, value):
        if name in {"record", "shop_id"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.record, name, value)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> CurrentUser:
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
    selected = payload.get("shop_id") or user.shop_id
    if user.org_id:
        org = db.get(Organization, user.org_id)
        if org is None:
            raise _CRED_EXC
        if not org.is_active and not user.is_platform_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="This organisation is inactive. Contact MediScan support.")
        if not db.query(Shop.id).filter(Shop.id == selected, Shop.org_id == user.org_id).first():
            raise _CRED_EXC
        if user.role != "owner" and not db.get(UserWorkspace, (user.id, selected)):
            raise _CRED_EXC
    elif selected != user.shop_id:
        raise _CRED_EXC
    return CurrentUser(user, selected)


def require_scan_licence(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> CurrentUser:
    """The user, when their organisation's licence lets them scan a bill."""
    from app.services.licensing import LicenceError, check_can_scan

    try:
        check_can_scan(db.get(Organization, user.org_id) if user.org_id else None)
    except LicenceError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return user


def require_owner(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner role required")
    return user


def require_platform_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin access required")
    return user
