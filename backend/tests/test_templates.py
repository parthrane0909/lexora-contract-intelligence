"""Templates: upload/parse/versioning/isolation/RBAC (no second parser)."""
import uuid

from conftest import TestingSession, auth, register
from doc_builders import CONTRACT_PDF_PAGES, make_pdf


def _upload(client, token, name, pages=CONTRACT_PDF_PAGES, ctype="Standard NDA"):
    r = client.post("/api/v1/templates/upload", headers=auth(token),
                    data={"name": name, "contract_type": ctype},
                    files={"file": ("tpl.pdf", make_pdf(pages), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_template_upload_parse_versioning(client):
    token = register(client, "tpl@x.com", "password123", "OrgTpl")
    h = auth(token)
    t1 = _upload(client, token, "Standard NDA")
    assert t1["version"] == 1 and t1["is_active"] and t1["status"] == "ready"
    assert t1["clause_count"] >= 3 and t1["created_by"] == "tpl@x.com"

    clauses = client.get(f"/api/v1/templates/{t1['id']}/clauses", headers=h).json()
    assert [c["order_index"] for c in clauses] == list(range(len(clauses)))
    assert all(c["page_start"] and c["text"] for c in clauses)  # same location concepts

    # Re-upload same name → version 2, v1 deactivated but listed in history.
    t2 = _upload(client, token, "Standard NDA")
    assert t2["version"] == 2 and t2["id"] != t1["id"]
    hist = client.get("/api/v1/templates/history?name=Standard NDA", headers=h).json()
    assert [t["version"] for t in hist] == [2, 1]
    assert sum(t["is_active"] for t in hist) == 1
    # Activate the old version again.
    back = client.post(f"/api/v1/templates/{t1['id']}/activate", headers=h).json()
    assert back["is_active"] is True
    hist2 = client.get("/api/v1/templates/history?name=Standard NDA", headers=h).json()
    assert [t["version"] for t in hist2 if t["is_active"]] == [1]

    # Reprocess is idempotent; download returns original bytes.
    rp = client.post(f"/api/v1/templates/{t2['id']}/reprocess", headers=h).json()
    assert rp["status"] == "ready"
    dl = client.get(f"/api/v1/templates/{t2['id']}/download", headers=h)
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")

    # Archive hides from default list.
    assert client.delete(f"/api/v1/templates/{t2['id']}", headers=h).status_code == 200
    assert all(t["id"] != t2["id"] for t in client.get("/api/v1/templates", headers=h).json())


def test_template_bad_files_and_isolation(client):
    token_a = register(client, "ta@x.com", "password123", "OrgTA")
    token_b = register(client, "tb@x.com", "password123", "OrgTB")
    t = _upload(client, token_a, "Vendor Agreement", ctype="Vendor")
    assert client.get(f"/api/v1/templates/{t['id']}", headers=auth(token_b)).status_code == 404
    assert client.get(f"/api/v1/templates/{t['id']}/clauses", headers=auth(token_b)).status_code == 404
    assert client.get("/api/v1/templates", headers=auth(token_b)).json() == []
    # Corrupt bytes → failed status with message, not a 500.
    r = client.post("/api/v1/templates/upload", headers=auth(token_a),
                    data={"name": "Broken", "contract_type": "NDA"},
                    files={"file": ("b.pdf", b"%PDF-1.4\n%garbage", "application/pdf")})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert r.json()["processing_error"]
    # Wrong extension rejected.
    r2 = client.post("/api/v1/templates/upload", headers=auth(token_a),
                     data={"name": "Bad", "contract_type": "NDA"},
                     files={"file": ("x.exe", b"MZ..", "application/octet-stream")})
    assert r2.status_code == 400
    # Viewer cannot upload/activate.
    from app.core.security import create_access_token

    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="tview@x.com", password_hash="x")
        db.add(user)
        db.flush()
        from app.models import Template as T

        tpl = db.get(T, uuid.UUID(t["id"]))
        db.add(OrganizationMember(organization_id=tpl.organization_id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(tpl.organization_id), "viewer")
    finally:
        db.close()
    assert client.post("/api/v1/templates/upload", headers=auth(vtok),
                       data={"name": "No", "contract_type": "NDA"},
                       files={"file": ("n.pdf", make_pdf(CONTRACT_PDF_PAGES), "application/pdf")}
                       ).status_code == 403
    assert client.get("/api/v1/templates", headers=auth(vtok)).status_code == 200
