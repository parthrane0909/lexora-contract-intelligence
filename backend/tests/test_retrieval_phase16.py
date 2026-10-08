"""Phase 16 retrieval/RAG regression suite (hermetic: hash embeddings +
InMemory store). Fixture texts are synthetic scaffolding for plumbing tests —
any metric here is labeled FIXTURE, never production quality."""
import pytest

from app.modules.retrieval import indexer
from app.modules.retrieval.vector_store import InMemoryVectorStore


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    store = InMemoryVectorStore(collection="p16")
    monkeypatch.setattr(indexer, "_STORE", store)
    yield store
    reset_provider()


def _hit(score, otype="clause", category="payment", oid="o1"):
    return {"score": score, "payload": {"object_type": otype, "category": category,
                                       "object_id": oid}}


def test_rerank_versioned_and_deterministic():
    from app.modules.retrieval import rerank as rr

    assert rr.RERANKER_VERSION == "v1"
    hits = [_hit(0.5, category="other"), _hit(0.55, category="payment")]
    out = rr.rerank(hits, query_category="payment", top_k=2)
    assert out[0]["payload"]["category"] == "payment"  # boost overcomes gap
    assert out[0]["final_score"] == round(0.55 + 0.15 + 0.05, 4)
    assert out[0]["rerank_explain"]["reranker_version"] == "v1"
    # no category: pure cosine order preserved
    out2 = rr.rerank(hits, query_category=None, top_k=2)
    assert [h["payload"]["category"] for h in out2] == ["payment", "other"]
    # source priority: policy outranks unknown type at equal cosine
    out3 = rr.rerank([_hit(0.5, otype="mystery"), _hit(0.5, otype="policy")],
                     query_category="payment")
    assert out3[0]["payload"]["object_type"] == "policy"


def test_rerank_never_crashes_on_malformed():
    from app.modules.retrieval import rerank as rr

    assert rr.rerank([], query_category="x") == []
    assert rr.rerank([{"score": 0.1}], query_category="x")[0]["final_score"] == 0.1


def test_context_diagnostics_counts():
    from app.modules.llm_explanation import diagnostics as dg

    built = {"sources": [
        {"source_type": "contract_clause", "text": "Cap is $5.", "category": "liability"},
        {"source_type": "policy", "text": "Cap is $5.", "category": "liability"},
        {"source_type": "policy", "text": "", "category": "other"}],
        "char_count": 100, "truncated": False}
    d = dg.diagnose_context(built, "liability")
    assert d["n_sources"] == 3 and d["duplicate_texts"] == 1
    assert d["empty_texts"] == 1 and d["has_mandatory_clause"] is True
    assert d["category_match_rate"] == 0.5
    assert d["source_type_dist"] == {"contract_clause": 1, "policy": 2}


def test_sufficiency_statuses():
    from app.modules.llm_explanation import diagnostics as dg

    clause = {"source_type": "contract_clause", "text": "t"}
    pol = {"source_type": "policy", "text": "p", "category": "liability"}
    other = {"source_type": "historical_clause", "text": "h", "category": "other"}
    assert dg.assess_sufficiency([clause])["status"] == "WEAK"
    assert dg.assess_sufficiency([clause, other], "liability")["status"] == "PARTIAL"
    s = dg.assess_sufficiency([clause, other, pol], "liability")
    assert s["status"] == "SUFFICIENT" and "policy" in s["matched_types"]


def test_org_isolation_vector_path(hermetic):
    """Cross-org points must never surface, even with identical text."""
    from app.modules.retrieval import search as search_mod
    from app.modules.retrieval.providers import get_provider
    from app.modules.retrieval.vector_store import org_filter, point_id

    prov = get_provider()
    for org, txt in (("org-a", "Liability is unlimited here."),
                     ("org-b", "Liability is unlimited here.")):
        v = prov.embed_text(txt)
        hermetic.upsert([{"id": point_id(org, "clause", f"c-{org}", prov.model_name),
                          "vector": v,
                          "payload": {"org_id": org, "object_type": "clause",
                                      "object_id": f"c-{org}",
                                      "embedding_model": prov.model_name}}])
    hits = hermetic.search(prov.embed_text("unlimited liability"), 10,
                           org_filter("org-a"))
    assert {h["payload"]["org_id"] for h in hits} == {"org-a"}
    assert all(h["payload"]["object_id"] == "c-org-a" for h in hits)


def test_empty_query_rejected_and_no_results_explicit(hermetic):
    from app.modules.retrieval import search as search_mod
    from app.modules.retrieval.providers import EmptyTextError, get_provider

    # empty query raises before any store access
    import uuid
    from unittest.mock import MagicMock

    db = MagicMock()
    with pytest.raises(EmptyTextError):
        search_mod.semantic_search(db, org_id=uuid.uuid4(), query="   ",
                                   provider=get_provider(), store=hermetic)
    # unknown org / empty store yields explicit empty results, not an error
    hits = hermetic.search(get_provider().embed_text("anything"), 5,
                           {"org_id": "org-nobody"})
    assert hits == []


def test_stale_and_mismatched_provenance_rejected():
    """Evidence items pointing at missing/other-org sources must be dropped
    by the enrichment path (unknown ids simply yield no text)."""
    from app.modules.retrieval import search as search_mod

    hits = [{"score": 0.9, "payload": {"object_type": "clause",
                                      "object_id": "00000000-0000-0000-0000-000000000000",
                                      "text_snippet": "stale snippet"}}]
    import uuid
    from unittest.mock import MagicMock

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    out = search_mod._enrich(db, hits)
    assert out[0]["text"] == "stale snippet"  # snippet kept, flagged by absence
    assert "contract_filename" not in out[0]  # no provenance invented


def test_duplicate_evidence_deduped_in_context():
    from app.modules.llm_explanation import diagnostics as dg

    built = {"sources": [
        {"source_type": "contract_clause", "text": "Same text here.", "category": "x"},
        {"source_type": "policy", "text": "Same Text Here. ", "category": "x"}],
        "char_count": 10, "truncated": False}
    assert dg.diagnose_context(built)["duplicate_texts"] == 1


def test_rerank_config_recorded():
    from app.modules.retrieval import rerank as rr

    assert isinstance(rr.W_CATEGORY, float) and isinstance(rr.W_SOURCE, float)
    assert set(rr.SOURCE_PRIORITY) >= {"clause", "policy", "template_clause"}
