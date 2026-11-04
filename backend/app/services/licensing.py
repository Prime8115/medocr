"""Licence plans and the rules that hold an organisation to them.

Plans are fixed: the platform admin picks one per organisation. Seats count
ACTIVE users only. Reaching a limit never removes anyone - it only stops
adding (docs/ORG_USER_MANAGEMENT_PRD.md, section 4).
"""
from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.organization import Organization, UserWorkspace
from app.models.shop import Shop
from app.models.user import User

# Fixed plans, smallest first. Changed here only.
PLANS = {
    "starter": {"label": "Starter", "users": 2, "workspaces": 1},
    "growth": {"label": "Growth", "users": 5, "workspaces": 3},
    "business": {"label": "Business", "users": 15, "workspaces": 10},
    "enterprise": {"label": "Enterprise", "users": 50, "workspaces": 50},
}
DEFAULT_PLAN = "starter"

ADMIN, MEMBER = "owner", "staff"


class LicenceError(Exception):
    """An action the organisation's licence does not allow, with the reason."""


def plan_of(org: Organization) -> dict:
    return PLANS.get(org.plan) or PLANS[DEFAULT_PLAN]


def smallest_plan_for(users: int, workspaces: int = 1) -> str:
    return next((k for k, p in PLANS.items() if p["users"] >= users and p["workspaces"] >= workspaces),
                "enterprise")


def seats_used(db: Session, org_id: str) -> int:
    return db.query(User).filter(User.org_id == org_id, User.is_active.is_(True)).count()


def workspaces_used(db: Session, org_id: str) -> int:
    return db.query(Shop).filter(Shop.org_id == org_id).count()


def expired(org: Organization, today: Optional[date] = None) -> bool:
    return bool(org.valid_until and org.valid_until < (today or date.today()))


def summary(db: Session, org: Organization) -> dict:
    plan = plan_of(org)
    used, spaces = seats_used(db, org.id), workspaces_used(db, org.id)
    return {
        "id": org.id, "name": org.name, "plan": org.plan, "plan_label": plan["label"],
        "seats_used": used, "seats_allowed": plan["users"],
        "workspaces_used": spaces, "workspaces_allowed": plan["workspaces"],
        "valid_until": org.valid_until.isoformat() if org.valid_until else None,
        "is_active": bool(org.is_active), "expired": expired(org),
        "over_limit": used > plan["users"] or spaces > plan["workspaces"],
    }


def _may_add(org: Organization) -> None:
    if not org.is_active:
        raise LicenceError("This organisation is inactive.")
    if expired(org):
        raise LicenceError(f"The licence expired on {org.valid_until.isoformat()}.")


def check_can_scan(org: Optional[Organization], today: Optional[date] = None) -> None:
    """Reading a bill - an upload, a scan again or a recheck - needs a licence
    in force. An expired licence stops scanning; the bills already read stay
    open to review, correct and approve (PRD section 4)."""
    if org is None:
        return
    if not org.is_active:
        raise LicenceError("This organisation is inactive. Contact MediScan support.")
    if expired(org, today):
        raise LicenceError(f"The licence expired on {org.valid_until.isoformat()}. "
                           "Renew it to scan bills - bills already scanned stay open.")


def check_can_add_user(db: Session, org: Organization) -> None:
    """Adding or reactivating one user needs a free seat."""
    _may_add(org)
    plan = plan_of(org)
    if seats_used(db, org.id) >= plan["users"]:
        raise LicenceError(f"All {plan['users']} seats on the {plan['label']} plan are in use.")


def check_can_add_workspace(db: Session, org: Organization) -> None:
    _may_add(org)
    plan = plan_of(org)
    if workspaces_used(db, org.id) >= plan["workspaces"]:
        raise LicenceError(f"The {plan['label']} plan allows {plan['workspaces']} workspace(s).")


def accessible_workspaces(db: Session, user: User) -> List[Shop]:
    """The workspaces this user may open: every one of the organisation for an
    admin, the ones given to a member - and always the current one."""
    if not user.org_id:
        shop = db.get(Shop, user.shop_id)
        return [shop] if shop else []
    q = db.query(Shop).filter(Shop.org_id == user.org_id)
    if user.role != ADMIN:
        ids = [r.shop_id for r in db.query(UserWorkspace).filter(UserWorkspace.user_id == user.id)]
        q = q.filter(Shop.id.in_(ids or [user.shop_id]))
    return q.order_by(Shop.name).all()


def workspace_ids_of(db: Session, user: User) -> List[str]:
    return [r.shop_id for r in db.query(UserWorkspace).filter(UserWorkspace.user_id == user.id)]


def set_workspaces(db: Session, user: User, shop_ids: List[str]) -> None:
    """Replace a user's workspace access, keeping their current workspace one
    they can still open."""
    db.query(UserWorkspace).filter(UserWorkspace.user_id == user.id).delete()
    for sid in dict.fromkeys(shop_ids):
        db.add(UserWorkspace(user_id=user.id, shop_id=sid))
    if shop_ids and user.shop_id not in shop_ids and user.role != ADMIN:
        user.shop_id = shop_ids[0]


def active_admins(db: Session, org_id: str) -> int:
    return db.query(User).filter(User.org_id == org_id, User.role == ADMIN, User.is_active.is_(True)).count()
