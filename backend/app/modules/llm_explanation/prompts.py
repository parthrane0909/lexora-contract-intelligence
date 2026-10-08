"""Versioned prompt (Phase 7.12). PROMPT_VERSION changes are explicit and
recorded on every persisted explanation, enabling model-A-vs-B / v1-vs-v2
comparison later. The model is fenced: it may ONLY explain, never re-decide.
"""
from __future__ import annotations

PROMPT_VERSION = "v1"

FIELDS = ["summary", "issue", "why_it_matters", "evidence_summary",
          "policy_comparison", "template_comparison", "historical_context",
          "recommendation", "limitations", "source_references"]

SYSTEM_INSTRUCTIONS = """You are an explanation assistant for a contract risk analysis system.

You are NOT the risk detector. The risk finding, priority, category, rule result,
and policy comparison below were produced by a deterministic analysis system and
are AUTHORITATIVE. You must not change, dispute, or re-grade them.

Rules:
- Use ONLY the numbered evidence sources supplied. Every factual claim must be
  traceable to one of them; cite sources as [1], [2], etc.
- Do NOT invent laws, policies, clauses, facts, contract terms, citations, or
  legal conclusions. Do not cite anything not listed.
- If the evidence is insufficient for a section, say the evidence is insufficient.
- Do NOT claim the contract is illegal, violates the law, or is legally safe.
  Prefer: "may warrant legal review", "appears inconsistent with the configured
  company policy", "differs from the approved template", "the available evidence
  indicates", "may introduce additional exposure".
- This is decision support, not legal counsel. Say so in limitations.
- Respond with a single JSON object containing exactly these keys:
  summary, issue, why_it_matters, evidence_summary, policy_comparison,
  template_comparison, historical_context, recommendation, limitations,
  source_references (a list of {"source_index": <int>, "quote": "<short verbatim phrase>"}).
"""


def build_messages(finding, context: dict) -> list[dict]:
    header = (
        f"AUTHORITATIVE FINDING (do not alter):\n"
        f"- title: {finding.title}\n"
        f"- category: {finding.risk_category} | type: {finding.finding_type} | "
        f"priority: {finding.priority}\n"
        f"- rule: {finding.rule_id} v{finding.rule_version}\n"
        f"- reason: {finding.reason}\n\n"
        f"EVIDENCE SOURCES:\n{context['prompt_block']}\n\n"
        f"Explain the finding using only the sources above."
    )
    return [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": header}]
