"""Versioned risk taxonomy (Phase 4.1).

Covers the Phase 3 taxonomy but stays honest about support levels:
- ml_support: can the v1 classifier predict this category at all?
- rules_support: does the v1 rule engine have deterministic rules for it?

data_privacy is RESERVED in Phase 3 (no training data) so it has neither;
intellectual_property has classification but no reliable deterministic
extraction yet, so v1 ships no IP rules rather than noisy ones.
"""
from app.modules.ml_nlp.taxonomy import CATEGORIES as CLAUSE_CATEGORIES

RISK_TAXONOMY_VERSION = "1.0"

RISK_CATEGORIES: dict[str, dict] = {
    "liability": {"ml_support": True, "rules_support": True},
    "indemnification": {"ml_support": True, "rules_support": True},
    "termination": {"ml_support": True, "rules_support": True},
    "payment": {"ml_support": True, "rules_support": True},
    "confidentiality": {"ml_support": True, "rules_support": True},
    "intellectual_property": {"ml_support": True, "rules_support": False},
    "data_privacy": {"ml_support": False, "rules_support": False},
    "renewal": {"ml_support": True, "rules_support": True},
    "governing_law": {"ml_support": True, "rules_support": True},
    "jurisdiction": {"ml_support": True, "rules_support": True},
    "warranty": {"ml_support": True, "rules_support": True},
    "insurance": {"ml_support": True, "rules_support": True},
    "audit": {"ml_support": True, "rules_support": False},
    "dispute_resolution": {"ml_support": True, "rules_support": False},
    "other": {"ml_support": True, "rules_support": False},
}

FINDING_TYPES = (
    "rule",  # deterministic problematic characteristic
    "policy_deviation",  # differs from the org's configured position
    "missing_term",  # expected term absent (subtype of rule findings)
    "uncertain_classification",  # ML confidence too low to trust the category
)

PRIORITIES = ("critical", "high", "medium", "low")

FINDING_STATUSES = ("open",)  # Phase 8 (human review) owns transitions


def validate_taxonomy() -> list[str]:
    problems = []
    if set(RISK_CATEGORIES) != set(CLAUSE_CATEGORIES):
        problems.append("risk taxonomy diverged from clause taxonomy")
    for cat, flags in RISK_CATEGORIES.items():
        if flags["ml_support"] and cat == "data_privacy":
            problems.append("data_privacy must stay ML-unsupported in v1")
    return problems
