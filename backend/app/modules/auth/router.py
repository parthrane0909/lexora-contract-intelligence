"""Auth routes."""
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, get_current_user
from app.db.session import get_db
from app.models import User
from app.modules.auth import schemas, service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.TokenOut)
def register(body: schemas.RegisterIn, db: Session = Depends(get_db)):
    _, _, _, token = service.register(db, body.email, body.password, body.org_name, body.display_name)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login", response_model=schemas.TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    _, _, token = service.authenticate(db, form.username, form.password)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(get_current_user), member=Depends(get_current_membership)):
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "organization_id": str(member.organization_id),
        "role": member.role,
    }
