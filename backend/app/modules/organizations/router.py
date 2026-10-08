"""Organization routes: own org + member list. Invite/role-change = admin only."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import Organization, OrganizationMember, User

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrgOut(BaseModel):
    id: str
    name: str


class MemberOut(BaseModel):
    user_id: str
    email: str
    role: str


@router.get("/me", response_model=OrgOut)
def my_org(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    org = db.get(Organization, member.organization_id)
    return {"id": str(org.id), "name": org.name}


@router.get("/members", response_model=list[MemberOut])
def members(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    rows = db.query(OrganizationMember, User).join(User, User.id == OrganizationMember.user_id).filter(
        OrganizationMember.organization_id == member.organization_id
    ).all()
    return [{"user_id": str(m.user_id), "email": u.email, "role": m.role} for m, u in rows]


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "viewer"


@router.post("/members", response_model=MemberOut, dependencies=[Depends(require_roles("admin"))])
def invite(
    body: InviteIn, member=Depends(get_current_membership), db: Session = Depends(get_db)
):
    from app.models import VALID_ROLES

    if body.role not in VALID_ROLES:
        from fastapi import HTTPException

        raise HTTPException(400, f"Invalid role. Valid: {VALID_ROLES}")
    # Phase 1: invite only existing users (no email sending yet).
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        from fastapi import HTTPException

        raise HTTPException(404, "User not found (they must register first in Phase 1)")
    existing = (
        db.query(OrganizationMember)
        .filter(
            OrganizationMember.organization_id == member.organization_id,
            OrganizationMember.user_id == user.id,
        )
        .first()
    )
    if existing:
        existing.role = body.role
    else:
        existing = OrganizationMember(
            organization_id=member.organization_id, user_id=user.id, role=body.role
        )
        db.add(existing)
    db.commit()
    return {"user_id": str(user.id), "email": user.email, "role": existing.role}
