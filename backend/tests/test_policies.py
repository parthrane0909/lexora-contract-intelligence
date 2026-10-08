"""Policy engine: validation, versioning, RBAC, isolation (no ML involved)."""
from conftest import auth, register


def _policy(client, token, category, params):
    r = client.post("/api/v1/policies", headers=auth(token),
                    json={"category": category, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_policy_crud_and_versioning(client):
    token = register(client, "pol@x.com", "password123", "OrgPol")
    h = auth(token)
    assert client.get("/api/v1/policies", headers=h).json() == []
    p1 = _policy(client, token, "payment", {"max_days": 30})
    assert p1["version"] == 1 and p1["is_active"] and p1["params"]["impact"] == "standard"
    p2 = _policy(client, token, "payment", {"max_days": 45, "impact": "high"})
    assert p2["version"] == 2
    active = client.get("/api/v1/policies", headers=h).json()
    assert len(active) == 1 and active[0]["version"] == 2
    hist = client.get("/api/v1/policies/history?category=payment", headers=h).json()
    assert [p["version"] for p in hist] == [2, 1]  # history preserved


def test_policy_validation_rejects_garbage(client):
    token = register(client, "pv@x.com", "password123", "OrgPV")
    bad = [
        ("nonsense", {"max_days": 30}),  # unknown category
        ("payment", {"max_dayz": 30}),  # unknown param key
        ("payment", {"max_days": -5}),  # negative
        ("payment", {"max_days": "thirty"}),  # wrong type
        ("renewal", {"allow_auto_renew": "yes"}),  # wrong type
        ("governing_law", {"preferred_jurisdictions": []}),  # empty list
        ("payment", {"max_days": 30, "impact": "extreme"}),  # bad impact
    ]
    for cat, params in bad:
        r = client.post("/api/v1/policies", headers=auth(token),
                        json={"category": cat, "params": params})
        assert r.status_code == 400, (cat, params, r.text)


def test_policy_rbac_and_isolation(client):
    from app.core.security import create_access_token
    from conftest import TestingSession

    from app.models import Contract as _C  # noqa: F401 (import side-effect check)
    from app.models import OrganizationMember, User

    token_a = register(client, "pa@x.com", "password123", "OrgPA")
    token_b = register(client, "pb@x.com", "password123", "OrgPB")
    _policy(client, token_a, "payment", {"max_days": 30})
    # Org B sees none of org A's policies.
    assert client.get("/api/v1/policies", headers=auth(token_b)).json() == []
    assert client.get("/api/v1/policies/history", headers=auth(token_b)).json() == []
    # Viewer cannot write.
    db = TestingSession()
    try:
        user = User(email="pview@x.com", password_hash="x")
        db.add(user)
        db.flush()
        from app.models import Organization

        org = db.query(Organization).filter(Organization.name == "OrgPA").first()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    r = client.post("/api/v1/policies", headers=auth(vtok),
                    json={"category": "payment", "params": {"max_days": 10}})
    assert r.status_code == 403
    # Viewer CAN read.
    assert client.get("/api/v1/policies", headers=auth(vtok)).status_code == 200
