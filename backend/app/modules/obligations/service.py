"""Lifecycle service (Phase 9). Direct, deterministic, no worker dependency.

extract_contract: clauses → extractor → obligations (+ renewal/notice dates
  derived where anchors exist) → events → risk/policy links → audit.
Re-runs preserve human-reviewed obligations (matched by clause+type+rule);
only unreviewed rows are replaced. Extracted values are immutable; `status`
is the explicit workflow state.
"""
from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (Clause, ClauseClassification, LifecycleEvent, LifecycleFeedback,
                        Obligation, ObligationReview)
from app.modules.audit.service import record
from app.modules.obligations import dates as D
from app.modules.obligations import lifecycle as LC
from app.modules.obligations.extractors import get_extractor
from app.modules.obligations.rules import linked_policy_deviation, policy_expectation
from app.modules.obligations.taxonomy import (
    COMMENT_MAX_CHARS,
    LIFECYCLE_CORRECTION_TYPES,
    LIFECYCLE_ENGINE_VERSION,
    OBLIGATION_STATUSES,
    OBLIGATION_TAXONOMY_VERSION,
    REVIEW_DECISIONS,
)


def _latest_categories(db: Session, contract_id: uuid.UUID) -> dict[str, str]:
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract_id)
            .order_by(ClauseClassification.created_at.desc()).all())
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(str(r.clause_id), r.predicted_category)
    return out


def _reviewed_keys(db: Session, contract_id: uuid.UUID) -> set[tuple[str, str, str | None]]:
    rows = (db.query(Obligation)
            .filter(Obligation.contract_id == contract_id).all())
    reviewed = {o.id for o in rows if db.query(ObligationReview)
                .filter(ObligationReview.obligation_id == o.id).first()}
    return {(str(o.clause_id), o.type, o.rule_id) for o in rows if o.id in reviewed}


def extract_contract(db: Session, contract_id: uuid.UUID, *,
                     triggered_by: uuid.UUID | None,
                     extractor: str = "deterministic") -> dict:
    from app.models import Contract

    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    clauses = db.query(Clause).filter(Clause.contract_id == contract.id)\
        .order_by(Clause.order_index).all()
    if not clauses:
        raise HTTPException(400, "Contract has no clauses to extract from.")
    cats = _latest_categories(db, contract.id)
    keep = _reviewed_keys(db, contract.id)

    # Replace unreviewed rows; reviewed extractions survive re-runs.
    for ob in db.query(Obligation).filter(Obligation.contract_id == contract.id).all():
        if (str(ob.clause_id), ob.type, ob.rule_id) not in keep:
            db.query(LifecycleEvent).filter(LifecycleEvent.obligation_id == ob.id)\
                .delete(synchronize_session=False)
            db.delete(ob)
    db.flush()

    ext = get_extractor(extractor)
    created, skipped = 0, 0
    fresh: list[Obligation] = []
    for cl in clauses:
        for cand in ext.extract(cl.text or "", cats.get(str(cl.id))):
            if (str(cl.id), cand["type"], cand.get("rule_id")) in keep:
                skipped += 1
                continue
            dev = linked_policy_deviation(db, contract.id, cl.id)
            ob = Obligation(
                organization_id=contract.organization_id, contract_id=contract.id,
                clause_id=cl.id, type=cand["type"], title=cand.get("title") or cand["type"],
                description=cand.get("description"), party=cand.get("party"),
                trigger=cand.get("trigger"), frequency=cand.get("frequency"),
                period_days=cand.get("period_days"),
                period_approximate=bool(cand.get("period_approximate")),
                notice_period_days=cand.get("notice_period_days"),
                cure_period_days=cand.get("cure_period_days"),
                deadline=cand.get("deadline"), effective_date=cand.get("effective_date"),
                expiration_date=cand.get("expiration_date"),
                renewal_period_days=cand.get("renewal_period_days")
                or (cand.get("period_days") if cand.get("type") == "RENEWAL" else None),
                auto_renew=cand.get("auto_renew"),
                amount=cand.get("amount"), currency=cand.get("currency"),
                status="UNCONFIRMED", confidence=cand.get("confidence"),
                extraction_method=cand.get("extraction_method", "deterministic_rule"),
                model_name=ext.name,
                model_version=f"{OBLIGATION_TAXONOMY_VERSION}/{ext.version}",
                rule_id=cand.get("rule_id"),
                source_text=(cl.text or "")[:2000],
                risk_finding_id=dev.id if dev else None)
            db.add(ob)
            fresh.append(ob)
            created += 1
    db.flush()
    # Pass 2: renewal math needs the contract expiration anchor, which may
    # live in a different clause — resolved here, never in the extractors.
    anchor = next((o.expiration_date for o in fresh if o.expiration_date), None)
    if anchor is None:
        anchor = next((o.expiration_date for o in db.query(Obligation).filter(
            Obligation.contract_id == contract.id,
            Obligation.expiration_date.is_not(None)).all()), None)
    for ob in fresh:
        if ob.type == "RENEWAL":
            if ob.auto_renew:
                ob.renewal_date = D.renewal_date(anchor, ob.renewal_period_days)
            ob.notice_deadline = D.notice_deadline(
                ob.renewal_date or anchor, ob.notice_period_days)
    db.flush()
    for ob in db.query(Obligation).filter(Obligation.contract_id == contract.id).all():
        cl = next((c for c in clauses if c.id == ob.clause_id), None)
        for ev in LC.events_for_obligation({
                "type": ob.type, "expiration_date": ob.expiration_date,
                "effective_date": ob.effective_date,
                "renewal_date": ob.renewal_date, "notice_deadline": ob.notice_deadline,
                "notice_period_days": ob.notice_period_days, "deadline": ob.deadline}):
            if not db.query(LifecycleEvent).filter(
                    LifecycleEvent.obligation_id == ob.id,
                    LifecycleEvent.event_type == ev["event_type"],
                    LifecycleEvent.event_date == ev["event_date"]).first():
                db.add(LifecycleEvent(
                    organization_id=contract.organization_id, contract_id=contract.id,
                    obligation_id=ob.id, event_type=ev["event_type"],
                    event_date=ev["event_date"], trigger=ob.trigger,
                    status=D.classify_event(ev["event_date"]),
                    source_clause_id=ob.clause_id, description=ev["description"]))
    db.commit()
    record(db, action="obligation.extracted", organization_id=contract.organization_id,
           user_id=triggered_by, object_type="contract", object_id=str(contract.id),
           detail=f"{created} obligations ({ext.name} {ext.version})")
    return {"status": "ready" if created else "partial",
            "obligations": created, "preserved": skipped,
            "engine": LIFECYCLE_ENGINE_VERSION}


def refresh_event_states(db: Session, contract_id: uuid.UUID) -> None:
    """Recompute stored event states against today (cheap, keeps DB truthful)."""
    changed = False
    for ev in db.query(LifecycleEvent).filter(
            LifecycleEvent.contract_id == contract_id).all():
        live = D.classify_event(ev.event_date)
        if live != ev.status and ev.status not in ("COMPLETED", "CANCELLED"):
            ev.status = live
            changed = True
    if changed:
        db.commit()


def _contract_anchors(db: Session, contract_id: uuid.UUID) -> dict:
    obs = db.query(Obligation).filter(Obligation.contract_id == contract_id).all()
    eff = next((o.effective_date for o in obs if o.type == "EFFECTIVE" and o.effective_date), None)
    exp = next((o.expiration_date for o in obs if o.type == "EXPIRY" and o.expiration_date), None)
    ren = [o for o in obs if o.type == "RENEWAL"]
    term = [o for o in obs if o.type == "TERMINATION"]
    return {"effective": eff, "expiration": exp, "renewals": ren, "terminations": term,
            "obligations": obs}


def lifecycle_summary(db: Session, contract) -> dict:
    """Efficient single-view payload: status, dates, renewal, notices, items."""
    from app.models import Contract as _C

    refresh_event_states(db, contract.id)
    a = _contract_anchors(db, contract.id)
    terminated = any("terminat" in (o.description or "").lower() and o.status == "CONFIRMED"
                     for o in a["obligations"])
    status = LC.contract_lifecycle(a["effective"], a["expiration"],
                                   terminated=terminated,
                                   archived=contract.is_archived,
                                   override=contract.lifecycle_status_override)
    # renewal intelligence from the first AUTO renewal, else first renewal
    ren_info: dict = {}
    autos = [o for o in a["renewals"] if o.auto_renew]
    target = autos[0] if autos else (a["renewals"][0] if a["renewals"] else None)
    if target:
        rdate = D.renewal_date(a["expiration"], target.renewal_period_days) \
            if target.auto_renew else None
        anchor = rdate or a["expiration"]
        ren_info = {"renewal_type": "AUTO" if target.auto_renew else "MANUAL",
                    "renewal_period_days": target.renewal_period_days,
                    "auto_renew": target.auto_renew,
                    "renewal_notice_days": target.notice_period_days,
                    "renewal_date": rdate,
                    "notice_deadline": D.notice_deadline(anchor, target.notice_period_days),
                    "source_clause_id": str(target.clause_id)}
    term_info = [{"termination_notice_days": o.notice_period_days,
                  "cure_period_days": o.cure_period_days,
                  "source_clause_id": str(o.clause_id)} for o in a["terminations"]]
    events = db.query(LifecycleEvent).filter(
        LifecycleEvent.contract_id == contract.id).order_by(LifecycleEvent.event_date).all()
    open_obs = [o for o in a["obligations"] if o.status in ("UNCONFIRMED", "CONFIRMED", "ACTIVE")]
    overdue = [e for e in events if e.status == "OVERDUE"]
    upcoming = [e for e in events if e.status in ("UPCOMING", "DUE")]
    return {
        "contract_id": str(contract.id), **status,
        "effective_date": a["effective"], "expiration_date": a["expiration"],
        "renewal": ren_info, "termination_windows": term_info,
        "open_obligations": len(open_obs),
        "upcoming_events": len(upcoming), "overdue_events": len(overdue),
        "obligations": [_ob_out(o) for o in a["obligations"]],
        "events": [_ev_out(e) for e in events],
    }


def _ob_out(o: Obligation) -> dict:
    return {
        "id": str(o.id), "contract_id": str(o.contract_id), "clause_id": str(o.clause_id),
        "type": o.type, "title": o.title, "description": o.description, "party": o.party,
        "trigger": o.trigger, "frequency": o.frequency, "period_days": o.period_days,
        "period_approximate": o.period_approximate,
        "notice_period_days": o.notice_period_days, "cure_period_days": o.cure_period_days,
        "deadline": o.deadline, "effective_date": o.effective_date,
        "expiration_date": o.expiration_date, "renewal_period_days": o.renewal_period_days,
        "auto_renew": o.auto_renew, "renewal_date": o.renewal_date,
        "notice_deadline": o.notice_deadline, "amount": o.amount, "currency": o.currency,
        "status": o.status, "confidence": o.confidence,
        "extraction_method": o.extraction_method, "model_version": o.model_version,
        "rule_id": o.rule_id, "source_text": (o.source_text or "")[:1000],
        "risk_finding_id": str(o.risk_finding_id) if o.risk_finding_id else None,
        "created_at": o.created_at.isoformat(),
    }


def _ev_out(e: LifecycleEvent) -> dict:
    return {
        "id": str(e.id), "contract_id": str(e.contract_id),
        "obligation_id": str(e.obligation_id) if e.obligation_id else None,
        "event_type": e.event_type, "event_date": e.event_date, "trigger": e.trigger,
        "status": e.status,
        "source_clause_id": str(e.source_clause_id) if e.source_clause_id else None,
        "description": e.description,
    }


# ------------------------------------------------------------- review ----

def validate_review(decision: str, comment: str | None, correction_type: str | None,
                    corrected: dict) -> str:
    if decision not in REVIEW_DECISIONS:
        raise HTTPException(400, f"Invalid decision {decision!r}.")
    comment = (comment or "").strip()
    if decision in ("reject", "modify") and not comment:
        raise HTTPException(400, f"A comment is required for {decision}.")
    if len(comment) > COMMENT_MAX_CHARS:
        raise HTTPException(400, f"Comment exceeds {COMMENT_MAX_CHARS} characters.")
    if decision == "modify" and correction_type not in LIFECYCLE_CORRECTION_TYPES:
        raise HTTPException(400, "MODIFY requires a valid correction_type.")
    if correction_type is not None and correction_type not in LIFECYCLE_CORRECTION_TYPES:
        raise HTTPException(400, f"Invalid correction_type {correction_type!r}.")
    if corrected.get("corrected_status") is not None:
        from app.modules.obligations.taxonomy import OBLIGATION_STATUSES

        if corrected["corrected_status"] not in OBLIGATION_STATUSES:
            raise HTTPException(400, "Invalid corrected_status.")
    return comment


def _snapshot_ob(ob: Obligation) -> dict:
    return {k: getattr(ob, k) for k in (
        "type", "title", "party", "trigger", "frequency", "period_days",
        "notice_period_days", "deadline", "effective_date", "expiration_date",
        "renewal_period_days", "auto_renew", "amount", "currency",
        "confidence", "extraction_method", "model_version", "rule_id")}


def review_obligation(db: Session, obligation: Obligation, *, reviewer_id,
                      reviewer_role: str | None, decision: str, comment: str | None,
                      correction_type: str | None = None,
                      corrected: dict | None = None):
    corrected = corrected or {}
    comment = validate_review(decision, comment, correction_type, corrected)
    snap = _snapshot_ob(obligation)
    # status transition is workflow state; extracted values stay immutable
    if decision == "confirm":
        obligation.status = "CONFIRMED"
    elif decision == "reject":
        obligation.status = "NOT_APPLICABLE"
    elif decision == "modify":
        obligation.status = corrected.get("corrected_status") or "CONFIRMED"
    review = ObligationReview(
        organization_id=obligation.organization_id, obligation_id=obligation.id,
        reviewer_id=reviewer_id, decision=decision, comment=comment or None,
        correction_type=correction_type,
        corrected_type=corrected.get("corrected_type"),
        corrected_date=corrected.get("corrected_date"),
        corrected_notice_days=corrected.get("corrected_notice_days"),
        corrected_party=corrected.get("corrected_party"),
        corrected_trigger=corrected.get("corrected_trigger"),
        corrected_status=corrected.get("corrected_status"),
        snapshot=json.dumps(snap, default=str))
    db.add(review)
    db.flush()
    fb = LifecycleFeedback(
        organization_id=obligation.organization_id, obligation_id=obligation.id,
        review_id=review.id, reviewer_id=reviewer_id, reviewer_role=reviewer_role,
        original_type=obligation.type, original_deadline=obligation.deadline,
        original_notice_days=obligation.notice_period_days,
        original_party=obligation.party, original_trigger=obligation.trigger,
        original_confidence=obligation.confidence,
        extraction_method=obligation.extraction_method,
        decision=decision, correction_type=correction_type,
        corrected_type=corrected.get("corrected_type"),
        corrected_date=corrected.get("corrected_date"),
        corrected_notice_days=corrected.get("corrected_notice_days"),
        reviewer_comment=comment or None)
    db.add(fb)
    db.commit()
    db.refresh(review)
    action = {"confirm": "obligation.confirmed", "reject": "obligation.rejected",
              "modify": "obligation.modified"}[decision]
    record(db, action=action, organization_id=obligation.organization_id,
           user_id=reviewer_id, object_type="obligation", object_id=str(obligation.id),
           detail=f"decision={decision}")
    return review, fb


# ------------------------------------------------------------- dashboard ----

def dashboard_counts(db: Session, organization_id: uuid.UUID) -> dict:
    """All numbers derived from stored state (refreshed event states)."""
    from app.models import Contract

    contracts = db.query(Contract).filter(
        Contract.organization_id == organization_id,
        Contract.is_archived.is_(False)).all()
    for c in contracts:
        refresh_event_states(db, c.id)
    expiring, renewals, upcoming, overdue, unconfirmed, notices = 0, 0, 0, 0, 0, 0
    for c in contracts:
        summ = lifecycle_summary(db, c)
        if summ["status"] == "EXPIRING":
            expiring += 1
        if summ["renewal"].get("auto_renew"):
            renewals += 1
        upcoming += summ["upcoming_events"]
        overdue += summ["overdue_events"]
        unconfirmed += sum(1 for o in summ["obligations"] if o["status"] == "UNCONFIRMED")
        notices += sum(1 for e in summ["events"]
                       if e["event_type"] in ("RENEWAL_NOTICE", "TERMINATION_NOTICE")
                       and e["status"] in ("UPCOMING", "DUE"))
    return {"contracts_expiring_soon": expiring, "renewals_approaching": renewals,
            "upcoming_obligations": upcoming, "overdue_obligations": overdue,
            "unconfirmed_obligations": unconfirmed, "upcoming_notice_deadlines": notices}


def set_override(db: Session, contract, status: str | None, user_id) -> None:
    from app.modules.obligations.taxonomy import LIFECYCLE_STATUSES

    if status is not None and status not in LIFECYCLE_STATUSES:
        raise HTTPException(400, f"Invalid lifecycle status {status!r}.")
    contract.lifecycle_status_override = status
    db.commit()
    record(db, action="lifecycle.recalculated", organization_id=contract.organization_id,
           user_id=user_id, object_type="contract", object_id=str(contract.id),
           detail=f"lifecycle override={status}")


def run_lifecycle_scan(db: Session) -> dict:
    """Deterministic org-wide scan callable directly (or from a worker later):
    refreshes event states for every contract. No scheduling here."""
    from app.models import Contract

    n_contracts, n_events = 0, 0
    for (cid,) in db.query(Contract.id).all():
        refresh_event_states(db, cid)
        n_contracts += 1
        n_events += db.query(LifecycleEvent).filter(
            LifecycleEvent.contract_id == cid).count()
    return {"contracts": n_contracts, "events": n_events}
