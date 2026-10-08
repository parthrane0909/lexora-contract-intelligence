"""Part 2 — Elasticsearch contract discovery search (spec tests A–N).

Requires a reachable Elasticsearch (default http://localhost:9200); the whole
module skips with a clear reason when there is none. Everything runs against
the dedicated `contract_search_test` index configured in conftest.py — the
live `contract_search` alias and its data are never touched.

    A  exact filename              I  no results
    B  filename without .pdf       J  organization isolation (hard criterion)
    C  prefix                      K  deleted/archived contract disappears
    D  middle word                 L  metadata update refreshes the index
    E  multiple words              M  reindex idempotency
    F  underscore/space/extension  N  Elasticsearch unavailable → clear 503
    G  typo / fuzzy                   (+ empty query rejected, ranking)

Qdrant/semantic search is intentionally out of scope here: it is covered by
test_semantic_search_api.py / test_vector_store.py and must stay untouched.
"""
import io
import uuid

import pytest

from app.models import Contract
from app.modules.search import es, index as search_index
from conftest import TestingSession, auth, register

PDF = b"%PDF-1.4 fake contract bytes"


def _es_reachable() -> bool:
    try:
        es.cluster_info()
        return True
    except Exception:  # noqa: BLE001 — any failure means "skip, not fail"
        return False


pytestmark = pytest.mark.skipif(
    not _es_reachable(),
    reason="Elasticsearch not reachable at ELASTICSEARCH_URL "
           "(start it with: docker compose up -d elasticsearch)",
)


@pytest.fixture(autouse=True)
def fresh_index():
    """Each test starts from an empty search index (idempotent reset)."""
    search_index.ensure_index(force=True)
    yield


def upload(client, token, filename):
    r = client.post(
        "/api/v1/contracts/upload",
        headers=auth(token),
        files={"file": (filename, io.BytesIO(PDF), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def search(client, token, q, **params):
    return client.get(
        "/api/v1/search/contracts", params={"q": q, **params}, headers=auth(token)
    )


def filenames(payload: dict) -> list[str]:
    return [hit["filename"] for hit in payload["results"]]


def assert_ranking(body: dict, expected_order: list[str]):
    """Every expected file is present, in exactly this relative order."""
    got = filenames(body)
    kept = [name for name in got if name in expected_order]
    assert kept == expected_order, f"order mismatch: {got}"


def assert_top_group(body: dict, expected: list[str]):
    """The top len(expected) results are exactly `expected`, in any order.

    Used where documents genuinely tie: identical scores fall back to the
    same recency tie-break as the contract list, so the order inside the
    group is not part of the contract — being on top is.
    """
    got = filenames(body)
    assert sorted(got[: len(expected)]) == sorted(expected), (
        f"expected {sorted(expected)} on top, got {got}"
    )


# --------------------------------------------------------------------- A ---
def test_a_exact_filename_ranks_first(client):
    token = register(client, email="a@x.com")
    for name in ("01_clear_high_risk_vendor.pdf", "01_high_risk_vendor_agreement.pdf",
                 "02_balanced_saas_agreement.pdf"):
        upload(client, token, name)

    body = search(client, token, "02_balanced_saas_agreement.pdf").json()
    assert body["total"] == 1, body
    assert body["results"][0]["filename"] == "02_balanced_saas_agreement.pdf"
    assert body["results"][0]["score"] > 0
    # DTO shape: application fields only, never raw Elasticsearch internals
    hit = body["results"][0]
    assert set(hit) >= {"contract_id", "filename", "analysis_status",
                        "processing_status", "score"}
    assert "_source" not in hit and "_index" not in hit


# --------------------------------------------------------------------- B ---
def test_b_filename_without_extension(client):
    token = register(client, email="b@x.com")
    upload(client, token, "03_nda.pdf")
    upload(client, token, "04_employment_agreement.pdf")

    body = search(client, token, "03_nda").json()
    assert filenames(body)[0] == "03_nda.pdf"
    body = search(client, token, "nda").json()
    assert filenames(body)[0] == "03_nda.pdf"


# --------------------------------------------------------------------- C ---
def test_c_prefix_matching(client):
    token = register(client, email="c@x.com")
    for name in ("01_clear_high_risk_vendor.pdf", "01_high_risk_vendor_agreement.pdf",
                 "03_nda.pdf"):
        upload(client, token, name)

    body = search(client, token, "vend").json()
    assert_top_group(body, ["01_clear_high_risk_vendor.pdf",
                            "01_high_risk_vendor_agreement.pdf"])
    # a single letter must already narrow the list (as-you-type from "b")
    body = search(client, token, "v").json()
    assert set(filenames(body)) == {"01_clear_high_risk_vendor.pdf",
                                    "01_high_risk_vendor_agreement.pdf"}


# --------------------------------------------------------------------- D ---
def test_d_middle_word(client):
    token = register(client, email="d@x.com")
    for name in ("02_balanced_saas_agreement.pdf", "04_employment_agreement.pdf"):
        upload(client, token, name)

    # "saas" sits in the MIDDLE of 02_balanced_saas_agreement.pdf
    body = search(client, token, "saas").json()
    assert body["total"] == 1
    assert body["results"][0]["filename"] == "02_balanced_saas_agreement.pdf"


# --------------------------------------------------------------------- E ---
def test_e_multiple_words(client):
    token = register(client, email="e@x.com")
    for name in ("01_clear_high_risk_vendor.pdf", "01_high_risk_vendor_agreement.pdf",
                 "05_low_risk_contract.pdf", "06_adversarial_indirect_risk_contract.pdf"):
        upload(client, token, name)

    body = search(client, token, "risk vendor").json()
    assert_top_group(body, ["01_clear_high_risk_vendor.pdf",
                            "01_high_risk_vendor_agreement.pdf"])
    # files that only contain one of the two words must rank below both
    assert body["results"][0]["score"] > body["results"][-1]["score"]
    assert filenames(body).index("05_low_risk_contract.pdf") >= 2


# --------------------------------------------------------------------- F ---
def test_f_separators_and_normalization(client):
    token = register(client, email="f@x.com")
    upload(client, token, "02_balanced_saas_agreement.pdf")
    upload(client, token, "01_high_risk_vendor_agreement.pdf")

    for query in ("balanced saas",            # spaces instead of underscores
                  "02 balanced saas agreement",
                  "02-balanced-saas-agreement",   # dashes
                  "02.balanced.saas.agreement",   # dots
                  "02_balanced_saas_agreement",   # underscore, no extension
                  "Balanced SAAS"):               # case insensitive
        body = search(client, token, query).json()
        assert body["results"], f"no result for {query!r}"
        assert body["results"][0]["filename"] == "02_balanced_saas_agreement.pdf", query

    body = search(client, token, "high risk vendor").json()
    assert body["results"][0]["filename"] == "01_high_risk_vendor_agreement.pdf"


# --------------------------------------------------------------------- G ---
def test_g_typo_fuzzy(client):
    token = register(client, email="g@x.com")
    for name in ("02_balanced_saas_agreement.pdf", "03_nda.pdf"):
        upload(client, token, name)

    # reasonable typo: one transposition
    body = search(client, token, "balnced").json()
    assert body["total"] >= 1
    assert body["results"][0]["filename"] == "02_balanced_saas_agreement.pdf"

    body = search(client, token, "balnced saas").json()
    assert body["results"] and body["results"][0]["filename"] == "02_balanced_saas_agreement.pdf"


# --------------------------------------------------------------------- H ---
def test_h_ranking_ladder(client):
    """exact > whole word > token prefix > fuzzy — strictly decreasing."""
    token = register(client, email="h@x.com")
    for name in ("vendor.pdf",           # exact filename for "vendor"
                 "vendor_master.pdf",    # whole token, not the whole name
                 "vendors_ltd.pdf",      # token only reached by a prefix
                 "vendr.pdf"):           # typo only reachable by fuzziness
        upload(client, token, name)

    body = search(client, token, "vendor").json()
    assert_ranking(body, ["vendor.pdf", "vendor_master.pdf",
                          "vendors_ltd.pdf", "vendr.pdf"])
    scores = [hit["score"] for hit in body["results"]]
    assert scores == sorted(scores, reverse=True), scores
    assert len(set(scores)) == len(scores), "scores must be distinguishable"


# --------------------------------------------------------------------- I ---
def test_i_no_results(client):
    token = register(client, email="i@x.com")
    upload(client, token, "03_nda.pdf")

    body = search(client, token, "nonexistent-xyz")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["total"] == 0
    assert payload["results"] == []
    assert payload["query"] == "nonexistent-xyz"


# --------------------------------------------------------------------- J ---
def test_j_organization_isolation(client):
    """Org B must never see Org A's contract — hard security criterion."""
    token_a = register(client, email="a@orga-example.com", org="OrgA")
    upload(client, token_a, "secret_vendor_contract.pdf")
    assert client.post("/api/v1/search/reindex-contracts",
                       headers=auth(token_a)).json()["indexed"] == 1

    token_b = register(client, email="b@orgb-example.com", org="OrgB")
    upload(client, token_b, "my_msa_notes.pdf")

    # B searches for every word of A's filename → nothing of A's, ever
    for query in ("secret vendor", "secret_vendor_contract.pdf", "secret",
                  "vendor contract"):
        body = search(client, token_b, query)
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["total"] == 0, (query, filenames(payload))
        assert "secret_vendor_contract.pdf" not in filenames(payload)

    # B's own reindex reconciles only B's documents (A's stay intact)
    re_b = client.post("/api/v1/search/reindex-contracts", headers=auth(token_b))
    assert re_b.status_code == 200 and re_b.json()["indexed"] == 1
    assert re_b.json()["removed"] == 0
    assert es.count(search_index.ensure_index()) == 2  # A's + B's

    # each organization still finds only its own contracts
    assert filenames(search(client, token_b, "msa").json()) == ["my_msa_notes.pdf"]
    assert filenames(search(client, token_b, "notes").json()) == ["my_msa_notes.pdf"]
    assert filenames(search(client, token_a, "secret").json()) == \
        ["secret_vendor_contract.pdf"]
    # B cannot reach A's contract id through the search endpoint either
    assert search(client, token_b, "secret").json()["results"] == []


def test_j2_overlapping_terms_never_leak_the_other_org(client):
    """Same search words in two orgs: results stay strictly within the caller."""
    token_a = register(client, email="a2@orga-example.com", org="OrgA2")
    upload(client, token_a, "secret_vendor_contract.pdf")
    a_id = upload(client, token_a, "vendor_master_agreement.pdf")["id"]

    token_b = register(client, email="b2@orgb-example.com", org="OrgB2")
    b_id = upload(client, token_b, "my_vendor_notes.pdf")["id"]

    body = search(client, token_b, "vendor").json()
    assert filenames(body) == ["my_vendor_notes.pdf"]
    assert [h["contract_id"] for h in body["results"]] == [b_id]
    assert a_id not in [h["contract_id"] for h in body["results"]]

    body = search(client, token_a, "vendor").json()
    assert sorted(filenames(body)) == ["secret_vendor_contract.pdf",
                                       "vendor_master_agreement.pdf"]
    assert b_id not in [h["contract_id"] for h in body["results"]]


# --------------------------------------------------------------------- K ---
def test_k_deleted_contract_disappears(client):
    token = register(client, email="k@x.com")
    doomed = upload(client, token, "doomed_contract.pdf")
    assert search(client, token, "doomed").json()["total"] == 1

    # (a) archive = this application's delete semantics (DELETE /contracts/{id})
    assert client.delete(f"/api/v1/contracts/{doomed['id']}",
                         headers=auth(token)).status_code == 200
    assert search(client, token, "doomed").json()["total"] == 0
    # ...but an explicit include_archived still finds it (state was indexed)
    assert search(client, token, "doomed",
                  include_archived="true").json()["total"] == 1

    # (b) simulate a hard delete that never published an index delete:
    # PostgreSQL verification must drop the stale document.
    gone = upload(client, token, "hard_deleted_contract.pdf")
    assert search(client, token, "hard_deleted").json()["total"] == 1
    session = TestingSession()
    session.query(Contract).filter(
        Contract.id == uuid.UUID(gone["id"])).delete()
    session.commit()
    session.close()
    assert search(client, token, "hard_deleted").json()["total"] == 0


# --------------------------------------------------------------------- L ---
def test_l_metadata_update_refreshes_index(client):
    token = register(client, email="l@x.com")
    up = upload(client, token, "msa.pdf")
    assert search(client, token, "globex").json()["total"] == 0

    r = client.patch(f"/api/v1/contracts/{up['id']}", headers=auth(token),
                     json={"counterparty": "Globex Industries"})
    assert r.status_code == 200, r.text

    body = search(client, token, "globex").json()
    assert body["total"] == 1
    assert body["results"][0]["filename"] == "msa.pdf"
    assert body["results"][0]["counterparty"] == "Globex Industries"

    # renaming does not exist as an endpoint; contract_type is the other
    # piece of searchable member metadata
    r = client.patch(f"/api/v1/contracts/{up['id']}", headers=auth(token),
                     json={"contract_type": "reseller"})
    assert r.status_code == 200
    body = search(client, token, "reseller").json()
    assert body["total"] == 1 and body["results"][0]["filename"] == "msa.pdf"


# --------------------------------------------------------------- L (new) ---
def test_l2_new_upload_is_indexed_immediately(client):
    """Acceptance criterion: a NEW contract enters the index on creation.

    Backfill (test M) only covers contracts that already existed; this one
    proves the upload path itself writes to Elasticsearch, synchronously,
    without waiting for any background job.
    """
    token = register(client, email="l2@x.com")
    assert es.count(search_index.ensure_index()) == 0

    upload(client, token, "fresh_nda_upload.pdf")

    assert es.count(search_index.ensure_index()) == 1  # written to ES, not just PG
    body = search(client, token, "fresh nda upload").json()
    assert body["total"] == 1
    assert body["results"][0]["filename"] == "fresh_nda_upload.pdf"
    # a one-word typo still resolves (fuzzy tier): "uplod" is not a prefix of
    # "upload", so only typo tolerance can bring it back.
    assert search(client, token, "uplod").json()["total"] >= 1


# --------------------------------------------------------------------- M ---
def test_m_reindex_idempotent(client):
    token = register(client, email="m@x.com")
    for name in ("alpha_contract.pdf", "beta_contract.pdf", "gamma_contract.pdf"):
        upload(client, token, name)
    # index it "by hand" first so the backfill is exercised on live docs
    assert es.count(search_index.ensure_index()) == 3

    first = client.post("/api/v1/search/reindex-contracts", headers=auth(token))
    second = client.post("/api/v1/search/reindex-contracts", headers=auth(token))
    assert first.status_code == 200 and second.status_code == 200
    f1, f2 = first.json(), second.json()
    assert f1["indexed"] == f2["indexed"] == 3
    assert f1["failed"] == f2["failed"] == 0
    assert f1["contracts_in_postgres"] == 3
    assert f1["removed"] == f2["removed"] == 0
    assert es.count(search_index.ensure_index()) == 3  # no duplicates

    # reindex never mutates PostgreSQL
    session = TestingSession()
    rows = session.query(Contract).order_by(Contract.filename).all()
    assert [r.filename for r in rows] == ["alpha_contract.pdf",
                                          "beta_contract.pdf", "gamma_contract.pdf"]
    assert all(not r.is_archived for r in rows)
    session.close()

    # every contract still resolvable exactly once
    assert search(client, token, "alpha").json()["total"] == 1


# --------------------------------------------------------------------- N ---
def test_n_elasticsearch_unavailable(client, monkeypatch):
    token = register(client, email="n@x.com")
    upload(client, token, "anything_contract.pdf")

    # point the client at a dead port — no cluster behind it
    monkeypatch.setattr(es, "es_url", lambda: "http://127.0.0.1:9")

    r = search(client, token, "anything")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "temporarily unavailable" in detail
    assert "127.0.0.1" not in detail  # no internal endpoints leaked

    # status explains the situation instead of pretending to be healthy
    st = client.get("/api/v1/search/status", headers=auth(token)).json()
    assert st["configured"] is True and st["available"] is False
    assert st["detail"]

    # PostgreSQL paths are untouched by the outage
    listing = client.get("/api/v1/contracts", headers=auth(token))
    assert listing.status_code == 200 and len(listing.json()) == 1

    # a new upload still succeeds (the indexing hook fails soft)
    up = upload(client, token, "another_contract.pdf")
    assert up["id"]

    # reindex reports the outage rather than corrupting anything
    rr = client.post("/api/v1/search/reindex-contracts", headers=auth(token))
    assert rr.status_code == 503
    session = TestingSession()
    assert session.query(Contract).count() == 2
    session.close()


# ------------------------------------------------------------ extras --------
def test_empty_query_is_rejected_and_not_sent_to_es(client):
    token = register(client, email="x@x.com")
    upload(client, token, "03_nda.pdf")
    r = client.get("/api/v1/search/contracts", params={"q": ""},
                   headers=auth(token))
    assert r.status_code == 422  # min_length=1: empty never reaches ES
    r = client.get("/api/v1/search/contracts", params={"q": "   "},
                   headers=auth(token))
    assert r.status_code == 400 and "empty" in r.json()["detail"].lower()


def test_search_requires_authentication(client):
    upload(client, register(client, email="auth@x.com"), "03_nda.pdf")
    assert client.get("/api/v1/search/contracts", params={"q": "nda"}).status_code == 401
    assert client.post("/api/v1/search/reindex-contracts").status_code == 401


def test_statuses_come_from_postgresql_not_the_index(client):
    """The index may hold an older status copy; the response must not."""
    token = register(client, email="s@x.com")
    up = upload(client, token, "stale_status.pdf")
    assert search(client, token, "stale").json()["results"][0]["analysis_status"] == "pending"

    # flip the row directly in PostgreSQL (no hook fires on purpose)
    session = TestingSession()
    row = session.get(Contract, uuid.UUID(up["id"]))
    row.analysis_status = "complete"
    row.processing_status = "processed"
    session.commit()
    session.close()

    hit = search(client, token, "stale").json()["results"][0]
    assert hit["analysis_status"] == "complete"
    assert hit["processing_status"] == "processed"
