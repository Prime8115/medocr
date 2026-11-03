"""Grant or revoke report-queue access for one already-registered account.

Run from backend/: python -m scripts.set_platform_admin user@example.com --grant
"""
import argparse

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.user import User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Existing MediScan account email")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--grant", action="store_true")
    action.add_argument("--revoke", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        user = db.query(User).filter(User.email.ilike(args.email.strip())).one_or_none()
        if user is None:
            parser.error("Account not found; register and verify the intended account first")
        user.is_platform_admin = args.grant
        db.add(AuditLog(
            shop_id=user.shop_id, actor_id=None,
            action="platform_admin.granted" if args.grant else "platform_admin.revoked",
            target=user.id, detail={"email": user.email, "source": "server_cli"},
        ))
        db.commit()
        print(f"{'Granted' if args.grant else 'Revoked'} platform report access: {user.email}")


if __name__ == "__main__":
    main()
