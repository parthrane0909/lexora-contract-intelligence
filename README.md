# AI Contract Risk & Compliance Intelligence Platform

Phase 1 — Foundation: monorepo with FastAPI backend, Next.js frontend,
PostgreSQL, Redis, Qdrant, MinIO. Clean module boundaries per master spec §36.

## Architecture (Phase 1 scope)

```
frontend (Next.js) ──REST──> backend (FastAPI /api/v1)
                                ├─ modules/auth            (register/login, JWT)
                                ├─ modules/organizations   (orgs, members, RBAC)
                                ├─ modules/contracts       (upload/list/get/archive, re-analyze stub)
                                ├─ modules/storage         (MinIO/S3 ↔ local fallback)
                                ├─ modules/audit           (audit logs)
                                ├─ document/ ml_nlp/ risk_engine/ policy_engine/
                                │   retrieval/ llm_explanation/ review/  (Phase 2+ placeholders)
                                └─ worker (Redis enqueue stub → full pipeline in Phase 2)
PostgreSQL (structured) · Qdrant (vectors, Phase 6) · MinIO/S3 (objects)
```

Pipeline preserved (spec §2): contract → processing → segmentation → … → review.
Phase 1 implements upload → validation → object storage → DB rows → queued job stub.
ML/risk/RAG/LLM are separate modules, never a single giant service.

## Quickstart

```bash
cp .env.example .env
docker compose up --build
# backend  http://localhost:8001  docs http://localhost:8001/docs
# frontend http://localhost:3000
```

Notes: compose maps the backend to host port 8001 (host 8000 is commonly
taken by other projects). The frontend build bakes `NEXT_PUBLIC_API_URL`
(defaults to `http://localhost:8001`). MinIO is disabled in Compose until
a verified image reference is obtainable; `STORAGE_BACKEND=local` is the
active path. Qdrant reuses the existing `qdrant-phase6` volume (external,
never deleted by Compose). PostgreSQL gets a readiness healthcheck that
backend/worker wait on.

Local backend dev (no docker):

```bash
cd backend
~/miniconda3/bin/python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL="sqlite:///./dev.db" QDRANT_URL="http://localhost:6333" \
  STORAGE_DIR="./storage" uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Product workflow: login → Contracts → Upload Contract (PDF/DOCX, visible
uploading/processing states) → open contract → Run analysis → Contract
Summary (deterministic, clause-grounded) → risks/clauses/obligations →
policy & template comparison → evidence & human review → Portfolio for
org-wide intelligence; Semantic Search for concept search; Templates vs
Approved Templates (`/templates?view=approved`: active+ready only);
Policies vs Company Policies (`/policies?view=playbook`: read-only
playbook). RAG explanations require a configured LLM key and fail closed
otherwise — deterministic analysis is unaffected.

Run tests:

```bash
cd backend
python -m pytest -q
```

## Env (see .env.example)

`DATABASE_URL`, `JWT_SECRET`, `S3_*` / `STORAGE_BACKEND=minio|local`,
`REDIS_URL`, `QDRANT_URL`, `LLM_*` (OpenAI-compatible, used from Phase 7).

## API (v1, all org-scoped except register/login)

- `POST /api/v1/auth/register` `{email,password,org_name}` → user + org + tokens
- `POST /api/v1/auth/login` (OAuth2 password flow) → tokens
- `GET /api/v1/auth/me`
- `GET /api/v1/organizations/me` · `GET /api/v1/organizations/members`
- `POST /api/v1/contracts/upload` (multipart pdf/docx) → contract
- `GET /api/v1/contracts?search=&status=&category=&analysis_status=&important=&include_archived=&sort=&page=&limit=`
  (server-side filters/sort; total in `X-Total-Count`) · `GET /api/v1/contracts/{id}`
- `PATCH /api/v1/contracts/{id}` `{counterparty?, contract_type?, contract_category?, is_important?}`
  (admin/contract_manager; category is the whole-document type, `is_important` is an
  organizational star — neither is a risk score)
- `POST /api/v1/contracts/{id}/analyze` (enqueue stub) · `DELETE /api/v1/contracts/{id}`
  (archive = soft delete; related records, files and the semantic index are kept)
- `GET /api/v1/search/contracts?q=&category=&analysis_status=&processing_status=&important=`
- `GET /api/v1/contracts/{id}/summary` · `GET /api/v1/contracts/{id}/risk-assessment`
- `POST /api/v1/contracts/{id}/classify` · `POST /api/v1/contracts/{id}/risk-analysis`
- `GET /api/v1/contracts/{id}/findings?priority=&category=&finding_type=&run_id=`
- `GET /api/v1/audit?limit=` (admin/reviewer)

## Data isolation

Every query filters by `organization_id` from the JWT membership.
Cross-org access returns 404 (never 403-with-existence-leak). Tests in
`backend/tests/test_org_isolation.py` prove it.

## Phase 2 — Document Intelligence (implemented)

- `app/modules/document/`: `parsers.py` (pypdf / python-docx → pages, headings,
  paragraphs, tables, char offsets), `segmenters.py` (`ClauseSegmenter`
  protocol + `DeterministicSegmenter` + registry for future DL models),
  `pipeline.py` (`process_contract`: load → parse → persist → segment →
  persist → statuses/audit; failures → `failed` + message).
- Tables: `parsed_documents`, `document_pages` (verbatim text + paragraph
  spans), `document_sections`, `clauses` (stable UUIDs for future findings).
  `contracts.processing_error` records graceful failures.
- Worker runs the real pipeline off `analysis_queue`; `analyze` falls back to
  inline processing when Redis is unreachable (same result, same audit).
- Viewer: `/contracts/[id]` — page navigator, section list, clause list with
  source highlighting; failed docs show the processing error.
- DOCX has no native pagination → stored as one logical page (documented in
  code); PDF tables extract inline (cell reconstruction is a later upgrade).

## Phase 2.5/3 — Transformer Clause Classification (implemented)

- Sources: LEDGAR (`MAdAiLab/lex_glue_ledgar`, official splits, 60k/10k/10k) +
  CUAD v1 (`theatticusproject/cuad`, CC-BY-4.0, span → containing paragraph).
  Prep (`ml_nlp/dataset.py`) is separate from training; official splits kept,
  1,097 cross-split duplicates removed, CUAD split by contract (seed 42).
  Train 6,351 (caps ~500/class) / val 10,279 / test 10,317.
- Model: `nlpaueb/legal-bert-base-uncased` fine-tune (configurable), 14-way
  softmax (data_privacy reserved — no clean source data), class-weighted loss,
  3 epochs, fp16 on RTX 3050. MLflow run `78b2be4d8` (params, metrics,
  full model artifacts).
- **Measured held-out TEST metrics (n=10,317): accuracy 0.8723,
  macro F1 0.7472, micro F1 0.8723, weighted F1 0.8840.**
  Strong: insurance 0.922, dispute_resolution 0.896, governing_law 0.885.
  Weak: renewal F1 0.293 (support 29, over-predicted), termination 0.582,
  warranty 0.605 — minority recall high / precision low (class-weight effect).
- Inference (`ml_nlp/classifier.py`): Clause → Tokenizer → Transformer →
  Prediction → `clause_classifications` (append-only, run-linked). No LLM.
  Missing model → HTTP 503, empty clause → skipped, unknown clause → 404.
- Viewer: "Classify clauses" button, per-clause category + confidence badges,
  model version on selection.

## Phase 4 — Risk Detection & Policy Engine (implemented)

- `risk_engine/`: versioned taxonomy (honest support flags — data_privacy and
  IP/audit/dispute rules explicitly absent), 18 deterministic rules
  (`LIAB/PAY/TERM/REN/GOV/JUR/INS/CONF/IND/WAR-*`, v1.0), regex fact
  extractors (caps, days, jurisdictions), transparent priority
  (base severity + one high-impact policy bump, reasons stored).
- `policy_engine/`: per-org, per-category validated params, versioned rows
  (update = new version, history kept). No hard-coded company values.
- `analyzer.py`: consumes latest Phase 3 predictions (never re-runs the
  Transformer) → append-only `risk_findings` under `risk_analysis` runs;
  confidence &lt; 0.5 → `uncertain_classification`; unclassified clauses
  counted, never silently clean.
- Findings carry clause/section/page snapshot, evidence excerpt,
  rule+policy IDs/versions, contract-vs-expected values, classifier
  provenance. Viewer shows risk dots, badges, and per-finding reason/policy
  evidence; clicking a clause jumps to its page.

## Part 1 — Risk analysis integrity (implemented)

The stored rollup `contracts.analysis_status` used to be set to `processed` by
*document parsing*, so a contract could read "Analysis: Complete" while zero
clauses were classified and zero findings existed. Analysis state is now
derived from the stored artifacts, never asserted:

- `risk_engine/state.py` derives three separate stages per contract —
  processing (parse/segment), classification (clauses × predictions × latest
  `clause_classification` run) and risk analysis (clauses × predictions ×
  findings × latest `risk_analysis` run) — plus an `overall` state
  (`GET /contracts/{id}` → `analysis_lifecycle`, also inside `/summary`).
- A run that finished before the newest clause/prediction row (contract was
  re-processed → old classifications/findings cascade away) is reported as
  `stale`, never as success and never as "0 findings".
- A failed classification or risk run is surfaced with its error; `0 findings`
  after a failed run is shown as *failed*, not as a clean contract.
- `contracts.analysis_status` is only that coarse rollup, recomputed at the
  existing write points (pipeline / classify / risk): `pending`, `queued`,
  `classified`, `partial`, `stale`, `complete`, `failed`.
- Clause states — exactly five: `high_risk`, `medium_risk`, `low_risk`,
  `no_detected_risk`, `needs_review`, each with a `state_reason`.
  `no_detected_risk` means classified + analyzed + no rule matched;
  `needs_review` means no result exists (unclassified, not run, failed, stale).
- Contract aggregation (Phase 8 shape) lives on `GET /contracts/{id}/summary`
  (`overview`, `risk_summary`, `analysis`) and on the single risk endpoint
  `GET /contracts/{id}/risk-assessment` (lifecycle + summary + every clause,
  including no-risk and needs-review ones + findings). Both are recomputed on
  read from clauses, predictions, runs and findings: one scoring system, no LLM.
- Findings persist the facts the rule actually saw
  (`risk_findings.extracted_facts`, migration
  `0016_finding_extracted_facts`) and expose a structured explanation
  (`explanation.what/why/evidence/attention`, `risk_engine/explain.py`) built
  only from rule output + taxonomy, plus `requires_attention`.
- Viewer: Processing / Classification / Risk analysis / Semantic search are four
  separate states, clauses carry state chips, and an explicit banner explains
  why a contract is not complete (not started / classified only / partial /
  failed / stale).

Tests: `backend/tests/test_risk_assessment.py` (11 tests). Full suite:
**293 passed, 2 skipped**.

## Phase 5 — Company Intelligence & Template Comparison (implemented)

- `templates/`: approved template upload (same validation/storage/parser/
  segmenter as contracts — no second parser), version-per-row history,
  activate/reprocess/archive, optional best-effort classification.
- `comparison/`: `ClauseMatcher` protocol + `DeterministicMatcher`
  (section → title → classified-category + token overlap; vector matcher
  plugs in later), value diffs via existing risk extractors
  (cap/days/jurisdiction), deterministic materiality
  (material/potentially_material/informational), policy-deviation linkage.
  `missing` ≡ spec's "removed" (template clause absent from contract).
- Policy UI (`/policies`): active positions with version/creator/timestamp,
  per-category editors, history, restore-as-new-version, deactivate;
  viewers read-only. Contract policy-review table in the viewer, derived
  from active policies × latest risk run (no duplicated logic).
- Comparison view (`/templates/[id]`): side-by-side template vs contract
  with value-diff tables; contract side deep-links to `#clause-{id}` in
  the viewer (auto-select + scroll).

## Phase 6 — Semantic Intelligence (implemented)
Architecture (retrieval only — no generation, risk engine untouched):

```
clauses / template clauses / policies
  ↓  retrieval/indexer.py (batch embed, chunked, idempotent)
EmbeddingProvider (MiniLM default, hash fallback for tests)
  ↓  retrieval/vector_store.py (VectorStore protocol)
Qdrant collection `semantic_objects` (cosine)  ←→  PG semantic_index (state only)
  ↓  retrieval/search.py (org filter ALWAYS from membership)
semantic search / precedent / SemanticClauseMatcher ("semantic")
```

- Embeddings: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, v`minilm-l6-v2`),
  env-configured (`EMBEDDING_MODEL/VERSION`); Legal-BERT NOT reused.
- Point IDs = UUID5(org, type, id, model); search pins
  `embedding_model == current` — geometries never mix; reindex drops stale.
- Payload: org_id, object_type, object_id, contract/template refs, category,
  section/page, snippet, model/version. Payload indexes on org/type/contract.
- Matcher: `semantic:{strong|possible}:{score}` methods in comparison reasons;
  thresholds `SEMANTIC_MATCH_THRESHOLD=0.55` / `STRONG=0.80` are engineering
  defaults — similarity is NOT legal equivalence.
- Precedent returns "similar historical clauses" + previous analysis, never
  "approved precedent". Policy vectors are context only; deterministic
  policy engine stays authoritative.
- Measured (MiniLM, n=10 clauses / 5 queries): recall@1 0.90, recall@5 1.00,
  precision@1 1.00; template matching 3/3 (0.85 strong, 0.76 possible,
  unrelated correctly unmatched). Identical on Qdrant and in-memory paths.
- Limits: English MiniLM; Qdrant outage → explicit 503, core flows unaffected;
  qdrant-client 1.12 needs server ≥1.10 (compose pinned to v1.12.1).

## Phase 7 — Evidence-Grounded RAG & Explanation (implemented)

Architecture (the LLM explains; it never detects, grades, or overrides):

```
Risk Finding (deterministic, authoritative)
  ↓  llm_explanation/evidence.py (clause mandatory → policy → template →
       comparison → historical via Phase 6; org validated per source)
EvidenceRanker (fixed priority order) → ContextBuilder (numbered [n] sources,
6000-char budget, clause always complete)
  ↓  versioned prompt v1 (fenced: explain only, cite [n], no new law/facts,
       "may warrant legal review" language)
LLMProvider (OpenAI-compatible incl. local Ollama; fake is tests-only)
  ↓  GroundingValidator (schema, citation resolution + verbatim quotes,
       org match, clause-[1] cited, banned legal conclusions)
Persisted Explanation (append-only + frozen evidence snapshot)
  ↓  Human Reviewer
```

- Provider: `LLM_PROVIDER=openai-compatible` (default; `LLM_BASE_URL/KEY/MODEL`,
  stdlib urllib, no SDK); verified live against local Ollama `llama3.2`.
  Unconfigured/down LLM → explicit 503 + persisted `failed` row, never a fake.
- No contract text in logs (ids/counts/latency only). External-API privacy is
  deployment-configurable via `LLM_BASE_URL` (local Ollama = no data leaves).
- Measured grounding-pipeline eval: citation validity 1.0, schema validity 1.0,
  bad-citation rejection 1.0, unsupported-claim rejection 1.0, evidence recall
  (clause/policy/template) 1.0. These evaluate the pipeline, not legal accuracy.
- Limits: explanation quality follows the underlying LLM; validator checks
  grounding mechanics, not truth; historical evidence needs indexed clauses;
  generation is synchronous (background jobs later).

## Phase 8 — Human Review & Feedback Ledger (implemented)

Architecture (reviewer is final; AI never auto-decides):

```
Risk Finding (append-only, never mutated by review)
  ↓  Evidence + AI Explanation (existing layers)
Human Review → accept | reject | modify | needs_more_review
  ↓  snapshot (finding + explanation versions frozen at review time)
ReviewDecision (append-only) + FeedbackLedger row (atomic) + audit event
  ↓  Future Evaluation (export JSONL; no auto-retraining anywhere)
```

- Decisions: accept (comment optional) / reject+comment / modify+comment+
  correction_type (10 fixed values) + optional corrected_* / needs_more_review+
  comment. Comments plain text ≤2000 chars. Review state derived from latest
  decision: unreviewed/reviewed/needs_more_review (no `in_review` — that needs
  assignment/locking, a future concern).
- Snapshot freezes finding fields + visible explanation version, so reviews
  stay meaningful after policy/template/model changes. Concurrent reviewers
  each get their own row; latest = newest timestamp.
- Ledger rows pair original AI output (category/priority/rule/classifier +
  confidence/policy/explanation versions) with the human decision — the
  evaluation dataset. Terminology: "reviewer-rejected", never unqualified
  "false positive" (except explicit FALSE_POSITIVE corrections).
- RBAC: submit = admin/legal_reviewer/contract_manager; read = all members;
  export = admin/legal_reviewer only; cross-org → 404; comments never logged.
- UI: HUMAN REVIEW section per finding (status badge, decision buttons,
  correction controls, append-only history with explanation pins) + review
  dashboard (review rate, high/critical unreviewed, rejections, corrections).

## Phase 9 — Contract Lifecycle & Obligation Intelligence (implemented)

"Lifecycle intelligence identifies potentially relevant contractual obligations
and events for human review; it does not provide legal advice."

Architecture (extends, never duplicates):

```
Clauses (Phase 2, same IDs)
  ↓  obligations/extractors.py — ObligationExtractor protocol;
     DeterministicObligationExtractor reuses Phase 4 risk extractors
     (facts only, never verdicts) + date/period/trigger/party parsing
Obligations (typed columns, NULL when unknowable; type/dates immutable)
  ↓  obligations/dates.py + lifecycle.py — deterministic calculation;
     renewal/notice dates only with anchors, else NULL + explicit reason
Lifecycle events (per-extract derivation, status recomputed on read)
  ↓  obligations/service.py — direct callable (worker may call later)
Lifecycle view / notifications foundation / dashboard
  ↓  obligation_reviews + lifecycle_feedback (Phase 8 *patterns*,
     separate tables; Phase 8 untouched) → future evaluation
```

- Taxonomy v1.0: 19 types with honest `supported` flags (unsupported ones are
  never emitted); 11 event types; lifecycle ACTIVE/EXPIRING/EXPIRED/… (no
  auto-expire without a reliable date; manual override wins); obligation
  UNCONFIRMED/CONFIRMED/… (status = workflow state, values immutable).
- Confidence is a documented deterministic heuristic (0.9/0.75/0.6),
  never presented as legal certainty or a model probability.
- Review: confirm/reject/modify (+correction taxonomy, corrected_* recorded
  while originals stay intact). Links to latest policy_deviation finding +
  active policy expectation are read-only references.
- Measured eval fixtures: extraction precision=1.000 recall=1.000 (6 labeled
  clauses); no fabricated dates on ambiguous input. These measure the
  deterministic pipeline, not legal accuracy.
- Limits: English regex extraction; word-number periods to twelve; US
  MM/DD/YYYY assumption; frequencies approximate (flagged); no background
  scheduler yet; Qdrant indexing of obligations deferred (structured queries
  first, per spec).

## Phase 10 — Notification Delivery & Obligation Recurrence (implemented)

"Notifications are operational reminders generated from contract lifecycle
data. They do not constitute legal advice or automatically perform
contractual actions."

Architecture (consumes Phase 9, never duplicates it):

```
Phase 9 Lifecycle Engine (UPCOMING / DUE / OVERDUE — authoritative)
  ↓  notifications/rules.py (windows × recipients × preferences)
Notification candidates (event detection vs delivery, separated)
  ↓  idempotency key org:event:recipient:type:window (UNIQUE)
notifications (append-only) → provider.send() → delivery ledger
  ↓  InApp (dev) / SMTP (env) / Webhook (signed) / FakeFail (tests)
User (in-app view; read/unread per recipient)
```

- Taxonomy v1.0 (7 types); windows 30/14/7/1/0/overdue as data
  (`DEFAULT_WINDOW_RULES`, env-JSON overridable); overdue emitted once.
- Preferences per member/type (absent = enabled); viewers read-only.
- Delivery ledger preserves every attempt + original error; bounded retries
  (`MAX_DELIVERY_ATTEMPTS=3`); no fake success ever (failure → FAILED).
- Recurrence: definition ⟂ instances; DAILY…YEARLY + BUSINESS_DAYS via
  Phase 9 `add_period` (anchor-derived, month-end safe); horizon-bounded
  (≤24mo), idempotent, human complete/skip with audit.
- Rejected obligations silent; unconfirmed get one REVIEW reminder.
- Measured: §36 windows/no-dupes/progression, §37 fail→retry (attempts=2,
  SENT, history kept), §35 month-end + quarterly series exact.
- Limits: no scheduler daemon (expose `run_notification_scan()` for cron/
  beat/CronJob); email/webhook need env config + real vendor testing;
  in-app is the default channel.

## Phase 11 — Multi-Contract Portfolio Intelligence (implemented)

"Portfolio intelligence aggregates stored contract analysis and lifecycle
data. Portfolio scores are operational prioritization metrics, not
legal-risk probabilities or legal advice."

Architecture (aggregation only — no recomputation, no duplicated logic):

```
risk_findings (latest run/contract) + review_decisions (latest/finding)
+ obligations + lifecycle_events + comparisons + feedback +
notifications  →  portfolio/ (SQL GROUP BY, org-scoped)
  → summary / attention / risks / obligations / renewals / deviations /
    counterparties / trends / feedback / notifications / drill-down
```

- Attention score v1.0 (exact weights: critical +10, high +6, unreviewed +3,
  overdue +5, renewal<30d +4, notice<30d +4, deviation +3), always with
  per-factor breakdown. Rejected findings contribute no risk weight but stay
  visible with their review state.
- Counterparty = member-supplied `contracts.counterparty` only (PATCH,
  admin/contract_manager); absent → explicit "Unspecified", never invented.
- Trends from append-only tables with stored timestamps (+ recorded
  model/rule versions); weekly/daily buckets.
- Limits: raw additive scores (unbounded); review-state filter needs ≥1
  finding; trends reflect record-creation times; semantic portfolio search
  deferred to a later phase.

## Disclaimer

## Phase 12 — ML Evaluation, Feedback Datasets & Model Improvement (implemented)

"Human review feedback is not automatically used to retrain or replace the
production model."

ML lifecycle (all identifiers linked — feedback → dataset → split →
training run → candidate → evaluation → promotion):

```
FeedbackLedger (org-scoped reviews)
  ↓  dataset.py: modify+valid correction → supervised gold;
     accept → eval-only (human-confirmed, never a "correction");
     reject/needs_more_review/ambiguous → excluded with reasons
dataset_builder.py: immutable versioned artifacts
  (examples/splits/manifest/quality.json + sha256; same version never rewritten)
  ↓  splits.py: contract_id-unit hashing (seeded); overlap verified, frozen
service.evaluate_bundle: full report (accuracy/micro/macro/weighted,
  per-class, confusion matrix) + calibration (ECE) + error analysis
  (FP/FN, misclassification pairs, truncation) → MLEvaluationRun row
training.py: Legal-BERT candidates only (never an LLM), MLflow candidates
  experiment, fingerprint-recorded configs
promotion.py: gates (macro/weighted no-regression + protected-class
  tolerance, default 0.03) are ADVISORY; POST …/promote (admin only)
  is the sole path to production — exactly one production row per task,
  demotions + events recorded.
```

- Evaluation metrics are engineering metrics; confidence is not legal
  certainty; model performance does not constitute legal accuracy; the
  system remains decision support for human legal review.
- Measured production baseline (frozen clause_v1 test, n=10317):
  accuracy 0.8723, macro F1 0.7472, weighted F1 0.8840; known weak spots
  renewal F1 0.293, termination 0.582, warranty 0.605 (high-recall /
  low-precision pattern); 36.45% of clauses exceed seq_len 128 with a
  higher error rate (0.162 vs 0.108); ECE 0.0668.
- Limits: feedback-derived training needs reviewed volume (Modify+correction
  rows); small org datasets risk imbalance (see quality report); candidate
  training is GPU-time-heavy — run offline, never in request paths;
  ACTIVE_MODEL_VERSION pinning requires the artifact to exist.

## Phase 13 — Controlled Experiments: Sequence Length & Class Weighting (implemented)

Single-seed ablations on frozen clause_v1 splits (train 6351 / validation
10279 / test 10317), same Legal-BERT recipe, one variable at a time:

- cells: baseline_repro (seq128+weighted), seq256, seq512, unweighted,
  seq256_weighted. Presets in `training_configs.py`, run via
  `register_experiment` → `run_experiment` (MLflow + registry writes).
- reported (single seed 42, frozen test): seq256_weighted accuracy 0.8782,
  macro F1 0.7629, weighted F1 0.8893 vs production seq128 0.8723/0.7472/
  0.8840; worst protected-class delta −0.0067; truncation 36.45% (128) →
  12.15% (256) → 1.28% (512), one clause beyond BERT max length.
- treat as historical reference, not proof: single seed, no CIs.

## Phase 14 — Statistical Validation & Final Model Selection (implemented, selection pending GPU evidence)

Answers: is the seq256 gain real and stable across seeds, configs,
protected classes, calibration, and truncation cost? Validation-first,
test-frozen.

- Schema: migration `0014_phase14_matrix_id` adds nullable indexed
  `ml_experiments.matrix_id` (model already declared it; 0013 lacked the
  column). Upgrade/downgrade verified on sqlite.
- Frozen-test discipline: `run_experiment(..., eval_split)` defaults to
  `"test"` (legacy) but matrix/selection paths force `"validation"`;
  invalid splits rejected; split recorded on the experiment row and report.
  Selection uses validation only; exactly one final frozen-test run follows.
- Matrix (`experiment_matrix.py`): A seq128+weighted, B seq256+weighted,
  C seq128 unweighted, D seq256 unweighted — identical data/splits/recipe
  except the named variable; optional LR-multiplier / epoch sensitivity
  cells (sensitivity, not HPO). `run_matrix`/`run_multi_seed` execute via the
  standard runner on validation; seeds default 42–46.
- Stats (`multi_seed.py`, `statistical.py`, re-exported via `metrics.py`):
  `aggregate_runs` (mean/std/min/max per scope + per-class);
  `paired_compare` (paired t-interval on per-seed differences, exact t
  critical values df≤30; n=5 ⇒ honestly wide CIs; "CI excludes zero" =
  consistent directional evidence, never proof or legal significance).
- Selection (`final_selection.py`): deterministic priority leakage-free →
  validation macro F1 → protected safety → stability → calibration →
  truncation/cost → accuracy/weighted tie-breaks; never highest test score.
  Emits selected version + rationale + rejected-with-reasons; `GET
  /classification/selection` serves it (org-scoped, validation only).
- Model card (`build/write_model_card`, `GET /models/{id}/model-card`):
  versioned JSON next to the artifact; states classification-only decision
  support, not legal advice; refuses to overwrite an existing card.
- Promotion/rollback (`promotion.py`): explicit admin promotion only;
  `rollback()` restores a version with proven production tenure + intact
  artifact, keeps exactly one production pointer, audits like a promotion;
  unevaluated/missing-artifact/already-production targets refused.
- APIs: matrix run/list/detail, selection, per-model statistics aggregate,
  protected-class vs-production breakdown, model card, promotion history
  (org-scoped), rollback — existing auth/RBAC; evaluation report contents
  stay metrics-only, raw texts never cross orgs.
- Frontend `/ml-evaluation`: experiment matrix, multi-seed aggregate,
  protected-class deltas, truncation excerpt, calibration/ECE, final
  selection, model card, promotion history + rollback — all rendered from
  measured API data, never hardcoded.
- Tests: `test_ml_multiseed.py` (aggregation, paired CI + guards, matrix
  build/validation-split enforcement, delegation, selection priority,
  card immutability, sensitivity, eval_split parameterization),
  `test_ml_promotion.py` (+rollback success/refusals),
  `test_ml_evaluation_api.py` (+matrix/card/statistics/rollback and
  +selection/protected/history/org-isolation); migration upgrade/downgrade
  verified; full suite + `tsc --noEmit` green.
- Reproduce: build/verify dataset → `run_matrix` (validation, seeds 42–46,
  cells A–D) → `paired_compare` + protected gates → `selection` →
  ONE frozen-test `run_experiment(eval_split="test")` on the winner →
  gates → explicit promote → card. GPU: Legal-BERT 3-epoch runs on a 4 GB
  card; bit-identical reproduction not promised across
  hardware/software; timings vary.
- Status: implementation + unit/integration verification complete; live
  multi-seed GPU evidence, final frozen test, and promotion decision are
  recorded in the Phase 14 report (this README makes no claim beyond what
  was actually run).

## Phase 15 — Human-Feedback Learning Loop (pipeline complete, no training data yet)

Phase 14 closed with production on `p14-prod-A42`, finalist D-s42 measured
but NOT promoted (artifact-level warranty gate −0.0415), and a standing
finding: renewal/warranty/termination need real human corrections, not more
seeds. Phase 15 builds that loop — and stops before training if the data is
not there.

- Pipeline (all pre-existing, verified, reused — nothing duplicated):
  human review (`review/decisions.py`: ACCEPT/MODIFY/REJECT/NEEDS_MORE_REVIEW)
  → `FeedbackLedger` (original prediction + decision + optional correction,
  append-only) → `dataset.build_examples` (modify+valid-correction →
  supervised gold; accept → eval-only human-confirmed; reject/ambiguous →
  excluded with machine-readable reasons) → `build_versioned_dataset`
  (immutable versions, sha256 manifests, contract-unit splits with
  `verify_no_overlap` leakage refusal) → existing training/promotion gates.
- New in Phase 15: `feedback_audit.py` (aggregate-only audit over the same
  eligibility rules: SUPERVISED_ELIGIBLE / EVALUATION_ONLY /
  EXCLUDED_AMBIGUOUS / EXCLUDED_INVALID / EXCLUDED_NON_CORRECTION, plus
  correction matrix, frozen-text overlap screen, and an explicit evidence
  verdict with per-requirement PASS/FAIL — never a bare label) and
  `GET /ml-evaluation/feedback/audit` (org-scoped, reviewer roles only,
  no raw clause texts) surfaced read-only on `/ml-evaluation`.
- Evidence verdict rules (stated policy, not vibes): ≥50 supervised AND ≥10
  unique contracts AND ≥5 weak-class (renewal/warranty/termination)
  corrections AND zero contradictions AND zero test overlap AND duplicate
  rate < 50% → SUFFICIENT; some clean data → LIMITED pilot only; else
  INSUFFICIENT. Thresholds are documented in `feedback_audit.VERDICT_RULES`.
- Measured inventory (dev.db, all orgs): **0 feedback rows — verdict
  INSUFFICIENT_CONTINUE_COLLECTING.** Per the phase's own stop conditions
  this is a successful outcome: no `clause_feedback_v1` was created, no
  experiment ran, no test was touched, production unchanged, no thresholds
  altered. Test fixtures use obviously-synthetic texts in tmp paths only.
- To reproduce later: collect MODIFY-with-correction reviews →
  `GET /ml-evaluation/feedback/audit` → if SUFFICIENT, `POST
  /ml-evaluation/datasets/build` with a `clause_feedback_*` version →
  small controlled seq256 experiment → validation gates → multi-seed
  confirmation → frozen test only for a gated finalist.

## Phase 16 — Retrieval & RAG Quality (baseline + hardening; improvement claim limited to derived evidence)

No human feedback exists (Phase 15: 0 corrections), so Phase 16 uses no
human labels at all: fixture judgments (authored in-repo, labeled FIXTURE)
and derived behavior (category-coherence leave-one-out over real
clause_v1 TRAIN texts — never validation/test — labeled DERIVED, never
gold). No LLM-as-judge (no LLM available; RAG generation covered by
deterministic fake-provider validator tests).

- Architecture (reused, not rebuilt): MiniLM-L6-v2 embeddings (384-dim,
  cosine) → Qdrant (`semantic_objects`, org_id mandatory filter, UUID5
  point ids, model-pinned search) → `semantic_search` (+ category/contract/
  template filters, PG enrichment with navigation anchors) → precedent
  path → RAG `collect_evidence` (deterministic priority clause → policy →
  template → comparison → historical) → bounded context (6000 chars,
  numbered sources) → versioned prompt v1 → deterministic validator
  (schema, citation resolution, quote substring, org match, mandatory
  clause citation, banned legal conclusions) → persisted explanation or
  auditable failure. Retrieval NEVER decides risk; LLM NEVER generates
  without evidence (empty context raises).
- Baseline (VERIFIED live runs): fixture eval, real MiniLM + real Qdrant —
  recall@1 0.9, recall@5 1.0, template matching 3/3 (n=5 queries: thin,
  labeled FIXTURE). Derived eval (112 queries, 14 categories × 8, real
  corpus texts, immutable artifact `retrieval-eval-derived-v1` + sha):
  raw cosine R@1 0.089 / R@5 0.305 / P@1 0.625 / MRR 0.726 / nDCG 0.476,
  zero-hit 0. Weakest: other, warranty, payment, IP — the same classes
  Phase 14 flagged, from an independent signal.
- Ablation (same dataset/config, recorded): category-filtered R@5 0.714,
  P@1 1.0 (uses the finding's known risk category — fair and available in
  production); deterministic reranker v1 (cosine + 0.15·category-match +
  0.05·source-priority, no learning) R@5 0.434 (+0.13), P@1 0.90, MRR 0.91
  at +0.1 ms. Verdict: reranker v1 is a justified approve-as-candidate
  (recorded, NOT auto-promoted); category filter is a precision tool with
  recall capped by K. No prompt change (no evidence it is the bottleneck).
- Hardening (new, tested): `diagnose_context` (composition/duplicates/
  truncation/category-match) + `assess_sufficiency`
  (SUFFICIENT/PARTIAL/WEAK on metadata only); hermetic regression suite
  (`test_retrieval_phase16.py`: rerank determinism/version, isolation on
  the vector path, empty-query rejection, stale-provenance handling,
  duplicate detection, negative retrieval); org isolation holds (structural
  org filter + app-layer revalidation; cross-org citation rate 0 by
  validator construction); failures classified (70 rank-1 hits / 24 low
  relevance / 18 ranking errors / 0 no-hit).
- RAG grounding (deterministic fakes): schema 1.0, citations 1.0, bad
  citations + unsupported claims rejected, evidence recall complete.
  Faithfulness beyond grounding (legal correctness) is NOT claimed —
  human reviewers remain the judges.
- Latency (measured, not optimized): embed ~9 ms/text batched, Qdrant
  search mean 10 ms (max 36 ms), rerank negligible.
- Production unchanged; no retrieval config promoted (reranker recorded
  as evaluated candidate only); p14 frozen test untouched; no synthetic
  labels; frontend unchanged (eval API + artifacts are the observability
  surface; no user-facing scores by design).

## Disclaimer

Assists human review and does not provide legal advice. Risk findings in later
phases are flagged as potential concerns / policy deviations, never claims of
illegality (spec §8).

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/ef293366-e4a2-4483-af84-63c4e09d66ba" />
