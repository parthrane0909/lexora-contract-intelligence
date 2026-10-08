"""Contract DISCOVERY search (Part 2) — Elasticsearch.

Architecture separation (the whole point of this module):

    SEARCH SYSTEM
          │
   ┌──────┴───────────────┐
   ▼                      ▼
CONTRACT DISCOVERY    SEMANTIC SEARCH
 Elasticsearch         Qdrant (MiniLM-L6-v2, 384-d, cosine)
 filename / metadata / clause meaning / vector similarity
 fuzzy / partial /
 search-as-you-type

Elasticsearch answers "find the contract/file I am looking for".
Qdrant answers "find clauses meaning something similar to X".

Hard rules encoded here:
  * PostgreSQL is the SOURCE OF TRUTH for every contract record.
    Elasticsearch only ever holds a *search copy* — ownership, risk findings,
    analysis writes, deletion truth, auth and org membership are never read
    from it. Every search result is re-verified against org-scoped
    PostgreSQL rows before it reaches a response (see service.py).
  * Every search query enforces organization scope on the backend; the
    frontend never filters cross-org results.
  * No Elasticsearch URL is hard-coded anywhere: configuration lives in
    app/core/config.py (ELASTICSEARCH_*).
  * A cluster outage is a *search* failure (503 + UI error state), never a
    PostgreSQL failure and never a fallback to a different algorithm.
"""
