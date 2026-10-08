"""Phase 15 feedback-loop tests. Fixture clause texts are obviously synthetic
test scaffolding (\"Synthetic test clause ...\") used only to exercise the
audit/versioning machinery in tmp paths — never written to real dataset dirs,
never trained on, never presented as real labels."""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register

from app.models import Clause, Contract, FeedbackLedger, Organization, RiskFinding, User

LABELS = ("payment", "termination", "liability", "warranty", "renewal")


def _seed_org(db, email="f@x.com", org_name="OrgF"):
    org = Organization(name=org_name)
    db.add(org)
    db.flush()
    user = User(email=email, password_hash="x")
    db.add(user)
    db.flush()
    return org, user


def _seed_feedback(db, org, user, tag, text, decision, corrected=None,
                   predicted="payment"):
    contract = Contract(organization_id=org.id, filename=f"{tag}.pdf",
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
    fb = FeedbackLedger(
        organization_id=org.id, finding_id=finding.id,
        review_id=uuid.uuid4(), reviewer_id=user.id, reviewer_role="legal_reviewer",
        original_category="payment", original_priority="medium",
        original_rule="R-1", original_classifier_prediction=predicted,
        original_classifier_confidence=0.9, decision=decision,
        corrected_category=corrected)
    db.add(fb)
    db.flush()
    return fb


def _txt(i, label="warranty"):
    return f"Synthetic test clause {i} about {label} obligations for audit testing."


def test_valid_correction_is_eligible(client):
    from app.modules.ml_evaluation import feedback_audit as fa

    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _seed_feedback(db, org, user, "c1", _txt(1), "modify", corrected="warranty",
                       predicted="payment")
        db.commit()
        audit = fa.audit_feedback(db, org.id, LABELS)
    finally:
        db.close()
    assert audit["buckets"]["SUPERVISED_ELIGIBLE"]["count"] == 1
    assert audit["eligible_class_distribution"] == {"warranty": 1}
    assert audit["correction_matrix"] == {"payment": {"warranty": 1}}
    assert audit["eligible_contracts"] == 1


def test_missing_correction_excluded(client):
    from app.modules.ml_evaluation import feedback_audit as fa

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="m@x.com", org_name="OrgM")
        _seed_feedback(db, org, user, "c1", _txt(2), "modify", corrected=None)
        db.commit()
        audit = fa.audit_feedback(db, org.id, LABELS)
    finally:
        db.close()
    assert audit["buckets"]["SUPERVISED_ELIGIBLE"]["count"] == 0
    assert audit["buckets"]["EXCLUDED_AMBIGUOUS"]["count"] == 1
    assert "modify_without_valid_correction" in audit["exclusion_reasons"]


def test_invalid_class_excluded(client):
    from app.modules.ml_evaluation import feedback_audit as fa

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="i@x.com", org_name="OrgI")
        _seed_feedback(db, org, user, "c1", _txt(3), "modify",
                       corrected="not_a_class")
        db.commit()
        audit = fa.audit_feedback(db, org.id, LABELS)
    finally:
        db.close()
    assert audit["buckets"]["SUPERVISED_ELIGIBLE"]["count"] == 0
    assert audit["buckets"]["EXCLUDED_AMBIGUOUS"]["count"] == 1


def test_ambiguous_and_empty_excluded(client):
    from app.modules.ml_evaluation import feedback_audit as fa

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="a@x.com", org_name="OrgA")
        _seed_feedback(db, org, user, "c1", _txt(4), "needs_more_review")
        _seed_feedback(db, org, user, "c2", "   ", "modify", corrected="payment")
        _seed_feedback(db, org, user, "c3", _txt(5), "reject")
        db.commit()
        audit = fa.audit_feedback(db, org.id, LABELS)
    finally:
        db.close()
    assert audit["buckets"]["EXCLUDED_AMBIGUOUS"]["count"] == 1
    assert audit["buckets"]["EXCLUDED_INVALID"]["count"] == 1
    assert audit["buckets"]["EXCLUDED_NON_CORRECTION"]["count"] == 1
    assert audit["buckets"]["SUPERVISED_ELIGIBLE"]["count"] == 0


def test_duplicate_and_contradiction_surfaced(tmp_path):
    from app.modules.ml_evaluation.dataset import build_examples
    from app.modules.ml_evaluation.dataset_builder import quality_report

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="d@x.com", org_name="OrgD")
        _seed_feedback(db, org, user, "c1", _txt(6), "modify", corrected="payment")
        _seed_feedback(db, org, user, "c2", _txt(6), "modify", corrected="payment")
        db.commit()
        examples, _ = build_examples(db, org.id, LABELS)
    finally:
        db.close()
    from app.modules.ml_evaluation.splits import split_examples

    rep = quality_report(examples, split_examples(
        [e for e in examples if e["usable_supervised"]], 42), LABELS)
    assert len(rep["duplicate_text"]) == 1  # second identical text flagged


def test_provenance_preserved(client):
    from app.modules.ml_evaluation.dataset import build_examples

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="p@x.com", org_name="OrgP")
        fb = _seed_feedback(db, org, user, "c1", _txt(7), "modify",
                           corrected="termination")
        db.commit()
        examples, _ = build_examples(db, org.id, LABELS)
    finally:
        db.close()
    ex = [e for e in examples if e["usable_supervised"]][0]
    assert ex["gold_label"] == "termination"
    assert ex["review_decision"] == "modify"
    assert ex["original_model_category"] == "payment"
    assert ex["contract_id"] and ex["clause_id"] and ex["example_id"]
    assert ex["source"] == "human_review"


def test_contract_split_isolation_and_immutability(tmp_path):
    from app.modules.ml_evaluation.dataset import build_examples
    from app.modules.ml_evaluation.dataset_builder import build_versioned_dataset

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="s@x.com", org_name="OrgS")
        for i in range(6):
            _seed_feedback(db, org, user, f"cc{i}", _txt(10 + i), "modify",
                           corrected="payment")
        db.commit()
        examples, _ = build_examples(db, org.id, LABELS)
    finally:
        db.close()
    usable = [e for e in examples if e["usable_supervised"]]
    built = build_versioned_dataset(
        usable, base_dir=str(tmp_path), version="v1", seed=42, valid_labels=LABELS)
    assert sum(built["split_counts"].values()) == 6
    # contract isolation is enforced by construction (raises on overlap)
    with pytest.raises(ValueError, match="already exists"):
        build_versioned_dataset(
            usable, base_dir=str(tmp_path), version="v1", seed=42,
            valid_labels=LABELS)
    man = json.load(open(f"{tmp_path}/v1/manifest.json"))
    assert man["splits_sha256"] and man["files_sha256"]["train.jsonl"]


def test_overlap_screen_flags_frozen_test():
    from app.modules.ml_evaluation import feedback_audit as fa
    from app.modules.ml_evaluation.dataset import norm_text

    ex = [{"example_id": "fb-1", "text": "Pay within 30 days."}]
    frozen = {"train": set(), "validation": set(),
              "test": {norm_text("Pay within 30 days.")}}
    out = fa.overlap_screen(ex, frozen)
    assert out["test"]["n_hits"] == 1
    assert out["train"]["n_hits"] == 0


def test_empty_inventory_verdict_insufficient(client):
    from app.modules.ml_evaluation import feedback_audit as fa

    register(client, "e@x.com", "password123", "OrgE")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "e@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        audit = fa.audit_feedback(db, org_id, LABELS)
        verdict = fa.evidence_verdict(audit)
    finally:
        db.close()
    assert audit["total"] == 0
    assert verdict["verdict"] == "INSUFFICIENT_CONTINUE_COLLECTING"


def test_no_fabricated_labels(client):
    """Every gold label must trace to a human correction; nothing invented."""
    from app.modules.ml_evaluation.dataset import build_examples

    db = TestingSession()
    try:
        org, user = _seed_org(db, email="n@x.com", org_name="OrgN")
        _seed_feedback(db, org, user, "c1", _txt(20), "accept", predicted="payment")
        _seed_feedback(db, org, user, "c2", _txt(21), "modify", corrected="bogus")
        db.commit()
        examples, _ = build_examples(db, org.id, LABELS)
    finally:
        db.close()
    golds = [e["gold_label"] for e in examples if e["usable_supervised"]]
    assert golds == []  # accept is eval-only; bogus correction excluded


def test_audit_api_org_isolation_and_roles(client):
    token_a = register(client, "a@x.com", "password123", "OrgA")
    token_b = register(client, "b@x.com", "password123", "OrgB")
    r = client.get("/api/v1/ml-evaluation/feedback/audit",
                   headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audit"]["total"] == 0
    assert body["verdict"]["verdict"] == "INSUFFICIENT_CONTINUE_COLLECTING"
    rb = client.get("/api/v1/ml-evaluation/feedback/audit",
                    headers={"Authorization": f"Bearer {token_b}"})
    assert rb.status_code == 200
    # viewer forbidden
    db = TestingSession()
    try:
        from app.core.security import create_access_token

        from app.models import Organization as Org
        from app.models import OrganizationMember

        user = User(email="v@x.com", password_hash="x")
        db.add(user)
        db.flush()
        org = db.query(Org).filter(Org.name == "OrgA").one()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    assert client.get("/api/v1/ml-evaluation/feedback/audit",
                      headers={"Authorization": f"Bearer {vtok}"}).status_code == 403
