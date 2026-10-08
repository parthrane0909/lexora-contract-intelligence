"""Contract-level category (Part 5 Step 2): ONE primary category per document.

The category describes the WHOLE agreement ("SaaS Subscription Agreement",
"Non-Disclosure Agreement", …). It is a completely different thing from
clause classification: Legal-BERT predicts categories for individual clauses,
while this is document-level metadata stored on `contracts.contract_type`.

Why `contract_type` and not a new column
----------------------------------------
Phase 11 already added exactly this field: contract-level, member-supplied,
NULL when unknown, exposed by GET /contracts and GET /contracts/{id},
patchable through PATCH /contracts/{id}, and already indexed by
Elasticsearch (search/index.py maps it with a `.raw` keyword sub-field for
filtering). Reusing it keeps ONE category concept instead of two nearly
identical columns, and needs no migration.

Storage convention
------------------
    contracts.contract_type = "SaaS Subscription Agreement"  → stored category
    contracts.contract_type = NULL                           → "Other / Unspecified"
                                                                (explicit state,
                                                                 never a guess)

Vocabulary and detection rules
------------------------------
The label set and the regexes come from `summary.TYPE_RULES` — the
project's established, reviewed document-type table (Part 4). Importing it
guarantees the category shown in a listing is the same type the Contract
Summary reports, instead of two rule tables that could drift apart.

Detection is deterministic and conservative: document title first, file name
second, nothing else. A clause's classification NEVER types the contract, and
an unmatched document stays "Other / Unspecified" — the summary generator is
never touched by this module.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Contract
from app.modules.contracts.summary import TYPE_RULES

# The explicit state for a contract with no category (stored as NULL).
UNSPECIFIED = "Other / Unspecified"

# Reuse the established document-type rules (pattern, label, …) rather than
# maintaining a second, diverging copy of them.

# One extra conservative rule the summary table has no use for (it types
# documents for prose, this vocabulary also has to cover sales agreements).
_EXTRA_RULES: tuple[tuple[str, str], ...] = (
    (r"sales agreement|sale and purchase|purchase and sale", "Sales Agreement"),
)

CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    tuple((pattern, label) for pattern, label, _, _ in TYPE_RULES) + _EXTRA_RULES
)

# Fixed vocabulary: what the list/search filters accept and what a user may
# pick in the UI (Part 6). "Other / Unspecified" is part of it so the
# missing state is always selectable, never an implicit NULL.
CATEGORIES: tuple[str, ...] = tuple(
    dict.fromkeys(label for _, label in CATEGORY_RULES)
) + (UNSPECIFIED,)


def parse_category(value: str) -> str:
    """Validate a user-supplied category (filter value or PATCH value).

    Returns the canonical label or the UNSPECIFIED sentinel; raises
    ValueError for anything outside the vocabulary. Never builds SQL.
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("Category must not be empty.")
    if len(v) > 128:
        raise ValueError("Category must be at most 128 characters.")
    if v.lower() in ("unspecified", UNSPECIFIED.lower(), "other/unspecified",
                     "other / unspecified", "other"):
        return UNSPECIFIED
    if v in CATEGORIES:
        return v
    raise ValueError(
        f"Unknown contract category: {value!r}. "
        f"Expected one of: {', '.join(CATEGORIES)}"
    )


def suggest_category(*, filename: str | None = None,
                     title: str | None = None) -> str | None:
    """Conservative, deterministic suggestion from trustworthy signals only.

    Reads the document title first and the file name second (the same order
    the Contract Summary uses). Returns None when nothing matches — an
    ambiguous document stays Unspecified instead of being guessed.
    """
    for hay in (title, filename):
        if not hay:
            continue
        normalized = re.sub(r"[_\-]+", " ", hay)
        for pattern, label in CATEGORY_RULES:
            if re.search(pattern, normalized, re.I):
                return label
    return None


def _document_title(db: Session, contract: Contract) -> str | None:
    """Opening line of the stored document — the trustworthy title signal.

    Mirrors summary._document_title (same first clause, same guards); when
    the document has no usable title the caller falls back to the filename.
    """
    from app.models import Clause

    first = (db.query(Clause).filter(Clause.contract_id == contract.id)
             .order_by(Clause.order_index).first())
    if first is None or first.section_number:
        return None
    line = (first.text or "").split("\n", 1)[0].strip()
    if line and len(line) <= 90 and not re.match(r"^\d", line):
        return line
    heading = (first.title or "").strip()
    if heading and not heading.startswith("Clause ") and len(heading) <= 90:
        return heading
    return None


def backfill_categories(db: Session, org_id=None) -> dict:
    """Idempotent backfill of existing contracts (Part 5 Step 3).

    Only touches rows whose `contract_type` is still NULL — a category a
    human assigned (or any value already present) is never overwritten, and
    re-running changes nothing. Ambiguous documents are left NULL, i.e.
    Unspecified. Every updated row is republished to the search index
    (best-effort, PostgreSQL stays authoritative).
    """
    from app.modules.search.indexer import index_contract  # avoid import cycle

    q = db.query(Contract).filter(Contract.contract_type.is_(None))
    if org_id is not None:
        q = q.filter(Contract.organization_id == org_id)
    rows = q.order_by(Contract.created_at).all()

    updated: list[str] = []
    unspecified: list[str] = []
    for c in rows:
        category = suggest_category(
            filename=c.filename, title=_document_title(db, c))
        if category is None:
            unspecified.append(str(c.id))
            continue
        c.contract_type = category
        updated.append(str(c.id))
    db.commit()

    indexed = 0
    for c in rows:
        if str(c.id) in updated and index_contract(c):
            indexed += 1
    return {
        "considered": len(rows),
        "updated": len(updated),
        "left_unspecified": len(unspecified),
        "reindexed": indexed,
        "updated_ids": updated,
    }
