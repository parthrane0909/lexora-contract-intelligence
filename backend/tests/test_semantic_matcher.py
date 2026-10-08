"""Semantic matcher + eval math: thresholds, provenance, metric correctness."""
import os

import pytest


@pytest.fixture(autouse=True)
def _hash_backend(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    yield
    reset_provider()


def _clauses():
    from app.modules.comparison.matching import normalize  # noqa (protocol compat check)

    class _C:
        def __init__(self, text, section=None, title=None):
            self.text = text
            self.section_number = section
            self.title = title
            self.predicted_category = None

    return _C, normalize


def test_registered_behind_protocol():
    from app.modules.comparison.matching import get_matcher
    from app.modules.retrieval.semantic_matcher import SemanticClauseMatcher

    m = get_matcher("semantic")
    assert isinstance(m, SemanticClauseMatcher) and m.name == "semantic"
    assert m.version == "1.0"


def test_strong_pair_scores_high_with_method():
    from app.modules.retrieval.semantic_matcher import SemanticClauseMatcher

    _C, _ = _clauses()
    t = [_C("Supplier liability is capped at $500,000 for all claims.")]
    c = [_C("Supplier's total liability shall not exceed $500,000."),
         _C("The office pantry is stocked on Fridays.")]
    m = SemanticClauseMatcher()
    pairs = m.match(t, c)
    mate = [(cc, method) for tt, cc, method in pairs if tt is not None][0]
    assert mate[0] is not None and mate[0].text.startswith("Supplier's total")
    assert mate[1].startswith("semantic:")  # band depends on geometry; scored either way
    score = float(mate[1].split(":")[2])
    assert score >= m.match_threshold
    # band labeling is deterministic given thresholds:
    relabelled = SemanticClauseMatcher(strong_threshold=score - 0.01)
    method2 = [md for tt, _, md in relabelled.match(t, c) if tt is not None][0]
    assert method2.startswith("semantic:strong:")
    leftovers = [(tt, cc) for tt, cc, _ in pairs if tt is None]
    assert len(leftovers) == 1  # pantry clause unmatched


def test_threshold_knobs():
    from app.modules.retrieval.semantic_matcher import SemanticClauseMatcher

    _C, _ = _clauses()
    t = [_C("Payment is due within thirty days of invoice.")]
    c = [_C("Termination requires sixty days written notice.")]
    strict = SemanticClauseMatcher(match_threshold=0.99)
    assert strict.match(t, c)[0][1] is None  # unrelated stays unmatched
    loose = SemanticClauseMatcher(match_threshold=0.0)
    mate = [cc for tt, cc, _ in loose.match(t, c) if tt is not None][0]
    assert mate is not None  # threshold 0 forces a (weak, labelled) pairing


def test_empty_text_never_guesses():
    from app.modules.retrieval.semantic_matcher import SemanticClauseMatcher

    _C, _ = _clauses()
    pairs = SemanticClauseMatcher().match([_C("   ")], [_C("Real clause text here.")])
    assert pairs[0][1] is None and pairs[1][0] is None


def test_eval_metric_math():
    from app.modules.retrieval.evaluate import precision_at_k, recall_at_k

    ranked = ["a", "b", "c", "d"]
    rel = {"b", "d", "z"}
    assert round(recall_at_k(ranked, rel, 2), 4) == round(1 / 3, 4)
    assert precision_at_k(ranked, rel, 2) == 0.5
    assert round(recall_at_k(ranked, rel, 4), 4) == round(2 / 3, 4)
    assert recall_at_k([], set(), 3) == 1.0
    assert os.environ.get("EMBEDDING_PROVIDER") == "hash"  # hermetic here


def test_matcher_env_thresholds_respected(monkeypatch):
    import app.modules.retrieval.semantic_matcher as sm

    monkeypatch.setenv("SEMANTIC_MATCH_THRESHOLD", "0.10")
    monkeypatch.setenv("SEMANTIC_STRONG_THRESHOLD", "0.95")
    m = sm.SemanticClauseMatcher()
    assert (m.match_threshold, m.strong_threshold) == (0.10, 0.95)
