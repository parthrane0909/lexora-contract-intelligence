"""Part 5 — contract metadata: category, important flag, filters, deletion.

Targeted coverage of the Part 5 acceptance list, against the real API:

    1–3   category persists, can be updated/cleared, survives reprocessing
    4–5   is_important toggles (idempotent) and survives reprocessing
    6–10  server-side filters (category / important / status / combined),
          sorting and pagination with X-Total-Count
    11–12 organization isolation and auth (no token → 401, viewer → 403)
    13–14 Elasticsearch sees metadata updates; reindex stays compatible
          with the v2 mapping (category + is_important)
    15–18 archive removes a contract from default listing/search, touches
          only the target, keeps related records and file bytes
    19    a failing search index can never corrupt PostgreSQL

Plus the deterministic category rules/backfill (Steps 2–3) and the
dashboard KPI input (Step 12).

Frozen parts (portfolio scoring, Legal-BERT, Qdrant, auth internals) are
NOT re-audited here — their own suites already cover them.
"""
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf
from doc_helpers import upload_and_process

from app.models import Clause, Contract, ParsedDocument, SemanticIndex
from app.modules.contracts.category import (
    UNSPECIFIED,
    backfill_categories,
    parse_category,
    suggest_category,
)
from app.modules.search import es, index as search_index

PDF = b"%PDF-1.4 fake contract bytes"
PAGES = [
    ["1. Services", "Provider shall deliver the services described here."],
    ["2. Fees", "Invoices are payable within thirty (30) days of receipt."],
]


# ------------------------------------------------------------- helpers ------

def _upload(client, token, filename="sample.pdf") -> str:
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (filename, PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _patch(client, token, cid, **body):
    return client.patch(f"/api/v1/contracts/{cid}", headers=auth(token), json=body)


def _get(client, token, cid) -> dict:
    r = client.get(f"/api/v1/contracts/{cid}", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _ids(payload: list) -> set:
    return {row["id"] for row in payload}


def _viewer_token(cid, email):
    """A viewer member of the SAME organization as `cid` (Part 3 RBAC)."""
    from app.core.security import create_access_token
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email=email, password_hash="x")
        db.add(user)
        db.flush()
        contract = db.get(Contract, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=contract.organization_id,
                                  user_id=user.id, role="viewer"))
        db.commit()
        return create_access_token(str(user.id), str(contract.organization_id),
                                   "viewer")
    finally:
        db.close()


def _es_reachable() -> bool:
    try:
        es.cluster_info()
        return True
    except Exception:  # noqa: BLE001 — any failure means "skip, not fail"
        return False


needs_es = pytest.mark.skipif(
    not _es_reachable(),
    reason="Elasticsearch not reachable at ELASTICSEARCH_URL "
           "(start it with: docker compose up -d elasticsearch)")


@pytest.fixture
def fresh_index():
    """Each ES test starts from an empty `contract_search_test` index."""
    search_index.ensure_index(force=True)
    yield


# ------------------------------------------------- 1/2. category state ------


def test_category_persists_updates_and_has_explicit_unspecified(client):
    token = register(client, "cat@x.com")
    h = auth(token)
    cid = _upload(client, token, "agreement.pdf")

    # A new upload has no category: the state is explicit, not an empty cell.
    assert _get(client, token, cid)["contract_category"] == UNSPECIFIED
    row = client.get("/api/v1/contracts", headers=h).json()[0]
    assert row["contract_category"] == UNSPECIFIED and row["contract_type"] is None

    # Manual override persists across fresh reads (same column as
    # contract_type, which the list/detail/search APIs already exposed).
    r = _patch(client, token, cid, contract_category="Vendor Agreement")
    assert r.status_code == 200, r.text
    assert r.json()["contract_category"] == "Vendor Agreement"
    assert r.json()["contract_type"] == "Vendor Agreement"
    assert _get(client, token, cid)["contract_category"] == "Vendor Agreement"

    # Explicit clearing back to the Unspecified state.
    r = _patch(client, token, cid, contract_category=UNSPECIFIED)
    assert r.json()["contract_category"] == UNSPECIFIED
    assert r.json()["contract_type"] is None

    # Values outside the vocabulary are rejected and change nothing.
    bad = _patch(client, token, cid, contract_category="Not A Category")
    assert bad.status_code == 400, bad.text
    assert _get(client, token, cid)["contract_category"] == UNSPECIFIED

    # The legacy free-text field keeps working exactly as before (Part 2
    # relies on it) and is reported through the same category view.
    assert _patch(client, token, cid, contract_type="reseller").status_code == 200
    assert _get(client, token, cid)["contract_category"] == "reseller"


# --------------------------------------------------- 3. reprocessing --------


def test_category_and_important_survive_reprocessing(client):
    from app.modules.document.pipeline import process_contract

    pdf = make_pdf(PAGES)
    token, cid, result = upload_and_process(
        client, pdf, "reprocess_me.pdf", "re@x.com")
    assert result["status"] == "processed"
    assert _patch(client, token, cid,
                  contract_category="Services Agreement",
                  is_important=True).status_code == 200

    # Run the whole document pipeline again (fresh parse + segmentation).
    db = TestingSession()
    try:
        rerun = process_contract(db, uuid.UUID(cid))
    finally:
        db.close()
    assert rerun["status"] == "processed"

    after = _get(client, token, cid)
    assert after["contract_category"] == "Services Agreement"
    assert after["is_important"] is True
    assert after["processing_status"] == "processed"


# ------------------------------------------------- 4/5. important flag -------


def test_important_toggle_is_persistent_and_idempotent(client):
    token = register(client, "star@x.com")
    h = auth(token)
    cid = _upload(client, token, "starred.pdf")
    assert _get(client, token, cid)["is_important"] is False

    # Repeated identical updates are idempotent, never toggle.
    for _ in range(3):
        r = _patch(client, token, cid, is_important=True)
        assert r.status_code == 200, r.text
        assert r.json()["is_important"] is True
    assert _get(client, token, cid)["is_important"] is True
    assert client.get("/api/v1/contracts", headers=h).json()[0]["is_important"] is True

    # Toggle back off.
    assert _patch(client, token, cid, is_important=False).json()["is_important"] is False

    # An omitted field means "leave unchanged" (not false).
    r = _patch(client, token, cid, counterparty="ACME")
    assert r.status_code == 200
    assert r.json()["is_important"] is False
    assert r.json()["counterparty"] == "ACME"


# --------------------------------------------------- 6–9. filtering ---------


def test_list_filters_category_important_status_and_combination(client):
    token = register(client, "flt@x.com")
    h = auth(token)
    a = _upload(client, token, "one.pdf")
    b = _upload(client, token, "two.pdf")
    c = _upload(client, token, "three.pdf")
    _patch(client, token, a, contract_category="Vendor Agreement", is_important=True)
    _patch(client, token, b, contract_category="Employment Agreement")
    # c keeps no category on purpose.

    # 6. category filter, including the explicit Unspecified state.
    assert _ids(client.get("/api/v1/contracts",
                           params={"category": "Vendor Agreement"},
                           headers=h).json()) == {a}
    assert _ids(client.get("/api/v1/contracts",
                           params={"category": "Other / Unspecified"},
                           headers=h).json()) == {c}
    # "Unspecified" is accepted as the short spelling of the same state.
    assert _ids(client.get("/api/v1/contracts",
                           params={"category": "Unspecified"},
                           headers=h).json()) == {c}

    # 7. important-only.
    assert _ids(client.get("/api/v1/contracts", params={"important": "true"},
                           headers=h).json()) == {a}
    assert client.get("/api/v1/contracts", params={"important": "false"},
                      headers=h).json() != []

    # 8. status filters (authoritative columns, never re-derived).
    assert _ids(client.get("/api/v1/contracts", params={"status": "uploaded"},
                           headers=h).json()) == {a, b, c}
    assert client.get("/api/v1/contracts",
                      params={"status": "processed"}, headers=h).json() == []
    assert _ids(client.get("/api/v1/contracts",
                           params={"analysis_status": "pending"},
                           headers=h).json()) == {a, b, c}
    assert client.get("/api/v1/contracts",
                      params={"analysis_status": "complete"},
                      headers=h).json() == []

    # 9. combined filters are ANDed.
    assert _ids(client.get(
        "/api/v1/contracts",
        params={"important": "true", "category": "Vendor Agreement",
                "status": "uploaded"},
        headers=h).json()) == {a}
    assert client.get(
        "/api/v1/contracts",
        params={"important": "true", "category": "Employment Agreement"},
        headers=h).json() == []

    # Invalid filter values are rejected, never silently ignored.
    for params in ({"category": "banana"},
                   {"analysis_status": "banana"},
                   {"status": "banana"},
                   {"sort": "banana"}):
        assert client.get("/api/v1/contracts", params=params,
                          headers=h).status_code == 400, params


# ------------------------------------------------ 10. sorting + paging ------


def test_sorting_and_pagination(client):
    token = register(client, "srt@x.com")
    h = auth(token)
    first = _upload(client, token, "a_first.pdf")
    second = _upload(client, token, "b_second.pdf")
    third = _upload(client, token, "c_third.pdf")
    _patch(client, token, first, contract_category="Vendor Agreement")
    _patch(client, token, third, contract_category="Employment Agreement")

    names = lambda params: [r["filename"] for r in client.get(
        "/api/v1/contracts", params=params, headers=h).json()]  # noqa: E731

    assert names({}) == ["c_third.pdf", "b_second.pdf", "a_first.pdf"]  # newest
    assert names({"sort": "oldest"}) == ["a_first.pdf", "b_second.pdf", "c_third.pdf"]
    assert names({"sort": "filename"}) == ["a_first.pdf", "b_second.pdf", "c_third.pdf"]
    # Category sort coalesces the missing value so PostgreSQL and SQLite
    # order "Other / Unspecified" identically.
    assert names({"sort": "category"}) == [
        "c_third.pdf",          # Employment Agreement
        "b_second.pdf",         # Other / Unspecified
        "a_first.pdf",          # Vendor Agreement
    ]

    page1 = client.get("/api/v1/contracts",
                       params={"sort": "oldest", "page": 1, "limit": 2}, headers=h)
    page2 = client.get("/api/v1/contracts",
                       params={"sort": "oldest", "page": 2, "limit": 2}, headers=h)
    assert [r["filename"] for r in page1.json()] == ["a_first.pdf", "b_second.pdf"]
    assert [r["filename"] for r in page2.json()] == ["c_third.pdf"]
    assert page1.headers["X-Total-Count"] == "3"
    assert page2.headers["X-Total-Count"] == "3"
    assert client.get("/api/v1/contracts", params={"page": 0}, headers=h).status_code == 422
    assert client.get("/api/v1/contracts", params={"limit": 1000}, headers=h).status_code == 422


# --------------------------------------------- 11/12. isolation + auth ------


def test_cross_organization_reads_and_writes_are_rejected(client):
    token_a = register(client, "iso_a@x.com")
    cid = _upload(client, token_a, "confidential.pdf")
    token_b = register(client, "iso_b@x.com")

    assert client.get(f"/api/v1/contracts/{cid}",
                      headers=auth(token_b)).status_code == 404
    assert _patch(client, token_b, cid, is_important=True,
                  contract_category="Vendor Agreement").status_code == 404
    assert client.delete(f"/api/v1/contracts/{cid}",
                         headers=auth(token_b)).status_code == 404
    assert client.get("/api/v1/contracts", headers=auth(token_b)).json() == []

    # Organization A's row is untouched by B's attempts.
    mine = _get(client, token_a, cid)
    assert mine["is_important"] is False and mine["contract_category"] == UNSPECIFIED


def test_unauthenticated_and_viewer_writes_are_rejected(client):
    token = register(client, "authz@x.com")
    cid = _upload(client, token, "guarded.pdf")

    # No token at all: reads and writes alike.
    assert client.get("/api/v1/contracts").status_code == 401
    assert client.patch(f"/api/v1/contracts/{cid}",
                        json={"is_important": True}).status_code == 401
    assert client.delete(f"/api/v1/contracts/{cid}").status_code == 401
    assert client.get("/api/v1/search/contracts",
                      params={"q": "guarded"}).status_code == 401
    assert client.get("/api/v1/portfolio/summary").status_code == 401

    # A viewer of the same organization may read, never write.
    vtok = _viewer_token(cid, "viewer_meta@x.com")
    assert client.get(f"/api/v1/contracts/{cid}",
                      headers=auth(vtok)).status_code == 200
    assert client.patch(f"/api/v1/contracts/{cid}", headers=auth(vtok),
                        json={"is_important": True}).status_code == 403
    assert client.patch(f"/api/v1/contracts/{cid}", headers=auth(vtok),
                        json={"contract_category": "Vendor Agreement"}).status_code == 403
    assert client.delete(f"/api/v1/contracts/{cid}",
                         headers=auth(vtok)).status_code == 403
    assert _get(client, token, cid)["is_important"] is False


# ------------------------------------- 13/14. Elasticsearch synchronisation --


@needs_es
def test_search_sees_category_and_important_updates(client, fresh_index):
    token, cid, _ = upload_and_process(client, make_pdf(PAGES),
                                       "vendor_deal.pdf", "es1@x.com")
    h = auth(token)
    search = lambda **params: client.get(  # noqa: E731
        "/api/v1/search/contracts", params=params, headers=h).json()

    assert search(q="vendor_deal")["total"] == 1
    # Before the update the new filters match nothing.
    assert search(q="vendor_deal", category="Vendor Agreement")["total"] == 0

    # PostgreSQL update → search document update (same index_contract hook).
    r = _patch(client, token, cid, contract_category="Vendor Agreement",
               is_important=True)
    assert r.status_code == 200, r.text

    # Query AND metadata filters operate together.
    body = search(q="vendor_deal", category="Vendor Agreement", important="true")
    assert body["total"] == 1, body
    hit = body["results"][0]
    assert hit["contract_id"] == cid
    assert hit["contract_category"] == "Vendor Agreement"
    assert hit["is_important"] is True
    assert hit["analysis_status"] and hit["processing_status"]

    # A different value of the same filter excludes the contract.
    assert search(q="vendor_deal", category="Employment Agreement")["total"] == 0
    assert search(q="vendor_deal", important="false")["total"] == 0

    # Flipping the flag back is reflected too.
    _patch(client, token, cid, is_important=False)
    assert search(q="vendor_deal", important="true")["total"] == 0
    assert search(q="vendor_deal", important="false")["total"] == 1


@needs_es
def test_reindex_is_idempotent_and_compatible_with_v2_mapping(client, fresh_index):
    token, cid, _ = upload_and_process(client, make_pdf(PAGES),
                                       "reindex_me.pdf", "es2@x.com")
    _patch(client, token, cid, contract_category="Vendor Agreement",
           is_important=True)

    out = client.post("/api/v1/search/reindex-contracts",
                      headers=auth(token)).json()
    assert out["index"] == es.index_name()  # reads/writes go through the alias
    assert out["index_version"] == search_index.INDEX_VERSION
    assert out["contracts_in_postgres"] >= 1
    assert out["indexed"] >= 1 and out["failed"] == 0, out

    # Idempotent: running it again indexes the same rows, removes nothing.
    again = client.post("/api/v1/search/reindex-contracts",
                        headers=auth(token)).json()
    assert again["indexed"] == out["indexed"] and again["removed"] == 0
    assert again["failed"] == 0

    # New fields remain filterable after a full reindex.
    body = client.get("/api/v1/search/contracts",
                      params={"q": "reindex_me", "category": "Vendor Agreement",
                              "important": "true"},
                      headers=auth(token)).json()
    assert body["total"] == 1


@needs_es
def test_archived_contract_disappears_from_default_search(client, fresh_index):
    token, cid, _ = upload_and_process(client, make_pdf(PAGES),
                                       "archivable.pdf", "es3@x.com")
    h = auth(token)
    assert client.get("/api/v1/search/contracts",
                      params={"q": "archivable"}, headers=h).json()["total"] == 1

    r = client.delete(f"/api/v1/contracts/{cid}", headers=h)
    assert r.status_code == 200 and r.json()["archived"] is True

    # Default listing AND default search no longer show it…
    assert client.get("/api/v1/contracts", headers=h).json() == []
    assert client.get("/api/v1/search/contracts",
                      params={"q": "archivable"}, headers=h).json()["total"] == 0
    # …but it still exists (archive, never a silent purge).
    assert client.get("/api/v1/search/contracts",
                      params={"q": "archivable", "include_archived": "true"},
                      headers=h).json()["total"] == 1


# ---------------------------------------- 15–18. deletion safety ------------


def test_archive_affects_only_the_target_and_keeps_related_records(client):
    pdf = make_pdf(PAGES)
    token, cid1, _ = upload_and_process(client, pdf, "keep_me.pdf", "del1@x.com")
    h = auth(token)
    cid2 = _upload(client, token, "keep_me_too.pdf")

    db = TestingSession()
    try:
        org = db.get(Contract, uuid.UUID(cid1)).organization_id
        semantic_before = db.query(SemanticIndex).filter(
            SemanticIndex.organization_id == org).count()
        clauses_before = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid1)).count()
        parsed_before = db.query(ParsedDocument).filter(
            ParsedDocument.contract_id == uuid.UUID(cid1)).count()
        storage_key = db.get(Contract, uuid.UUID(cid1)).storage_key
    finally:
        db.close()
    assert clauses_before > 0 and parsed_before == 1

    r = client.delete(f"/api/v1/contracts/{cid1}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Clear response: this is an ARCHIVE, explicitly not a permanent purge.
    assert body == {**body, "ok": True, "archived": True, "permanent": False}

    # The neighbouring contract is untouched.
    assert _ids(client.get("/api/v1/contracts", headers=h).json()) == {cid2}
    assert client.get(f"/api/v1/contracts/{cid2}", headers=h).status_code == 200

    db = TestingSession()
    try:
        row = db.get(Contract, uuid.UUID(cid1))
        assert row.is_archived is True
        # Related records are kept (soft delete): clauses, parse and semantic
        # index rows are never cascade-deleted by an archive.
        assert db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid1)).count() == clauses_before
        assert db.query(ParsedDocument).filter(
            ParsedDocument.contract_id == uuid.UUID(cid1)).count() == parsed_before
        assert db.query(SemanticIndex).filter(
            SemanticIndex.organization_id == org).count() == semantic_before
        # Storage reference unchanged — no file cleanup on archive.
        assert row.storage_key == storage_key
    finally:
        db.close()

    # The archived contract's file is still downloadable (audit/restore), and
    # so is the untouched neighbour's.
    d1 = client.get(f"/api/v1/contracts/{cid1}/download", headers=h)
    assert d1.status_code == 200 and d1.content == pdf
    d2 = client.get(f"/api/v1/contracts/{cid2}/download", headers=h)
    assert d2.status_code == 200 and d2.content == PDF

    # Repeating the deletion is safe and idempotent.
    again = client.delete(f"/api/v1/contracts/{cid1}", headers=h)
    assert again.status_code == 200 and again.json()["already_archived"] is True
    assert _ids(client.get("/api/v1/contracts", headers=h).json()) == {cid2}


def test_failed_search_index_never_corrupts_postgresql(client, monkeypatch):
    token = register(client, "esfail@x.com")
    cid = _upload(client, token, "resilient.pdf")

    def boom(*args, **kwargs):
        raise RuntimeError("elasticsearch is down")

    monkeypatch.setattr("app.modules.search.es.index_document", boom)

    # Metadata writes still succeed: indexing is best-effort by contract.
    r = _patch(client, token, cid, contract_category="Vendor Agreement",
               is_important=True)
    assert r.status_code == 200, r.text
    assert r.json()["contract_category"] == "Vendor Agreement"
    assert r.json()["is_important"] is True

    # And so does the archive.
    assert client.delete(f"/api/v1/contracts/{cid}",
                         headers=auth(token)).status_code == 200

    db = TestingSession()
    try:
        row = db.get(Contract, uuid.UUID(cid))
        assert row.contract_type == "Vendor Agreement"
        assert row.is_important is True
        assert row.is_archived is True
    finally:
        db.close()

    # The API keeps serving PostgreSQL truth even while indexing fails.
    assert _get(client, token, cid)["contract_category"] == "Vendor Agreement"


# --------------------------------------------- Steps 2/3. category rules -----


def test_suggest_category_is_conservative_and_deterministic():
    assert suggest_category(filename="02_balanced_saas_agreement.pdf") == \
        "SaaS Subscription Agreement"
    assert suggest_category(filename="03_nda.pdf") == "Non-Disclosure Agreement"
    assert suggest_category(filename="04_employment_agreement.pdf") == \
        "Employment Agreement"
    assert suggest_category(filename="vendor_supply.pdf") == "Vendor Agreement"
    assert suggest_category(title="Master Services Agreement") == "Services Agreement"
    # Title beats the file name when both carry a signal.
    assert suggest_category(filename="scan_0001.pdf",
                            title="Mutual Non-Disclosure Agreement") == \
        "Non-Disclosure Agreement"
    # Ambiguous documents stay Unspecified — never guessed.
    assert suggest_category(filename="random_scans.pdf") is None
    assert suggest_category(title="Project Framework Agreement") is None
    assert suggest_category() is None


def test_parse_category_vocabulary():
    assert parse_category("Vendor Agreement") == "Vendor Agreement"
    assert parse_category("Unspecified") == UNSPECIFIED
    assert parse_category("  other / unspecified ") == UNSPECIFIED
    with pytest.raises(ValueError):
        parse_category("banana")
    with pytest.raises(ValueError):
        parse_category("")


def test_backfill_is_idempotent_and_never_overwrites_manual_values(client):
    token = register(client, "bf@x.com")
    nda = _upload(client, token, "03_nda.pdf")
    mystery = _upload(client, token, "mystery_file.pdf")
    employment = _upload(client, token, "04_employment_agreement.pdf")
    # A human already categorized this one: the backfill must not touch it.
    assert _patch(client, token, employment,
                  contract_category="Vendor Agreement").status_code == 200

    db = TestingSession()
    try:
        first = backfill_categories(db)
        assert first["updated"] == 1, first
        rows = {str(r.id): r.contract_type for r in db.query(Contract).all()}
        assert rows[nda] == "Non-Disclosure Agreement"
        assert rows[mystery] is None          # ambiguous → Other / Unspecified
        assert rows[employment] == "Vendor Agreement"  # manual value intact

        second = backfill_categories(db)
        assert second["updated"] == 0, second  # idempotent
    finally:
        db.close()

    # Backfilled values are immediately filterable.
    h = auth(token)
    assert _ids(client.get("/api/v1/contracts",
                           params={"category": "Non-Disclosure Agreement"},
                           headers=h).json()) == {nda}


# ------------------------------------------------ Step 12. dashboard KPI ----


def test_portfolio_summary_supplies_the_dashboard_kpis(client):
    token = register(client, "kpi@x.com")
    h = auth(token)
    starred = _upload(client, token, "kpi_one.pdf")
    _upload(client, token, "kpi_two.pdf")

    def summary():
        r = client.get("/api/v1/portfolio/summary", headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    assert summary()["important_contracts"] == 0
    _patch(client, token, starred, is_important=True)
    assert summary()["important_contracts"] == 1

    # Archived contracts never count as important.
    assert client.delete(f"/api/v1/contracts/{starred}", headers=h).status_code == 200
    assert summary()["important_contracts"] == 0

    # The other three Part-7 inputs are already supplied by this endpoint.
    s = summary()
    assert set(s) >= {"total_contracts",
                      "contracts_with_high_critical_unresolved",
                      "upcoming_renewals_30d", "important_contracts"}
    assert s["total_contracts"] >= 1
    assert isinstance(s["contracts_with_high_critical_unresolved"], int)
    assert isinstance(s["upcoming_renewals_30d"], int)
