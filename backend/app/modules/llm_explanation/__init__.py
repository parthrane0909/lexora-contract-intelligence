"""Evidence-grounded risk explanations (Phase 7).

Pipeline: RiskFinding → evidence (Phase 6 retrieval + DB sources) →
numbered context → LLM explanation → grounding validation → append-only
persistence → human reviewer. The LLM explains; it never detects, grades,
or overrides anything.
"""
from app.modules.llm_explanation.service import (  # noqa: F401
    explanation_history,
    generate_explanation,
    latest_explanation,
)
