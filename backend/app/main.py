"""FastAPI app: CORS, health, v1 routes. Tables auto-created if missing (dev);
production uses Alembic migrations (alembic/versions)."""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.core.deps import require_roles
from app.db.base import Base
from app.db.session import get_db, get_engine
from app.models import AuditLog  # noqa: F401  (register tables)

app = FastAPI(title="Contract Risk & Compliance Intelligence Platform", version="0.1.0")

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in s.BACKEND_CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Part 5: the contract listing reports its page total in X-Total-Count;
    # browsers may only read response headers listed here.
    expose_headers=["X-Total-Count"],
)
app.include_router(api_router)


@app.on_event("startup")
def _startup():
    Base.metadata.create_all(bind=get_engine())


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/v1/audit")
def audit(limit: int = 50, member=Depends(require_roles("admin", "legal_reviewer")),
          db: Session = Depends(get_db)):
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.organization_id == member.organization_id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [{"action": r.action, "object_type": r.object_type, "object_id": r.object_id,
             "detail": r.detail, "created_at": r.created_at.isoformat()} for r in rows]
