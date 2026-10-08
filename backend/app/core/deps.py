"""Auth dependencies: current user + org scoping + RBAC (spec Module 1)."""
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models import OrganizationMember, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # Attach org/role from token for request scoping (membership re-validated below).
    user.token_org_id = payload.get("org")  # type: ignore[attr-defined]
    user.token_role = payload.get("role")  # type: ignore[attr-defined]
    return user


def get_current_membership(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> OrganizationMember:
    """Every org-scoped endpoint depends on this: guarantees org isolation."""
    org_id = getattr(user, "token_org_id", None)
    member = (
        db.query(OrganizationMember)
        .filter(
            OrganizationMember.user_id == user.id,
            OrganizationMember.organization_id == uuid.UUID(org_id),
        )
        .first()
        if org_id
        else None
    )
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization access")
    return member


def require_roles(*roles: str):
    def checker(member: OrganizationMember = Depends(get_current_membership)) -> OrganizationMember:
        if member.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return member

    return checker
