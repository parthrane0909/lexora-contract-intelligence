"""Evidence retrieval (Phase 7.9). Every item traceable to a stored source.

Priority (deterministic): 1 current clause (mandatory) → 2 active policy →
3 approved template clause (latest comparison) → 4 comparison evidence →
5 historical similar clauses (Phase 6) → 6 other indexed knowledge (skipped
in v1: no clean ingestion path exists — documented, not invented).

Org ownership is validated at the application layer on every source, in
addition to Qdrant filtering. Retrieval NEVER modifies the finding.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import (Clause, Comparison, ComparisonFinding, Policy, RiskFinding,
                        Template, TemplateClause)
from app.modules.policy_engine.service import active_policies

POLICY_TEXT_CHARS = 2000
HISTORY_TOP_K = 3


def _policy_text(category: str, params: dict) -> str:
    bits = "; ".join(f"{k} = {json.dumps(v)}" for k, v in sorted(params.items()))
    return f"Company policy [{category}]: {bits}"


def collect_evidence(db: Session, finding: RiskFinding, *,
                     provider=None, store=None) -> list[dict]:
    """Return ranked EvidenceItems (dicts). Raises ValueError if the
    mandatory current clause is missing (data corruption, never silence)."""
    org = finding.organization_id
    items: list[dict] = []
    seen: set[tuple] = set()

    def add(source_type: str, source_id: str, title: str, text: str, rank: int,
            category: str | None = None, page=None, section=None,
            similarity: float | None = None, provenance: str = "database",
            retrieval_method: str = "direct", metadata: dict | None = None,
            contract_id=None, clause_id=None, template_id=None):
        key = (source_type, source_id)
        if not text or not text.strip() or key in seen:
            return
        seen.add(key)
        items.append({
            "evidence_id": f"ev-{len(items) + 1}", "source_type": source_type,
            "source_id": source_id, "organization_id": str(org),
            "contract_id": str(contract_id) if contract_id else None,
            "clause_id": str(clause_id) if clause_id else None,
            "title": title, "text": text.strip(),
            "category": category, "page": page, "section": section,
            "similarity_score": similarity, "provenance": provenance,
            "retrieval_method": retrieval_method, "rank": rank,
            "metadata": metadata or {},
            "template_id": str(template_id) if template_id else None,
        })

    # 1. Current clause — mandatory.
    clause = db.get(Clause, finding.clause_id)
    if clause is None or clause.organization_id != org or not (clause.text or "").strip():
        raise ValueError("Current clause for this finding is missing or empty.")
    add("contract_clause", str(clause.id),
        f"Contract clause §{clause.section_number or '–'} (page {clause.page_start or '?'})",
        clause.text, rank=0, category=finding.risk_category,
        page=clause.page_start, section=clause.section_number,
        contract_id=clause.contract_id, clause_id=clause.id)

    # 2. Active policy for the finding's category.
    policies = active_policies(db, org)
    policy = policies.get(finding.risk_category)
    if policy is not None and policy.organization_id == org:
        params = json.loads(policy.params)
        add("policy", str(policy.id),
            f"Company policy [{policy.category}] v{policy.version}",
            _policy_text(policy.category, params)[:POLICY_TEXT_CHARS], rank=1,
            category=policy.category, provenance="policy_engine",
            metadata={"policy_version": policy.version})

    # 3+4. Latest comparison: template clause + comparison evidence.
    comp = (db.query(Comparison)
            .filter(Comparison.contract_id == finding.contract_id,
                    Comparison.organization_id == org)
            .order_by(Comparison.created_at.desc()).first())
    if comp is not None:
        rows = (db.query(ComparisonFinding)
                .filter(ComparisonFinding.comparison_id == comp.id,
                        ComparisonFinding.contract_clause_id == finding.clause_id).all())
        for row in rows:
            if row.template_clause_id is not None:
                tc = db.get(TemplateClause, row.template_clause_id)
                tpl = db.get(Template, row.template_id) if row.template_id else None
                if (tc is not None and tc.organization_id == org and tpl is not None
                        and tpl.organization_id == org):
                    add("template_clause", str(tc.id),
                        f"Approved template {tpl.name} v{tpl.version} §{tc.section_number or '–'}",
                        tc.text, rank=2, category=row.category,
                        page=tc.page_start, section=tc.section_number,
                        template_id=tpl.id,
                        metadata={"template_version": tpl.version,
                                  "difference_type": row.difference_type,
                                  "materiality": row.materiality})

    # 5. Historical similar clauses (Phase 6; org-scoped inside).
    try:
        from app.modules.retrieval.search import precedent_for_clause

        prec = precedent_for_clause(db, org_id=org, clause_id=finding.clause_id,
                                    top_k=HISTORY_TOP_K, provider=provider, store=store)
        for r in prec.get("results", []):
            if r.get("object_type") != "clause":
                continue
            try:
                hid = uuid.UUID(r["object_id"])
            except (ValueError, TypeError):
                continue
            hcl = db.get(Clause, hid)
            if hcl is None or hcl.organization_id != org or hcl.id == finding.clause_id:
                continue
            label = f"Historical clause §{hcl.section_number or '–'}"
            prev = (r.get("previous_analysis") or [{}])[0]
            if prev.get("rule_id"):
                label += f" (previously flagged {prev['rule_id']})"
            add("historical_clause", str(hcl.id), label, hcl.text, rank=3,
                page=hcl.page_start, section=hcl.section_number,
                similarity=r.get("score"), provenance="semantic_retrieval",
                retrieval_method="precedent_for_clause",
                metadata={"previous_rule": prev.get("rule_id")},
                contract_id=hcl.contract_id, clause_id=hcl.id)
    except Exception:
        # Retrieval is best-effort context: a down vector store must not kill
        # explanation of an already-detected finding. Absence is explicit below.
        pass

    items.sort(key=lambda e: e["rank"])
    for i, e in enumerate(items, 1):
        e["evidence_id"] = f"ev-{i}"
    return items
