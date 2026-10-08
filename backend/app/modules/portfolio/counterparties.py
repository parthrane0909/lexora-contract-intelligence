"""Counterparty intelligence (Phase 11.12). The ONLY counterparty signal is
the member-supplied `contracts.counterparty` field (set via PATCH
/contracts/{id}); nothing is inferred from filenames or invented. Contracts
without one aggregate under "Unspecified" — explicitly, not silently.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Contract, Obligation, RiskFinding
from app.modules.portfolio import aggregations as A

UNSPECIFIED = "Unspecified"


def counterparty_portfolio(db: Session, org_id: uuid.UUID) -> dict:
    contracts = db.query(Contract).filter(
        Contract.organization_id == org_id,
        Contract.is_archived.is_(False)).all()
    findings = A.latest_run_findings_q(db, org_id).all()
    reviews = A.latest_reviews(db, org_id)
    by_cp: dict[str, dict] = {}
    cids_by_cp: dict[str, set[str]] = {}
    for c in contracts:
        name = (c.counterparty or "").strip() or UNSPECIFIED
        cids_by_cp.setdefault(name, set()).add(str(c.id))
        cell = by_cp.setdefault(name, {
            "counterparty": name, "contract_count": 0, "active_contract_count": 0,
            "risk_count": 0, "high_risk_count": 0, "overdue_obligation_count": 0,
            "upcoming_renewal_count": 0, "contract_ids": []})
        cell["contract_count"] += 1
        if c.processing_status == "processed":
            cell["active_contract_count"] += 1
        cell["contract_ids"].append(str(c.id))
    for f in findings:
        for name, ids in cids_by_cp.items():
            if str(f.contract_id) in ids:
                by_cp[name]["risk_count"] += 1
                rev = reviews.get(str(f.id))
                if (rev is None or rev.decision != "reject") and \
                        f.priority in ("high", "critical"):
                    by_cp[name]["high_risk_count"] += 1
    for o in db.query(Obligation).filter(
            Obligation.organization_id == org_id).all():
        for name, ids in cids_by_cp.items():
            if str(o.contract_id) not in ids:
                continue
            if o.deadline and o.deadline < A.today_iso() and \
                    o.status in ("UNCONFIRMED", "CONFIRMED", "ACTIVE", "OVERDUE"):
                by_cp[name]["overdue_obligation_count"] += 1
            if o.renewal_date and o.renewal_date >= A.today_iso() and \
                    o.status in ("UNCONFIRMED", "CONFIRMED", "ACTIVE"):
                by_cp[name]["upcoming_renewal_count"] += 1
    ranked = sorted(by_cp.values(),
                    key=lambda r: (-r["high_risk_count"], -r["risk_count"],
                                   r["counterparty"]))
    return {"counterparties": ranked,
            "counterparty_data_available": any(
                (c.counterparty or "").strip() for c in contracts)}
