"""Retrieval endpoints (Phase 6.15). Org-scoped; org comes from membership.

Indexing (expensive): non-viewer roles. Search/status/precedent: any member.
Vector-store outages → 503 with explicit state; core flows never touch this.
"""
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import Contract, Template
from app.modules.retrieval import schemas
from app.modules.retrieval.auto_index import run_semantic_index
from app.modules.retrieval.indexer import (get_store, index_policy,
                                           index_status, index_template, reindex_scope)
from app.modules.retrieval.providers import EmptyTextError, EmbeddingUnavailableError
from app.modules.retrieval.search import precedent_for_clause, semantic_search
from app.modules.retrieval.vector_store import VectorStoreError

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


def _uuid_or_404(value: str | None, name: str) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, f"{name} not found")


@router.get("/health")
def health(member=Depends(get_current_membership)):
    try:
        ok = get_store().health_check()
    except VectorStoreError:
        ok = False
    return {"qdrant_reachable": ok}


@router.post("/index")
def index(body: schemas.IndexIn, member=Depends(get_current_membership),
          db: Session = Depends(get_db)):
    """Index a contract's clauses, a template's clauses, one policy, or
    re-index a scope under the current embedding model."""
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot trigger indexing")
    try:
        if body.scope == "contract":
            cid = _uuid_or_404(body.contract_id, "Contract")
            contract = db.get(Contract, cid) if cid else None
            if contract is None or contract.organization_id != member.organization_id:
                raise HTTPException(404, "Contract not found")
            # Automatic indexing covers normal processing; this stays as the
            # explicit re-index / retry-after-failure path (same service).
            return run_semantic_index(db, contract.id, triggered_by=member.user_id)
        if body.scope == "template":
            tid = _uuid_or_404(body.template_id, "Template")
            template = db.get(Template, tid) if tid else None
            if template is None or template.organization_id != member.organization_id:
                raise HTTPException(404, "Template not found")
            return index_template(db, template.id, triggered_by=member.user_id)
        if body.scope == "policy":
            pid = _uuid_or_404(body.policy_id, "Policy")
            from app.models import Policy

            policy = db.get(Policy, pid) if pid else None
            if policy is None or policy.organization_id != member.organization_id:
                raise HTTPException(404, "Policy not found")
            return index_policy(db, policy.id, triggered_by=member.user_id)
        if body.scope == "reindex":
            return reindex_scope(
                db, member.organization_id,
                contract_id=_uuid_or_404(body.contract_id, "Contract"),
                template_id=_uuid_or_404(body.template_id, "Template"))
        raise HTTPException(400, "scope must be contract|template|policy|reindex")
    except (VectorStoreError, EmbeddingUnavailableError) as e:
        raise HTTPException(503, f"Semantic backend unavailable: {e}")


@router.post("/search", response_model=schemas.SearchOut)
def search(body: schemas.SearchIn, member=Depends(get_current_membership),
           db: Session = Depends(get_db)):
    try:
        return semantic_search(
            db, org_id=member.organization_id, query=body.query, top_k=body.top_k,
            object_type=body.object_type,
            contract_id=_uuid_or_404(body.contract_id, "Contract"),
            template_id=_uuid_or_404(body.template_id, "Template"),
            category=body.category)
    except EmptyTextError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except (VectorStoreError, EmbeddingUnavailableError) as e:
        raise HTTPException(503, f"Semantic backend unavailable: {e}")


@router.get("/status")
def status(contract_id: str | None = Query(default=None),
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return index_status(db, member.organization_id,
                        _uuid_or_404(contract_id, "Contract"))


@router.get("/precedent")
def precedent(clause_id: str = Query(...), contract_id: str = Query(...),
              top_k: int = 5, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    """Similar historical clauses for one clause (+ previous analysis where
    present — explicitly NOT an approved legal precedent)."""
    cid = _uuid_or_404(clause_id, "Clause")
    contract = db.get(Contract, _uuid_or_404(contract_id, "Contract"))
    if contract is None or contract.organization_id != member.organization_id:
        raise HTTPException(404, "Contract not found")
    try:
        return precedent_for_clause(db, org_id=member.organization_id,
                                    clause_id=cid, top_k=top_k)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except (VectorStoreError, EmbeddingUnavailableError) as e:
        raise HTTPException(503, f"Semantic backend unavailable: {e}")


def _eval_base_dir() -> str:
    import os

    from app.modules.ml_evaluation.service import artifact_base

    return os.path.join(artifact_base(), "retrieval_eval")


@router.post("/eval-datasets/build", dependencies=[Depends(require_roles("admin"))])
def build_eval_dataset(member=Depends(get_current_membership),
                       db: Session = Depends(get_db),
                       seed: int = Query(default=42), max_queries: int = Query(default=50)):
    from app.modules.retrieval import eval_dataset as evd

    dataset = evd.build_eval_dataset(db, member.organization_id, seed=seed,
                                     max_queries=max_queries)
    if not dataset["examples"]:
        raise HTTPException(400, "No risk findings to build an eval dataset from.")
    try:
        info = evd.write_eval_dataset(dataset, _eval_base_dir())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**info, "dataset_version": dataset["dataset_version"],
            "n_queries": dataset["n_queries"],
            "n_human_confirmed": dataset["n_human_confirmed"]}


@router.get("/eval-datasets")
def list_eval_datasets(member=Depends(get_current_membership)):
    import glob
    import json

    out = []
    for path in sorted(glob.glob(os.path.join(_eval_base_dir(), "*.json"))):
        if os.path.basename(path).startswith("retrieval-eval-") and "-report" not in path:
            try:
                with open(path) as f:
                    meta = json.load(f)
                if meta.get("organization_id") != str(member.organization_id):
                    continue
                out.append({"dataset_version": meta.get("dataset_version"),
                            "n_queries": meta.get("n_queries"),
                            "created_at": meta.get("created_at")})
            except Exception:
                continue
    return out


@router.post("/evaluate", dependencies=[Depends(require_roles("admin"))])
def run_eval(member=Depends(get_current_membership), db: Session = Depends(get_db),
             dataset_version: str = Query(...), top_k: int = Query(default=10)):
    from app.modules.retrieval import evaluate_real as evr

    path = os.path.join(_eval_base_dir(), dataset_version + ".json")
    if not os.path.exists(path):
        raise HTTPException(404, "Evaluation dataset not found.")
    with open(path) as f:
        dataset = json.load(f)
    if dataset.get("organization_id") != str(member.organization_id):
        raise HTTPException(404, "Evaluation dataset not found.")
    try:
        report = evr.run_evaluation(
            db, member.organization_id, dataset, top_k=top_k,
            out_dir=_eval_base_dir())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(503, f"Retrieval evaluation failed: {e}")
    return report


@router.get("/evaluations")
def list_evaluations(member=Depends(get_current_membership)):
    import glob
    import json

    out = []
    for path in sorted(glob.glob(os.path.join(_eval_base_dir(), "retrieval-eval-*-report.json"))):
        try:
            with open(path) as f:
                rep = json.load(f)
            if "aggregate" not in rep:
                continue
            if (rep.get("config") or {}).get("organization_id") != str(member.organization_id):
                continue
            out.append({"dataset_version": (rep.get("config") or {}).get("dataset_version"),
                        "evaluated_at": (rep.get("config") or {}).get("evaluated_at"),
                        "aggregate": rep.get("aggregate")})
        except Exception:
            continue
    return out


@router.get("/evaluations/{dataset_version}")
def get_evaluation(dataset_version: str, member=Depends(get_current_membership)):
    import json

    path = os.path.join(_eval_base_dir(), f"retrieval-eval-{dataset_version}-report.json")
    # Only full report artifacts (with aggregate) are served here.
    if not os.path.exists(path):
        raise HTTPException(404, "Evaluation report not found.")
    with open(path) as f:
        rep = json.load(f)
    if "aggregate" not in rep:
        raise HTTPException(404, "Evaluation report not found.")
    if (rep.get("config") or {}).get("organization_id") != str(member.organization_id):
        raise HTTPException(404, "Evaluation report not found.")
    return rep
