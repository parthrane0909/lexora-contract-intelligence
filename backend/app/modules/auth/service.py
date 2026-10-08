"""Auth service: register (user+org+membership), authenticate."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models import Organization, OrganizationMember, User
from app.modules.audit.service import record


def register(db: Session, email: str, password: str, org_name: str, display_name: str | None):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be >= 8 chars")
    if db.query(User).filter(User.email == email.lower()).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email.lower(), password_hash=hash_password(password), display_name=display_name)
    org = Organization(name=org_name)
    db.add_all([user, org])
    db.flush()
    member = OrganizationMember(organization_id=org.id, user_id=user.id, role="admin")
    db.add(member)
    db.commit()
    db.refresh(user)
    record(db, action="user.registered", organization_id=org.id, user_id=user.id, detail=email)
    token = create_access_token(str(user.id), str(org.id), "admin")
    return user, org, member, token


def authenticate(db: Session, email: str, password: str):
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    member = (
        db.query(OrganizationMember).filter(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at).first()
    )
    if not member:
        raise HTTPException(status_code=403, detail="No organization membership")
    token = create_access_token(str(user.id), str(member.organization_id), member.role)
    return user, member, token
