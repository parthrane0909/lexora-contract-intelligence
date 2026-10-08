"""Deterministic explanation-pipeline evaluation (Phase 7.28).

Measures the grounding pipeline — NOT legal accuracy:
  1. citation validity (all citations resolve to supplied evidence)
  2. schema validity (required fields present)
  3. unsupported-claim rejection (banned phrases / bad citations caught)
  4. evidence recall (expected source types present in context)

Providers are deterministic fakes (good / bad_citation / unsupported_claim),
so the numbers measure the VALIDATOR + EVIDENCE layers, which is exactly
what this phase owns. Real-LLM output quality is reported separately from
the live smoke test (provider/model/prompt recorded, no accuracy claimed).

Usage:
  python -m app.modules.llm_explanation.evaluate --out /tmp/opencode/eval7.json
"""
from __future__ import annotations

import argparse
import json


def _sources():
    return [
        {"evidence_id": "ev-1", "source_type": "contract_clause", "source_id": "c1",
         "organization_id": "org-1", "title": "Contract clause",
         "text": "Supplier liability shall be capped at $50,000 for all claims.",
         "category": "liability", "page": 8, "section": "8.1",
         "similarity_score": None, "provenance": "database",
         "retrieval_method": "direct", "rank": 0, "metadata": {}},
        {"evidence_id": "ev-2", "source_type": "policy", "source_id": "p1",
         "organization_id": "org-1", "title": "Company policy [liability] v2",
         "text": "Company policy [liability]: max_amount = 10000.",
         "category": "liability", "page": None, "section": None,
         "similarity_score": None, "provenance": "policy_engine",
         "retrieval_method": "direct", "rank": 1, "metadata": {}},
        {"evidence_id": "ev-3", "source_type": "template_clause", "source_id": "t1",
         "organization_id": "org-1", "title": "Approved template Std v1",
         "text": "Supplier liability shall be capped at $10,000.",
         "category": "liability", "page": 3, "section": "8.1",
         "similarity_score": None, "provenance": "database",
         "retrieval_method": "direct", "rank": 2, "metadata": {}},
    ]


def _finding():
    class _F:
        title = "Liability cap exceeds company policy"
        risk_category = "liability"
        finding_type = "policy_deviation"
        priority = "high"
        rule_id = "LIAB-003"
        rule_version = "1.0"
        reason = "Cap above policy maximum."

    return _F()


def run_eval() -> dict:
    from app.modules.llm_explanation.context import build_context
    from app.modules.llm_explanation.prompts import PROMPT_VERSION, build_messages
    from app.modules.llm_explanation.providers import FakeProvider
    from app.modules.llm_explanation.validator import GroundingError, validate_explanation

    sources = _sources()
    ctx = build_context(_finding(), sources)
    messages = build_messages(_finding(), ctx)

    results: dict = {"prompt_version": PROMPT_VERSION, "cases": {}}
    # 1-2. good provider: schema + citations must validate
    out = FakeProvider("good").generate_structured(messages)
    try:
        validate_explanation(out, ctx["sources"], "org-1")
        results["cases"]["good_schema_valid"] = True
        results["cases"]["good_citations_valid"] = True
    except GroundingError as e:
        results["cases"][f"UNEXPECTED: {e}"] = False
    # 3a. bad citations rejected
    try:
        bad = FakeProvider("bad_citation").generate_structured(messages)
        validate_explanation(bad, ctx["sources"], "org-1")
        results["cases"]["bad_citation_rejected"] = False
    except GroundingError:
        results["cases"]["bad_citation_rejected"] = True
    # 3b. unsupported legal conclusions rejected
    try:
        claim = FakeProvider("unsupported_claim").generate_structured(messages)
        validate_explanation(claim, ctx["sources"], "org-1")
        results["cases"]["unsupported_claim_rejected"] = False
    except GroundingError:
        results["cases"]["unsupported_claim_rejected"] = True
    # 4. evidence recall over expected source types
    types = {s["source_type"] for s in ctx["sources"]}
    results["cases"]["evidence_recall"] = {
        t: (t in types) for t in ("contract_clause", "policy", "template_clause")}
    results["citation_validity_rate"] = (
        1.0 if results["cases"].get("good_citations_valid") else 0.0)
    results["schema_validity_rate"] = (
        1.0 if results["cases"].get("good_schema_valid") else 0.0)
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate explanation grounding pipeline")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = run_eval()
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
