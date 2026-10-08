from conftest import auth, register


def test_upload_validate_search_archive(client):
    token = register(client)
    h = auth(token)
    pdf = b"%PDF-1.4 fake contract bytes"
    # upload ok
    r = client.post("/api/v1/contracts/upload", headers=h,
                    files={"file": ("msa.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    # invalid type rejected
    bad = client.post("/api/v1/contracts/upload", headers=h,
                      files={"file": ("x.exe", b"MZ...", "application/octet-stream")})
    assert bad.status_code == 400
    # bad magic rejected
    bad2 = client.post("/api/v1/contracts/upload", headers=h,
                       files={"file": ("y.pdf", b"not a pdf", "application/pdf")})
    assert bad2.status_code == 400
    # search + filter
    assert len(client.get("/api/v1/contracts?search=msa", headers=h).json()) == 1
    assert client.get("/api/v1/contracts?search=nothing", headers=h).json() == []
    # analyze: with a live worker/Redis this stays "queued"; without one the
    # pipeline runs inline — fake bytes then fail gracefully with a record.
    a = client.post(f"/api/v1/contracts/{cid}/analyze", headers=h)
    assert a.status_code == 200
    assert a.json()["processing_status"] in ("queued", "processed", "failed")
    if a.json()["processing_status"] == "failed":
        detail = client.get(f"/api/v1/contracts/{cid}", headers=h).json()
        assert detail["processing_error"]
    # download returns original bytes
    d = client.get(f"/api/v1/contracts/{cid}/download", headers=h)
    assert d.status_code == 200 and d.content == pdf
    # archive hides from default list
    assert client.delete(f"/api/v1/contracts/{cid}", headers=h).status_code == 200
    assert client.get("/api/v1/contracts", headers=h).json() == []
