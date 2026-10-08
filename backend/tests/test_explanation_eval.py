"""Grounding-pipeline eval assertions (deterministic; no external LLM)."""
from app.modules.llm_explanation.evaluate import run_eval


def test_grounding_eval_rates():
    report = run_eval()
    assert report["prompt_version"] == "v1"
    assert report["citation_validity_rate"] == 1.0
    assert report["schema_validity_rate"] == 1.0
    assert report["cases"]["bad_citation_rejected"] is True
    assert report["cases"]["unsupported_claim_rejected"] is True
    assert report["cases"]["evidence_recall"] == {
        "contract_clause": True, "policy": True, "template_clause": True}
