"""Shared Phase 2 test helpers: upload bytes via API, run pipeline inline."""
import uuid

from conftest import TestingSession, auth, register

from app.modules.document.pipeline import process_contract


def upload_and_process(client, data: bytes, filename: str, email: str = "u@x.com"):
    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (filename, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        result = process_contract(db, uuid.UUID(cid))
    finally:
        db.close()
    return token, cid, result


def norm(s: str) -> str:
    import re

    return re.sub(r"\s+", " ", s).strip()
