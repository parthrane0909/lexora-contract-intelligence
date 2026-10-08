"""Deterministic obligation extraction (Phase 9.6). Protocol + first implementation.

ObligationExtractor protocol leaves room for a future
TransformerObligationExtractor. The deterministic implementation reuses
Phase 4 risk_engine.extractors (facts, never risk verdicts) plus date /
period / trigger / party parsing below. NEVER fabricates: unknown →
None, and confidence is a documented engineering heuristic (0.9 explicit
value+trigger, 0.75 value w/o trigger, 0.6 category signal only) — NOT a
model probability and never presented as legal certainty.
"""
from __future__ import annotations

import datetime
import re
from typing import Protocol

from app.modules.risk_engine import extractors as fx

EXTRACTION_RULES_VERSION = "1.0"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_MONTH_ABBR = {m[:3]: i for m, i in _MONTHS.items()}

_DATE_RES = [
    re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I),
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", re.I),
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),  # MM/DD/YYYY
]

_PERIOD_RE = re.compile(
    r"(\d+)\s*(business\s+days|business\s+day|days?|weeks?|months?|years?)", re.I)
_FREQUENCY_RE = re.compile(
    r"\b(each|every|per)\s+(week|month|quarter|year|day)\b|\b(weekly|monthly|quarterly|annually|annual|daily)\b", re.I)
_FREQ_DAYS = {"day": 1, "daily": 1, "week": 7, "weekly": 7, "month": 30,
              "monthly": 30, "quarter": 91, "quarterly": 91, "year": 365,
              "annual": 365, "annually": 365}

_TRIGGER_KEYWORDS = (
    ("invoice", "invoice_received"), ("receipt", "receipt"),
    ("expir", "contract_expiration"), ("renew", "renewal_date"),
    ("terminat", "termination"), ("breach", "breach"),
    ("deliver", "delivery"), ("audit", "audit_request"),
    ("report", "reporting_due"), ("sign", "contract_effective"),
    ("effective", "contract_effective"), ("commencement", "contract_effective"),
)

_PARTY_RES = (
    (re.compile(r"\b(vendor|supplier|contractor|licensor|provider)\b", re.I), "VENDOR"),
    (re.compile(r"\b(client|customer|company|licensee|buyer)\b", re.I), "CLIENT"),
    (re.compile(r"\b(each party|either party|both parties|the parties)\b", re.I), "EITHER"),
)


class ObligationExtractor(Protocol):
    name: str
    version: str

    def extract(self, clause_text: str, category: str | None) -> list[dict]:
        """Return obligation dicts (possibly empty — never fabricated)."""
        ...


def _date_after_keyword(text: str, keywords: tuple[str, ...]) -> datetime.date | None:
    """Explicit date nearest AFTER a keyword (effective/expiry dates often
    share a clause with other dates — first-date would misattribute)."""
    low = text.lower()
    positions = [m.start() for kw in keywords for m in re.finditer(kw, low)]
    if not positions:
        return parse_explicit_date(text)
    best, best_dist = None, None
    for rx in _DATE_RES:
        for m in rx.finditer(text):
            try:
                g = m.groups()
                if rx is _DATE_RES[0]:
                    d = datetime.date(int(g[2]), _month(g[0]), int(g[1]))
                elif rx is _DATE_RES[1]:
                    d = datetime.date(int(g[2]), _month(g[0]), int(g[1]))
                elif rx is _DATE_RES[2]:
                    d = datetime.date(int(g[0]), int(g[1]), int(g[2]))
                else:
                    d = datetime.date(int(g[2]), int(g[0]), int(g[1]))
                if not 1900 <= d.year <= 2100:
                    continue
            except ValueError:
                continue
            dist = min(abs(m.start() - p) for p in positions)
            if best is None or dist < best_dist:
                best, best_dist = d, dist
    return best


def parse_explicit_date(text: str) -> datetime.date | None:
    """First plausible calendar date in text, else None. Year guard 1900-2100;
    MM/DD/YYYY assumed for slashes (documented US-contracts assumption)."""
    for rx in _DATE_RES:
        m = rx.search(text)
        if not m:
            continue
        try:
            g = m.groups()
            if rx is _DATE_RES[0]:
                d = datetime.date(int(g[2]), _month(g[0]), int(g[1]))
            elif rx is _DATE_RES[1]:
                d = datetime.date(int(g[2]), _month(g[0]), int(g[1]))
            elif rx is _DATE_RES[2]:
                d = datetime.date(int(g[0]), int(g[1]), int(g[2]))
            else:
                d = datetime.date(int(g[2]), int(g[0]), int(g[1]))
            if 1900 <= d.year <= 2100:
                return d
        except ValueError:
            continue
    return None


def _month(name: str) -> int:
    n = name.lower()
    return _MONTHS.get(n, _MONTH_ABBR.get(n[:3], 0)) or 1


def parse_period(text: str) -> tuple[int | None, bool, bool]:
    """Return (days, approximate, business_days) for the first period mention."""
    m = _PERIOD_RE.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        mult = 7 if "week" in unit else 30 if "month" in unit else 365 if "year" in unit else 1
        return n * mult, False, "business" in unit
    f = _FREQUENCY_RE.search(text.lower())
    if f:
        word = [g for g in f.groups() if g][-1]  # unit word, not each/every/per
        return _FREQ_DAYS[word.lower()], True, False
    return None, False, False


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12}
_WORD_PERIOD_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"[-\s]?(year|month|week|day)s?\b", re.I)
_NOTICE_SPAN_RE = re.compile(
    r"\d+\s*(business\s+)?days?\s+notice|notice[^\d.]{0,60}?\d+\s*days?", re.I)


def renewal_period_days(text: str) -> tuple[int | None, bool]:
    """Renewal term length, ignoring notice-period mentions ("60 days notice"
    is not a term). Supports word numbers ("one-year") deterministically."""
    scrubbed = _NOTICE_SPAN_RE.sub(" ", text)
    days, approx, _ = parse_period(scrubbed)
    if days is not None:
        return days, approx
    m = _WORD_PERIOD_RE.search(scrubbed)
    if m:
        n = _WORD_NUM[m.group(1).lower()]
        unit = m.group(2).lower()
        mult = 7 if unit == "week" else 30 if unit == "month" else 365 if unit == "year" else 1
        return n * mult, False
    return None, False


def infer_trigger(text: str) -> str:
    low = text.lower()
    for kw, trigger in _TRIGGER_KEYWORDS:
        if kw in low:
            return trigger
    return "unspecified"


def infer_party(text: str) -> str | None:
    hits = {label for rx, label in _PARTY_RES if rx.search(text)}
    if len(hits) == 1:
        return next(iter(hits))
    return None  # ambiguous or absent → None, never guessed


def _conf(has_value: bool, has_trigger: bool) -> float:
    return 0.9 if (has_value and has_trigger) else (0.75 if has_value else 0.6)


def _base(ob_type: str, rule_id: str, title: str, text: str, **kw) -> dict:
    d = {"type": ob_type, "rule_id": rule_id, "title": title,
         "description": text.strip()[:500], "party": infer_party(text),
         "trigger": infer_trigger(text), "frequency": None,
         "period_days": None, "period_approximate": False,
         "notice_period_days": None, "deadline": None,
         "effective_date": None, "expiration_date": None,
         "renewal_period_days": None, "auto_renew": None,
         "amount": None, "currency": None, "confidence": 0.6,
         "extraction_method": "deterministic_rule",
         "model_version": EXTRACTION_RULES_VERSION}
    d.update(kw)
    return d


class DeterministicObligationExtractor:
    """Regex/fact-based extraction over clause text (+ optional category)."""

    name = "deterministic"
    version = EXTRACTION_RULES_VERSION

    def extract(self, clause_text: str, category: str | None = None) -> list[dict]:
        text = clause_text or ""
        if not text.strip():
            return []
        out: list[dict] = []
        low = text.lower()
        days, approx, _biz = parse_period(text)
        explicit = parse_explicit_date(text)
        trigger = infer_trigger(text)

        if category == "payment" or ("pay" in low and ("$" in text or "fee" in low or "invoice" in low or days)):
            facts = fx.extract_payment(text)
            amount, curr = fx.parse_money(text)
            out.append(_base("PAYMENT", "OB-PAY-001", "Payment obligation", text,
                             period_days=facts.get("period_days"), period_approximate=False,
                             trigger=trigger if trigger != "unspecified" else (
                                 "invoice_received" if "invoice" in low else trigger),
                             deadline=explicit.isoformat() if explicit else None,
                             amount=amount, currency=curr,
                             confidence=_conf(facts.get("period_days") is not None or amount is not None,
                                              trigger != "unspecified")))
        if category == "renewal" or "renew" in low:
            facts = fx.extract_renewal(text)
            rp, rp_approx = renewal_period_days(text)
            out.append(_base("RENEWAL", "OB-REN-001", "Renewal term", text,
                             trigger=trigger if trigger != "unspecified" else "contract_expiration",
                             renewal_period_days=rp, auto_renew=facts.get("auto_renew"),
                             notice_period_days=facts.get("notice_days"),
                             confidence=_conf(True, True)))
        if category == "termination" or "terminat" in low:
            facts = fx.extract_termination(text)
            cure = re.search(r"(\d+)\s*days?\s+cure|cure[^\d.]{0,40}?(\d+)\s*days?", low)
            cure_days = int(next(g for g in cure.groups() if g)) if cure else None
            out.append(_base("TERMINATION", "OB-TERM-001", "Termination right/notice", text,
                             trigger=trigger if trigger != "unspecified" else "termination",
                             notice_period_days=facts.get("notice_days"),
                             deadline=explicit.isoformat() if explicit else None,
                             confidence=_conf(facts.get("notice_days") is not None, True)))
            if cure_days:
                out.append(_base("NOTICE", "OB-NOT-002", "Cure period", text,
                                 trigger="breach", notice_period_days=cure_days,
                                 confidence=0.75))
        if category == "insurance" or ("insurance" in low and ("maintain" in low or "coverage" in low or "policy" in low)):
            facts = fx.extract_insurance(text)
            _amt, curr = fx.parse_money(text)
            out.append(_base("INSURANCE", "OB-INS-001", "Insurance maintenance", text,
                             trigger=trigger if trigger != "unspecified" else "contract_effective",
                             amount=facts.get("min_coverage"), currency=curr or facts.get("currency"),
                             deadline=explicit.isoformat() if explicit else None,
                             confidence=_conf(facts.get("min_coverage") is not None, True)))
        if category == "confidentiality" or ("confidential" in low and ("surviv" in low or "year" in low or "month" in low or "period" in low or "perpetuity" in low)):
            out.append(_base("CONFIDENTIALITY", "OB-CONF-001", "Confidentiality duty", text,
                             period_days=days, period_approximate=approx,
                             trigger=trigger if trigger != "unspecified" else "contract_effective",
                             confidence=_conf(days is not None, True)))
        if re.search(r"\baudit\b.*\b(right|access|inspect|books|records)\b|\b(right|access)\b.*\baudit\b", low):
            out.append(_base("AUDIT", "OB-AUD-001", "Audit right", text,
                             trigger=trigger if trigger != "unspecified" else "audit_request",
                             notice_period_days=parse_period(text)[0],
                             confidence=_conf(True, trigger != "unspecified")))
        if re.search(r"\b(reports?|deliverables?|statements?|certifications?)\b", low) and re.search(
                r"\b(provide|submit|furnish|deliver|furnishes)\b", low):
            freq_m = _FREQUENCY_RE.search(low)
            out.append(_base("REPORTING", "OB-REP-001", "Reporting/deliverable", text,
                             frequency=freq_m.group(0) if freq_m else None,
                             period_days=days, period_approximate=approx,
                             trigger=trigger if trigger != "unspecified" else "reporting_due",
                             deadline=explicit.isoformat() if explicit else None,
                             confidence=_conf(days is not None or explicit is not None, True)))
        # contract-level dates (any clause); dated to the NEARBY keyword
        if explicit and re.search(r"expir|termination date|term ends?|ends on|through\s+\w+\s+\d", low):
            exp_date = _date_after_keyword(text, ("expir", "ends on", "through"))
            out.append(_base("EXPIRY", "OB-EXP-001", "Contract expiration", text,
                             trigger="contract_expiration",
                             expiration_date=(exp_date or explicit).isoformat(),
                             confidence=0.85))
        if explicit and re.search(r"effective|commencement|starts on|as of", low):
            eff_date = _date_after_keyword(text, ("effective", "commencement", "starts on", "as of"))
            out.append(_base("EFFECTIVE", "OB-EFF-001", "Contract effective date", text,
                             trigger="contract_effective",
                             effective_date=(eff_date or explicit).isoformat(),
                             confidence=0.85))
        # generic written-notice obligation (only when nothing else fired)
        if not out and re.search(r"written notice", low) and days:
            out.append(_base("NOTICE", "OB-NOT-001", "Notice requirement", text,
                             trigger=trigger, notice_period_days=days, confidence=0.7))
        return out


_EXTRACTORS: dict[str, type[ObligationExtractor]] = {"deterministic": DeterministicObligationExtractor}


def register_extractor(name: str, cls: type[ObligationExtractor]) -> None:
    _EXTRACTORS[name] = cls


def get_extractor(name: str = "deterministic") -> ObligationExtractor:
    try:
        return _EXTRACTORS[name]()
    except KeyError:
        raise ValueError(f"Unknown extractor {name!r}. Registered: {sorted(_EXTRACTORS)}") from None
