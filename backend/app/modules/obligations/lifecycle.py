"""Lifecycle computation (Phase 9.8-9.11, 9.15-9.17). Pure functions over
stored obligations + optional manual override. No PDFs, no BERT, no LLM.

Contract status rule (deliberately conservative): ACTIVE→EXPIRED happens
ONLY with a reliable expiration_date; otherwise UNKNOWN/ACTIVE with the
gap explicit. Manual override always wins. Risk/review statuses untouched.
"""
from __future__ import annotations

from app.modules.obligations import dates as D
from app.modules.obligations.taxonomy import EVENT_STATES  # noqa: F401 (re-export)


def contract_lifecycle(effective: str | None, expiration: str | None,
                       terminated: bool = False, archived: bool = False,
                       override: str | None = None,
                       ref=None) -> dict:
    """Return {status, reason}. Override wins; else deterministic derivation."""
    if override:
        return {"status": override, "reason": "Manual status set by organization member."}
    if archived:
        return {"status": "ARCHIVED", "reason": "Contract archived."}
    if terminated:
        return {"status": "TERMINATED", "reason": "Termination recorded."}
    ref = ref or D.today()
    exp = D.parse_date(expiration)
    eff = D.parse_date(effective)
    if exp is None:
        if eff is not None and eff > ref:
            return {"status": "DRAFT",
                    "reason": "Effective date is in the future; no expiration extracted."}
        return {"status": "UNKNOWN",
                "reason": "No reliable expiration date extracted; status cannot be determined."}
    if exp < ref:
        return {"status": "EXPIRED",
                "reason": f"Expiration {exp.isoformat()} has passed."}
    if (exp - ref).days <= 90:
        return {"status": "EXPIRING",
                "reason": f"Expiration {exp.isoformat()} is within 90 days."}
    return {"status": "ACTIVE",
            "reason": f"Expiration {exp.isoformat()} is more than 90 days out."}


def renewal_intelligence(auto_renew: bool | None, renewal_period_days: int | None,
                         notice_days: int | None, expiration: str | None) -> dict:
    """Renewal record; dates only when anchors exist (never fabricated)."""
    if auto_renew is None and renewal_period_days is None and notice_days is None:
        return {}
    rtype = "AUTO" if auto_renew else ("MANUAL" if auto_renew is False else "UNKNOWN")
    rdate = D.renewal_date(expiration, renewal_period_days) if auto_renew else None
    anchor = rdate or expiration
    return {"renewal_type": rtype, "renewal_period_days": renewal_period_days,
            "auto_renew": auto_renew, "renewal_notice_days": notice_days,
            "renewal_date": rdate,
            "notice_deadline": D.notice_deadline(anchor, notice_days)}


def termination_window(notice_days: int | None, cure_days: int | None) -> dict:
    if notice_days is None and cure_days is None:
        return {}
    return {"termination_notice_days": notice_days, "cure_period_days": cure_days}


def events_for_obligation(ob: dict) -> list[dict]:
    """Derive lifecycle events from one obligation dict (pre-persistence shape
    uses the same keys as the row). Only dateless-safe events are emitted."""
    t, out = ob.get("type"), []
    if t == "EXPIRY" and ob.get("expiration_date"):
        out.append(("CONTRACT_EXPIRY", ob["expiration_date"], "Contract expiration extracted."))
    if t == "EFFECTIVE" and ob.get("effective_date"):
        out.append(("CONTRACT_START", ob["effective_date"], "Contract effective date extracted."))
    if t == "RENEWAL":
        if ob.get("renewal_date"):
            out.append(("RENEWAL", ob["renewal_date"], "Calculated renewal date."))
        if ob.get("notice_deadline"):
            out.append(("RENEWAL_NOTICE", ob["notice_deadline"],
                        "Calculated renewal notice deadline."))
    if t == "TERMINATION" and ob.get("notice_period_days") and ob.get("deadline"):
        out.append(("TERMINATION_NOTICE", ob["deadline"], "Termination notice deadline."))
    if t == "PAYMENT" and ob.get("deadline"):
        out.append(("PAYMENT_DEADLINE", ob["deadline"], "Payment deadline."))
    if t == "REPORTING" and ob.get("deadline"):
        out.append(("REPORTING_DEADLINE", ob["deadline"], "Reporting deadline."))
    if t == "INSURANCE" and ob.get("deadline"):
        out.append(("INSURANCE_RENEWAL", ob["deadline"], "Insurance renewal deadline."))
    return [{"event_type": et, "event_date": ed, "description": d} for et, ed, d in out]


def event_state(event_date: str | None, ref=None) -> str:
    return D.classify_event(event_date, ref)
