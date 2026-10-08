"""Deterministic fact extractors (Phase 4.2 input layer).

Plain regex over clause text — no ML, no LLM. Every extractor returns None
for "not found" instead of guessing; absence of a fact is itself a signal
that missing_term rules consume. All functions are pure and unit-testable.
"""
from __future__ import annotations

import re

_MONEY = re.compile(
    r"(?P<cur>\$|USD|EUR|GBP|€|£)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<mult>million|billion|thousand|[mMkK])?",
    re.IGNORECASE,
)
_MULT = {"million": 1e6, "m": 1e6, "billion": 1e9, "k": 1e3, "thousand": 1e3}


def parse_money(text: str) -> tuple[float | None, str | None]:
    """First $-anchored amount in text → (amount, currency). None if absent."""
    for m in _MONEY.finditer(text):
        if not m.group("cur"):
            continue  # bare numbers are not money
        num = float(m.group("num").replace(",", ""))
        mult = (m.group("mult") or "").lower()
        num *= _MULT.get(mult, 1.0)
        cur = m.group("cur")
        cur = {"$": "USD", "€": "EUR", "£": "GBP"}.get(cur, cur.upper())
        return num, cur
    return None, None


def _days_near(text: str, keywords: tuple[str, ...]) -> int | None:
    low = text.lower()
    for kw in keywords:
        i = low.find(kw)
        if i == -1:
            continue
        window = text[max(0, i - 40):i + 60]
        m = re.search(r"(\d+)\s*(?:business\s+)?days?", window, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def extract_liability(text: str) -> dict:
    low = text.lower()
    unlimited = bool(re.search(r"unlimited\s+liabilit|liabilit\w*\s+(is\s+)?unlimited|uncapped", low))
    cap_amount, cap_currency = parse_money(text)
    capped_words = bool(re.search(r"\bcapped?\b|\blimited to\b|\bcap\b.*liabilit|liabilit\w*.*\bcapped?\b", low))
    excludes_consequential = bool(
        re.search(r"indirect|consequential|incidental|special damages|loss of profit", low))
    return {"cap_amount": cap_amount if (capped_words or cap_amount) else None,
            "cap_currency": cap_currency, "unlimited": unlimited,
            "excludes_consequential": excludes_consequential}


def extract_payment(text: str) -> dict:
    days = _days_near(text, ("pay", "payment", "invoice", "net ", "due"))
    if days is None:
        m = re.search(r"\bnet\s*(\d+)\b", text.lower())
        days = int(m.group(1)) if m else None
    amount, currency = parse_money(text)
    return {"period_days": days, "amount": amount, "currency": currency}


def extract_termination(text: str) -> dict:
    low = text.lower()
    notice = _days_near(text, ("terminat", "notice"))
    for_convenience = bool(re.search(r"for convenience|without cause|at will", low))
    return {"notice_days": notice, "for_convenience": for_convenience}


def extract_renewal(text: str) -> dict:
    low = text.lower()
    auto = bool(re.search(r"automatically?\s+renew|auto[\s\-]?renew|deemed\s+renewed|evergreen", low))
    notice = _days_near(text, ("renew", "notice", "expir"))
    return {"auto_renew": auto, "notice_days": notice}


def extract_governing_law(text: str) -> dict:
    m = re.search(r"(?:governed by|subject to|laws? of)(?: the)?(?: laws? of)?\s+"
                  r"([A-Z][A-Za-z .\-]{2,60}?)"
                  r"(?=[,.;]|$|\s+without|\s+and\s)", text)
    jur = m.group(1).strip(" .") if m else None
    if jur and len(jur.split()) > 6:
        jur = None  # over-long capture: not a jurisdiction
    return {"jurisdiction": jur}


def extract_insurance(text: str) -> dict:
    amounts = []
    for m in _MONEY.finditer(text):
        if not m.group("cur"):
            continue
        num = float(m.group("num").replace(",", "")) * _MULT.get((m.group("mult") or "").lower(), 1.0)
        amounts.append(num)
    _, currency = parse_money(text)
    return {"coverage_amounts": amounts,
            "min_coverage": min(amounts) if amounts else None, "currency": currency}


def extract_confidentiality(text: str) -> dict:
    low = text.lower()
    has_period = bool(re.search(r"\bsurviv|\d+\s*(?:year|month|day)|in perpetuity|during the term", low))
    return {"has_period": has_period}


def extract_indemnity(text: str) -> dict:
    low = text.lower()
    mentions = len(re.findall(r"indemnif", low))
    mutual = bool(re.search(r"mutual|each party.*indemnif|both parties.*indemnif|reciprocal", low))
    return {"mentions_indemnity": mentions > 0, "one_sided": mentions > 0 and not mutual}


def extract_warranty(text: str) -> dict:
    low = text.lower()
    has_duration = bool(re.search(r"warranty.*\d+\s*(?:year|month|day)|"
                                  r"\d+\s*(?:year|month|day).*warranty|warranty period", low))
    return {"has_duration": has_duration}


EXTRACTORS = {
    "liability": extract_liability,
    "payment": extract_payment,
    "termination": extract_termination,
    "renewal": extract_renewal,
    "governing_law": extract_governing_law,
    "jurisdiction": extract_governing_law,  # forum language mirrors governing-law phrasing
    "insurance": extract_insurance,
    "confidentiality": extract_confidentiality,
    "indemnification": extract_indemnity,
    "warranty": extract_warranty,
}


def extract_facts(category: str, text: str) -> dict:
    fn = EXTRACTORS.get(category)
    return fn(text) if fn else {}
