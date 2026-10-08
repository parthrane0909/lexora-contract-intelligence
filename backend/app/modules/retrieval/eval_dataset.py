"""Versioned retrieval evaluation datasets (Phase 13.9).

Ground truth strength is explicit per example:
- human_confirmed: a reviewer accepted a finding/explanation touching the
  source, or the expectation comes from a validated comparison/template.
- derived: expectation inferred from system behavior (latest finding on the
  clause, template pairing). NEVER labeled "gold" — only human-confirmed
  rows may be called gold.

Queries come from risk-finding titles/reasons (what a reviewer would ask),
expected sources are clause/policy/template object ids. Per-org datasets;
nothing crosses organizations.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid

from sqlalchemy.orm import Session

from app.models import ComparisonFinding, Policy, ReviewDecision, RiskFinding

DATASET_VERSION_PREFIX = "retrieval-eval-v1"
EVAL_K_DEFAULT = (1, 3, 5, 10)


def _version(org_id: uuid.UUID, seed: int, n: int) -> str:
    raw = f"{org_id}:{seed}:{n}".encode()
    return f"{DATASET_VERSION_PREFIX}-{hashlib.sha256(raw).hexdigest()[:8]}"


def build_eval_dataset(db: Session, organization_id: uuid.UUID, *,
                       seed: int = 42, max_queries: int = 50) -> dict:
    """Assemble examples from the org's own findings + reviews + comparisons."""
    findings = (db.query(RiskFinding)
                .filter(RiskFinding.organization_id == organization_id)
                .order_by(RiskFinding.created_at).limit(max_queries * 2).all())
    reviewed = {(r.finding_id, r.decision) for r in db.query(ReviewDecision).filter(
        ReviewDecision.organization_id == organization_id).all()}
    reviewed_ids = {fid for fid, _ in reviewed}
    accepted_ids = {fid for fid, d in reviewed if d == "accept"}
    examples = []
    for f in findings[:max_queries]:
        expected = [{
            "object_type": "clause",
            "object_id": str(f.clause_id),
            "strength": ("human_confirmed"
                         if f.id in accepted_ids else "derived"),
        }]
        if f.policy_id:
            pol = db.get(Policy, f.policy_id)
            if pol is not None and pol.organization_id == organization_id:
                expected.append({
                    "object_type": "policy",
                    "object_id": str(pol.id),
                    "strength": "human_confirmed" if f.id in accepted_ids else "derived",
                })
        comp = (db.query(ComparisonFinding)
                .filter(ComparisonFinding.contract_id == f.contract_id,
                        ComparisonFinding.contract_clause_id == f.clause_id)
                .order_by(ComparisonFinding.created_at.desc()).first())
        if comp is not None and comp.template_clause_id is not None:
            expected.append({
                "object_type": "template_clause",
                "object_id": str(comp.template_clause_id),
                "strength": "derived",
            })
        examples.append({
            "query_id": f"q-{f.id.hex[:12]}",
            "query": f"{f.title}. {f.reason or ''}".strip(),
            "category": f.risk_category,
            "contract_id": str(f.contract_id),
            "expected": expected,
            "reviewed": f.id in reviewed_ids,
        })
    version = _version(organization_id, seed,
                       len(examples) + len(str(sorted(
                           e["query_id"] for e in examples))))
    return {
        "dataset_version": version,
        "organization_id": str(organization_id),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seed": seed,
        "n_queries": len(examples),
        "n_human_confirmed": sum(1 for e in examples if any(
            s["strength"] == "human_confirmed" for s in e["expected"])),
        "examples": examples,
    }


def write_eval_dataset(dataset: dict, base_dir: str) -> dict:
    """Persist versioned artifact (immutable by convention: refuse overwrite)."""
    path = os.path.join(base_dir, dataset["dataset_version"] + ".json")
    if os.path.exists(path):
        raise ValueError(f"Retrieval eval dataset {dataset['dataset_version']!r} "
                         f"already exists — build a new version instead.")
    os.makedirs(base_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(dataset, f, indent=2)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": path, "sha256": h.hexdigest()}
