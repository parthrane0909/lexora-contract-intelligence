from conftest import auth, register


def test_register_login_me(client):
    token = register(client)
    r = client.get("/api/v1/auth/me", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    # duplicate email rejected
    r2 = client.post("/api/v1/auth/register", json={
        "email": "a@x.com", "password": "password123", "org_name": "Other"})
    assert r2.status_code == 400
    # wrong password rejected
    r3 = client.post("/api/v1/auth/login", data={"username": "a@x.com", "password": "wrong"})
    assert r3.status_code == 401


def test_org_isolation(client):
    t1 = register(client, "u1@x.com", "password123", "Org1")
    t2 = register(client, "u2@x.com", "password123", "Org2")
    pdf = b"%PDF-1.4 fake contract bytes"
    r = client.post("/api/v1/contracts/upload", headers=auth(t1),
                    files={"file": ("c1.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    # Org2 cannot see Org1's contract (404, no existence leak)
    assert client.get(f"/api/v1/contracts/{cid}", headers=auth(t2)).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/download", headers=auth(t2)).status_code == 404
    # Org2 list is empty
    assert client.get("/api/v1/contracts", headers=auth(t2)).json() == []
    # Org1 sees it
    assert client.get(f"/api/v1/contracts/{cid}", headers=auth(t1)).status_code == 200
