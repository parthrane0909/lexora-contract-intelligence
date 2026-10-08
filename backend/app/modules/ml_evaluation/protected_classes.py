"""Canonical protected classes (Phase 13). Single source of truth — the
promotion gates import from here. These are the supported contract
categories where a regression matters most for review prioritization.
"Protected" is an engineering guardrail, not a legal-significance claim.
"""
PROTECTED_CLASSES = (
    "liability",
    "indemnification",
    "termination",
    "payment",
    "confidentiality",
    "governing_law",
    "renewal",
    "warranty",
)

MAX_PROTECTED_CLASS_REGRESSION = 0.03
