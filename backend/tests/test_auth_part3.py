"""Part 3 — authentication & authorization matrix (spec tests A–H).

    A  valid login                     E  malformed / expired / forged token
    B  invalid login                   F  protected endpoints reject bad auth
    C  GET /auth/me with valid token   G  cross-org resource isolation
    D  GET /auth/me without token      H  role restrictions (403) stay intact
                                        and a 403 does NOT end the session

Everything runs against conftest's isolated SQLite database — the live
PostgreSQL data is never touched. No backend auth behaviour is changed by
Part 3; these tests pin down what already exists.
"""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.core.config import get_settings
from app.models import OrganizationMember, User
from conftest import TestingSession, auth, register

PDF = b"%PDF-1.4 fake contract bytes"

PROTECTED_GET = [
    "/api/v1/auth/me",
    "/api/v1/contracts",
    "/api/v1/portfolio/health",
    "/api/v1/search/contracts?q=nda",
    "/api/v1/templates",
    "/api/v1/policies",
    "/api/v1/organizations/me",
    "/api/v1/ml-evaluation/datasets",
    "/api/v1/reviews/summary",
    f"/api/v1/contracts/{uuid.uuid4()}/risk-assessment",
    f"/api/v1/contracts/{uuid.uuid4()}/summary",
]


# ---------------------------------------------------------------- A / B
def test_a_valid_login(client):
    register(client, "a-valid@x.com")
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "a-valid@x.com", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    # the issued token is immediately usable
    me = client.get("/api/v1/auth/me", headers=auth(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "a-valid@x.com"


@pytest.mark.parametrize(
    "username,password",
    [
        ("a-bad@x.com", "wrong-password"),   # known user, wrong password
        ("nobody@x.com", "password123"),     # unknown user
        ("a-bad@x.com", ""),                 # empty password
    ],
)
def test_b_invalid_login(client, username, password):
    register(client, "a-bad@x.com")
    r = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------- C / D
def test_c_auth_me_valid_token(client):
    token = register(client, "c-me@x.com")
    r = client.get("/api/v1/auth/me", headers=auth(token))
    assert r.status_code == 200, r.text
    me = r.json()
    # Backend is authoritative: these are DB values, not client-side claims.
    uuid.UUID(me["id"])            # id parses
    uuid.UUID(me["organization_id"])
    assert me["email"] == "c-me@x.com"
    assert me["role"] == "admin"
    assert me["display_name"] is None   # register() created none; no invention


def test_d_auth_me_no_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------- E
def _expired_token(user_id: str, org_id: str) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "org": org_id,
            "role": "admin",
            "iat": now - timedelta(hours=1),
            "exp": now - timedelta(minutes=5),
        },
        s.JWT_SECRET,
        algorithm=s.JWT_ALGORITHM,
    )


def test_e_bad_tokens_rejected(client):
    token = register(client, "e-tok@x.com")
    me = client.get("/api/v1/auth/me", headers=auth(token)).json()

    cases = {
        "malformed": "not.a.jwt",
        "truncated": token[: len(token) // 2],
        "forged-signature": (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJ4In0."
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        ),
        "expired": _expired_token(me["id"], me["organization_id"]),
        # structurally valid, but the subject is not a real user
        "unknown-sub": jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "org": me["organization_id"],
                "role": "admin",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            get_settings().JWT_SECRET,
            algorithm=get_settings().JWT_ALGORITHM,
        ),
        # token signed for a different (empty) org: valid signature, no membership
        "org-without-membership": jwt.encode(
            {
                "sub": me["id"],
                "org": str(uuid.uuid4()),
                "role": "admin",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            get_settings().JWT_SECRET,
            algorithm=get_settings().JWT_ALGORITHM,
        ),
    }
    for label, bad in cases.items():
        r = client.get("/api/v1/auth/me", headers=auth(bad))
        # 401 = cannot authenticate. A valid signature with no membership for
        # the claimed org is a *permission* problem -> 403, never 200.
        expected = 403 if label == "org-without-membership" else 401
        assert r.status_code == expected, f"{label}: {r.status_code} {r.text}"


# ---------------------------------------------------------------- F
@pytest.mark.parametrize("path", PROTECTED_GET)
def test_f_protected_get_without_token(client, path):
    assert client.get(path).status_code == 401, path
    assert client.get(path, headers=auth("garbage.token.value")).status_code == 401, path


def test_f_protected_post_without_token(client):
    r = client.post("/api/v1/retrieval/search", json={"query": "indemnity", "top_k": 3})
    assert r.status_code == 401, r.text
    r2 = client.post("/api/v1/search/reindex-contracts")
    assert r2.status_code == 401, r2.text


def test_f_valid_token_still_works_for_every_path(client):
    """The 401s above are auth failures, not broken endpoints."""
    token = register(client, "f-ok@x.com")
    for path in PROTECTED_GET:
        r = client.get(path, headers=auth(token))
        # A valid admin session is never told it is unauthenticated (401) or
        # forbidden (403); 404 = empty resource, 503 = search backend down.
        assert r.status_code not in (401, 403), f"{path} -> {r.status_code} {r.text}"


# ---------------------------------------------------------------- G
def test_g_cross_org_isolation(client):
    t1 = register(client, "g1@x.com", org="Org One")
    t2 = register(client, "g2@x.com", org="Org Two")

    up = client.post(
        "/api/v1/contracts/upload",
        headers=auth(t1),
        files={"file": ("iso.pdf", io.BytesIO(PDF), "application/pdf")},
    )
    assert up.status_code == 200, up.text
    cid = up.json()["id"]

    me1 = client.get("/api/v1/auth/me", headers=auth(t1)).json()
    me2 = client.get("/api/v1/auth/me", headers=auth(t2)).json()
    assert me1["organization_id"] != me2["organization_id"]

    # The other organization cannot read the resource at all (404, no leak).
    assert client.get(f"/api/v1/contracts/{cid}", headers=auth(t2)).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/risk-assessment", headers=auth(t2)).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/summary", headers=auth(t2)).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/download", headers=auth(t2)).status_code == 404

    # Lists are org-scoped: Org Two sees nothing of Org One.
    assert client.get("/api/v1/contracts", headers=auth(t2)).json() == []
    names2 = [c["filename"] for c in client.get("/api/v1/contracts", headers=auth(t1)).json()]
    assert "iso.pdf" in names2

    # Organization endpoints resolve to each caller's own organization.
    org2 = client.get("/api/v1/organizations/me", headers=auth(t2))
    assert org2.status_code == 200
    assert org2.json()["id"] == me2["organization_id"]


# ---------------------------------------------------------------- H
def test_h_role_restrictions_and_403(client):
    token = register(client, "h-role@x.com")

    # Make this test member a viewer (isolated test DB only).
    db = TestingSession()
    try:
        user = db.query(User).filter(User.email == "h-role@x.com").one()
        member = db.query(OrganizationMember).filter(OrganizationMember.user_id == user.id).one()
        member.role = "viewer"
        db.commit()
    finally:
        db.close()

    # Still authenticated — 403 is permission, not authentication.
    assert client.get("/api/v1/auth/me", headers=auth(token)).status_code == 200

    # Admin-only endpoint refuses with 403 (not 401, and not 200).
    invite = client.post(
        "/api/v1/organizations/members",
        headers=auth(token),
        json={"email": "someone@x.com", "role": "viewer"},
    )
    assert invite.status_code == 403, invite.text

    # Admin/contract_manager endpoint also refuses a viewer.
    reindex = client.post("/api/v1/search/reindex-contracts", headers=auth(token))
    assert reindex.status_code == 403, reindex.text

    # The forbidden calls did not log the user out: the session still resolves.
    assert client.get("/api/v1/auth/me", headers=auth(token)).status_code == 200
    # ...and read access to org data still works for a viewer.
    assert client.get("/api/v1/contracts", headers=auth(token)).status_code == 200
