"""Transparent priority scoring (Phase 4.5). No black boxes.

priority = base severity from the rule, bumped at most one level when the
org's policy marks the position high-impact. Every contributor is stored in
priority_reasons on the finding so the calculation is fully inspectable.
"""

_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_LEVELS = ("low", "medium", "high", "critical")


def compute_priority(base: str, *, policy_impact: str | None = None,
                     extra_reasons: list[str] | None = None) -> tuple[str, list[str]]:
    """Return (priority, reasons). Raises ValueError on unknown base."""
    if base not in _ORDER:
        raise ValueError(f"Unknown priority {base!r}")
    level = _ORDER[base]
    reasons = list(extra_reasons or [])
    if policy_impact == "high" and level < _ORDER["critical"]:
        level += 1
        reasons.append("Bumped one level: company policy marks this position high-impact")
    return _LEVELS[level], reasons
