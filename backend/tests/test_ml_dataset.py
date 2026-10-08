"""Dataset construction tests: feedback transformation rules, versioning,
reproducibility, and the usability contract (accept≠correction, etc.)."""
import uuid

import pytest
from conftest import TestingSession, auth, register

from app.models import Clause, Contract, FeedbackLedger, Organization, RiskFinding, User

LABELS = ("payment", "termination", "liability")


def _seed_org(db, email="t@x.com", org_name="OrgT"):
    org = Organization(name=org_name)
    db.add(org)
    db.flush()
    user = User(email=email, password_hash="x")
    db.add(user)
    db.flush()
    return org, user


def _seed_clause(db, org, user, contract_tag="c1", text="Pay within 30 days."):
    contract = Contract(organization_id=org.id, filename=f"{contract_tag}.pdf",
                        storage_key="k", uploaded_by=user.id)
    db.add(contract)
    db.flush()
    clause = Clause(organization_id=org.id, contract_id=contract.id, text=text,
                    char_start=0, char_end=len(text), order_index=0,
                    segmenter_name="test", segmenter_version="test")
    db.add(clause)
    db.flush()
    finding = RiskFinding(organization_id=org.id, contract_id=contract.id,
                          clause_id=clause.id, risk_category="payment",
                          finding_type="rule", priority="medium",
                          title="t", reason="r", rule_id="R-1", rule_version="1.0")
    db.add(finding)
    db.flush()
    return contract, clause, finding


def _feedback(db, org, user, finding, decision, corrected=None):
    fb = FeedbackLedger(
        organization_id=org.id, finding_id=finding.id,
        review_id=uuid.uuid4(), reviewer_id=user.id,
        original_category="payment", original_priority="medium",
        original_rule="R-1", original_classifier_prediction="payment",
        original_classifier_confidence=0.9, decision=decision,
        corrected_category=corrected)
    db.add(fb)
    db.flush()
    return fb


def _examples(db, org):
    from app.modules.ml_evaluation.dataset import build_examples

    return build_examples(db, org.id, LABELS)


def test_modify_with_valid_correction_is_supervised(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _, _, finding = _seed_clause(db, org, user)
        _feedback(db, org, user, finding, "modify", corrected="termination")
        db.commit()
        examples, stats = _examples(db, org)
    finally:
        db.close()
    assert len(examples) == 1
    ex = examples[0]
    assert ex["gold_label"] == "termination" and ex["usable_supervised"] is True
    assert ex["usable_eval"] is True and ex["exclusion_reason"] is None
    assert stats["modify"] == 1 and stats["total"] == 1


def test_accept_is_eval_only_not_supervised(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _, _, finding = _seed_clause(db, org, user)
        _feedback(db, org, user, finding, "accept")
        db.commit()
        examples, stats = _examples(db, org)
    finally:
        db.close()
    ex = examples[0]
    assert ex["usable_supervised"] is False  # never an automatic correction
    assert ex["usable_eval"] is True and ex["gold_label"] == "payment"
    assert ex["human_confirmed"] is True
    assert stats["accept"] == 1


def test_reject_never_supervised(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _, _, finding = _seed_clause(db, org, user)
        _feedback(db, org, user, finding, "reject")
        db.add(FeedbackLedger(
            organization_id=org.id, finding_id=finding.id, review_id=uuid.uuid4(),
            reviewer_id=user.id, original_classifier_prediction="payment",
            decision="needs_more_review"))
        db.commit()
        examples, stats = _examples(db, org)
    finally:
        db.close()
    by_decision = {e["review_decision"]: e for e in examples}
    assert by_decision["reject"]["usable_supervised"] is False
    assert by_decision["reject"]["eval_fp"] is True
    assert by_decision["needs_more_review"]["exclusion_reason"].startswith("decision_")
    assert stats["reject"] == 1 and stats["needs_more_review"] == 1


def test_ambiguous_modify_excluded_with_reason(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _, _, finding = _seed_clause(db, org, user)
        _feedback(db, org, user, finding, "modify", corrected="not_a_label")
        _feedback(db, org, user, finding, "modify")  # no correction at all
        db.commit()
        examples, _ = _examples(db, org)
    finally:
        db.close()
    assert all(e["usable_supervised"] is False for e in examples)
    reasons = {e["exclusion_reason"] for e in examples}
    assert "modify_without_valid_correction" in reasons


def test_empty_text_excluded(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _, _, finding = _seed_clause(db, org, user, text="   ")
        _feedback(db, org, user, finding, "modify", corrected="payment")
        db.commit()
        examples, _ = _examples(db, org)
    finally:
        db.close()
    assert examples[0]["exclusion_reason"] == "empty_text"
    assert examples[0]["usable_supervised"] is False


def test_example_ids_deterministic_and_org_scoped(client):
    db = TestingSession()
    try:
        org, user = _seed_org(db, "a@x.com", "OA")
        _, _, finding = _seed_clause(db, org, user)
        _feedback(db, org, user, finding, "modify", corrected="liability")
        db.commit()
        ex1, _ = _examples(db, org)
        ex2, _ = _examples(db, org)
        assert ex1[0]["example_id"] == ex2[0]["example_id"]  # reproducible
        assert ex1[0]["organization_id"] == str(org.id)
        org_b, user_b = _seed_org(db, "b@x.com", "OB")
        ex_b, _ = _examples(db, org_b)
        assert ex_b == []  # nothing leaks across orgs
    finally:
        db.close()
