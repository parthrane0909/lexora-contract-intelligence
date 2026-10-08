"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useEffect, useState } from "react";
import { Breadcrumbs } from "@/components/ui";
import Link from "next/link";
import { useParams } from "next/navigation";
import { API_URL, authHeaders } from "@/lib/api";
import ReviewPanel from "@/components/ReviewPanel";
import RiskAssessmentSection, { ContractSummarySection } from "@/components/RiskSections";
import { StatusBadge, analysisLabel, clauseStateLabel, indexLabel, statusToneFor } from "@/components/ui";
import { processingLabel, processingTone } from "@/lib/contracts";
import {
  ContractSummaryPayload,
  Life,
  RaClause,
  RiskAssessment,
  RiskFinding,
  Stage,
  analysisNotice,
  stateChip,
  stateReason,
} from "@/lib/risk";

type Detail = {
  id: string; filename: string; processing_status: string; analysis_status: string;
  semantic_index_status?: string; semantic_index_error?: string | null;
  processing_error?: string | null; page_count?: number | null; clause_count?: number | null;
  model_versions?: string | null; analysis_lifecycle?: Life | null;
};
type Page = { page_number: number; char_start: number; char_end: number; text: string };
type Section = { heading: string; level: number; section_number?: string | null; order_index: number; page_start?: number | null };
type Clause = {
  id: string; section_number?: string | null; title?: string | null; text: string;
  page_start?: number | null; page_end?: number | null;
};
type Doc = { parser_name: string; parser_version: string; page_count: number; pages: Page[]; sections: Section[] };
type Cls = { clause_id: string; predicted_category: string; confidence: number; model_version: string };

const PRI_STYLE: Record<string, string> = {
  critical: "bg-red-600 text-white", high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800", low: "bg-slate-200 text-slate-700",
};

/** `model_versions` arrives as a JSON blob (`{"parser": …, "segmenter": …}`).
 *  Render it as readable key/value pairs instead of raw JSON. */
function formatModelVersions(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.entries(parsed).map(([k, v]) => `${k} ${String(v)}`).join(" · ");
    }
  } catch {
    /* not JSON — fall through and show the stored string verbatim */
  }
  return raw;
}

function highlighted(pageText: string, clauseText: string) {
  const needle = clauseText.slice(0, 80).trim();
  const i = needle ? pageText.indexOf(needle) : -1;
  if (i < 0) return <>{pageText}</>;
  return <>{pageText.slice(0, i)}<mark className="bg-yellow-200">{pageText.slice(i, i + needle.length)}</mark>{pageText.slice(i + needle.length)}</>;
}

/** Chip colour, state wording and the not-complete notice live in lib/risk
 *  so the summary, the risk section and the header can never word them
 *  differently. */

// ---------------------------------------------------------------------------
// Analysis orchestration (the "Run risk analysis" button).
//
// The backend exposes two separate, synchronous endpoints — POST /classify
// and POST /risk-analysis — and the risk engine deliberately never
// classifies. So one user action has to run them in order, check every
// response, then refresh from the AUTHORITATIVE GET /risk-assessment. Any
// failure stops the run and is shown verbatim (never turned into a quiet
// "Partial").
// ---------------------------------------------------------------------------

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
/** One orchestration run may not exceed this — polling always has an end. */
const RISK_TIMEOUT_MS = 120_000;
const RISK_POLL_MS = 1500;

/** Stage labels shown while the button works (index = current stage). */
const RISK_STEPS = [
  { key: "processing", label: "Checking document…" },
  { key: "classifying", label: "Classifying clauses…" },
  { key: "evaluating", label: "Evaluating risk…" },
  { key: "updating", label: "Updating assessment…" },
];

/** A message a reviewer can act on: `detail` when the API sent one,
 *  otherwise a cleaned-up body. Stack traces and HTML are never shown. */
function errorText(status: number, raw: string, body: unknown): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (typeof d === "string" ? d : (d as { msg?: string })?.msg))
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  const clean = (raw || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  if (clean && !/traceback|node_modules|sqlalchemy|at Object\.|\bat \w+ \(/i.test(clean)) {
    return clean.slice(0, 300);
  }
  return `The backend returned HTTP ${status}.`;
}

function errMessage(e: unknown): string {
  return e instanceof Error && e.message ? e.message : "Unexpected error.";
}

/** Status line for one action. `error`/`incomplete` render as alert and make
 *  the risk button read "Retry Risk Analysis". */
type Msg = { tone: "info" | "success" | "error" | "incomplete"; text: string } | null;

const MSG_TONE: Record<string, string> = {
  info: "text-ink-soft",
  success: "text-slate-700",
  error: "text-red-700",
  incomplete: "text-amber-800",
};

function MsgSpan({ m }: { m: NonNullable<Msg> }) {
  return (
    <span
      className={`text-xs ${MSG_TONE[m.tone] ?? MSG_TONE.info}`}
      role={m.tone === "error" || m.tone === "incomplete" ? "alert" : "status"}
    >
      {m.text}
    </span>
  );
}

export default function ContractViewer() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <ContractView />
    </RequireAuth>
  );
}

function ContractView() {
  const params = useParams();
  const id = params.id as string;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [clauses, setClauses] = useState<Clause[]>([]);
  const [clsMap, setClsMap] = useState<Record<string, Cls>>({});
  const [findings, setFindings] = useState<RiskFinding[]>([]);
  const [ra, setRa] = useState<RiskAssessment | null>(null);
  const [summary, setSummary] = useState<ContractSummaryPayload | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  // Bumped on every successful load so panels that fetch their own data
  // (lifecycle, policy overview) refetch after classify / risk / re-process
  // instead of keeping the values they saw on first mount.
  const [dataVersion, setDataVersion] = useState(0);
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Clause | null>(null);
  const [err, setErr] = useState("");
  const [procMsg, setProcMsg] = useState<Msg>(null);
  const [clsMsg, setClsMsg] = useState<Msg>(null);
  const [riskMsg, setRiskMsg] = useState<Msg>(null);
  const [lifeMsg, setLifeMsg] = useState<Msg>(null);
  const [idxMsg, setIdxMsg] = useState<Msg>(null);
  // One analysis action at a time: the three analysis buttons are disabled
  // while any of them runs, so a double click can never start a second run.
  const [busy, setBusy] = useState<"" | "risk" | "classify" | "reprocess">("");
  // Current RISK_STEPS index while the orchestrated run is in flight (-1 = idle).
  const [riskStep, setRiskStep] = useState(-1);

  async function load() {
    setErr("");
    const h = authHeaders();
    const dRes = await fetch(`${API_URL}/api/v1/contracts/${id}`, { headers: h });
    if (!dRes.ok) return setErr("Contract not found or not authorized.");
    const d = await dRes.json();
    setDetail(d);
    if (d.processing_status !== "processed") { setDataVersion((v) => v + 1); return; }
    const [docRes, clRes, clsRes, riskRes, raRes, sumRes] = await Promise.all([
      fetch(`${API_URL}/api/v1/contracts/${id}/document`, { headers: h }),
      fetch(`${API_URL}/api/v1/contracts/${id}/clauses`, { headers: h }),
      fetch(`${API_URL}/api/v1/contracts/${id}/classifications`, { headers: h }),
      // Fallback list only — risk STATE is never derived from it. The
      // authoritative payload is /risk-assessment, fetched right below.
      fetch(`${API_URL}/api/v1/contracts/${id}/findings`, { headers: h }),
      fetch(`${API_URL}/api/v1/contracts/${id}/risk-assessment`, { headers: h }),
      fetch(`${API_URL}/api/v1/contracts/${id}/summary`, { headers: h }),
    ]);
    if (docRes.ok) setDoc(await docRes.json());
    if (clRes.ok) setClauses(await clRes.json());
    if (clsRes.ok) {
      const arr: Cls[] = await clsRes.json();
      const m: Record<string, Cls> = {};
      arr.forEach((c) => { m[c.clause_id] = c; });
      setClsMap(m);
    }
    const raJson = raRes.ok ? await raRes.json() : null;
    setRa(raJson);
    // One findings list, one source of truth: risk-assessment's own findings
    // (identical rows to /findings, verified) whenever it loaded.
    const fallback: RiskFinding[] = riskRes.ok ? await riskRes.json() : [];
    setFindings(raJson ? raJson.findings : fallback);
    setSummary(sumRes.ok ? await sumRes.json() : null);
    setSummaryLoading(false);
    setDataVersion((v) => v + 1);
  }
  useEffect(() => { load(); }, [id]);

  // Non-blocking status polling: only while a background job is actually in
  // flight (processing or search indexing). Indexing-only polls touch just
  // the contract record, so the page stays responsive throughout.
  const idxStatus = detail?.semantic_index_status ?? "pending";
  const processingActive = !!detail && ["queued", "processing"].includes(detail.processing_status);
  const indexActive = !!detail && ["queued", "processing"].includes(idxStatus);
  async function refreshDetail() {
    try {
      const res = await fetch(`${API_URL}/api/v1/contracts/${id}`, { headers: authHeaders() });
      if (res.ok) setDetail(await res.json());
    } catch { /* keep last known state */ }
  }
  useEffect(() => {
    if (!processingActive && !indexActive) return;
    const t = setInterval(() => { if (processingActive) load(); else refreshDetail(); }, 3000);
    return () => clearInterval(t);
  }, [processingActive, indexActive, id]);

  // Deep link from comparison view: #clause-<id> selects + scrolls.
  useEffect(() => {
    const m = window.location.hash.match(/#clause-([0-9a-f-]+)/);
    if (!m) return;
    const target = clauses.find((c) => c.id === m[1]);
    if (target) {
      setSel(target);
      if (target.page_start) setPage(target.page_start);
      setTimeout(() => document.getElementById(`clause-${target.id}`)?.scrollIntoView({ block: "center" }), 300);
    }
  }, [clauses]);

  // Deep link from the contracts table: /contracts/{id}?view=summary|risk
  // scrolls to and focuses that section. Both sections only render once the
  // document is processed, so this retries briefly rather than failing on the
  // first tick after navigation.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const view = new URLSearchParams(window.location.search).get("view");
    const selector =
      view === "risk" ? "#risk-assessment" : view === "summary" ? "#summary" : null;
    if (!selector) return;
    let tries = 0;
    const timer = setInterval(() => {
      const el = document.querySelector<HTMLElement>(selector);
      if (el) {
        clearInterval(timer);
        el.scrollIntoView({ block: "start" });
        if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
        el.focus({ preventScroll: true });
      } else if (++tries > 80) {
        clearInterval(timer); // ~24s: the section never appeared (not processed)
      }
    }, 300);
    return () => clearInterval(timer);
  }, [detail?.processing_status, dataVersion, id]);

  /** POST one orchestration step, always checking `response.ok`. Backend
   *  `detail` is surfaced as-is; stack traces and HTML never are. */
  async function postStep(path: string): Promise<Record<string, unknown>> {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/v1/contracts/${id}${path}`, {
        method: "POST",
        headers: authHeaders(),
      });
    } catch {
      throw new Error("Could not reach the backend — check that it is running, then retry.");
    }
    const raw = await res.text();
    let body: unknown = null;
    try { body = raw ? JSON.parse(raw) : null; } catch { /* non-JSON body */ }
    if (!res.ok) throw new Error(errorText(res.status, raw, body));
    return (body as Record<string, unknown>) ?? {};
  }

  /** Authoritative lifecycle — the only state the run is allowed to trust. */
  async function fetchLife(): Promise<Life> {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/v1/contracts/${id}/risk-assessment`, {
        headers: authHeaders(),
      });
    } catch {
      throw new Error("Could not reach the backend — check that it is running, then retry.");
    }
    const raw = await res.text();
    if (!res.ok) throw new Error(errorText(res.status, raw, null));
    return (JSON.parse(raw) as RiskAssessment).analysis;
  }

  /** The document pipeline's own status (re-processing runs in the worker). */
  async function fetchProcessingStatus(): Promise<string> {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/v1/contracts/${id}`, { headers: authHeaders() });
    } catch {
      throw new Error("Could not reach the backend — check that it is running, then retry.");
    }
    const raw = await res.text();
    if (!res.ok) throw new Error(errorText(res.status, raw, null));
    return (JSON.parse(raw) as Detail).processing_status || "?";
  }

  /** Poll the authoritative endpoint until `done` — bounded by the run's
   *  deadline, so polling can never continue forever. */
  async function waitUntil(
    done: (life: Life) => boolean,
    deadline: number,
    timeoutMsg: string
  ): Promise<Life> {
    let life = await fetchLife();
    while (!done(life)) {
      if (Date.now() > deadline) throw new Error(timeoutMsg);
      await sleep(RISK_POLL_MS);
      life = await fetchLife();
    }
    return life;
  }

  /** Re-process = document pipeline only (asynchronous, in the worker):
   *  enqueue, then poll the document status until it is terminal so the
   *  message on screen is always true. */
  async function runAnalysis() {
    if (busy) return;
    setBusy("reprocess");
    setProcMsg({ tone: "info", text: "Re-processing document…" });
    try {
      await postStep("/analyze");
      const deadline = Date.now() + RISK_TIMEOUT_MS;
      let status = "?";
      while (Date.now() < deadline) {
        status = await fetchProcessingStatus();
        if (status === "processed" || status === "failed") break;
        await sleep(RISK_POLL_MS);
      }
      if (status === "processed") {
        setProcMsg({
          tone: "success",
          text: "Document re-processed — run risk analysis again to rebuild the assessment.",
        });
      } else if (status === "failed") {
        setProcMsg({
          tone: "error",
          text: "Re-process failed — the document pipeline reported an error. Open Processing details for it, then retry.",
        });
      } else {
        setProcMsg({
          tone: "incomplete",
          text: "Re-processing is still running — refresh the page in a moment to see the new status.",
        });
      }
      try { await load(); } catch { /* keep last known state */ }
    } catch (e) {
      setProcMsg({ tone: "error", text: `Re-process failed — ${errMessage(e)}` });
    } finally {
      setBusy("");
    }
  }

  /** Manual/advanced single step: classification only. */
  async function runClassify() {
    if (busy) return;
    setBusy("classify");
    setClsMsg({ tone: "info", text: "Classifying…" });
    try {
      const j = await postStep("/classify");
      const n = typeof j.classified === "number" ? j.classified : null;
      const t = typeof j.clauses === "number" ? j.clauses : null;
      setClsMsg({
        tone: "success",
        text: `Classified ${n ?? "?"}${t != null ? ` of ${t}` : ""} clauses.`,
      });
    } catch (e) {
      setClsMsg({ tone: "error", text: `Classification failed — ${errMessage(e)}` });
    } finally {
      setBusy("");
      try { await load(); } catch { /* keep last known state */ }
    }
  }

  /** "Run risk analysis" — the whole workflow in one click:
   *  processing complete → classify if missing/stale/failed → run the risk
   *  rules → persist → refresh the authoritative /risk-assessment → judge
   *  the result. Anything that fails stops here and is shown, never
   *  translated into a quiet "Partial". */
  async function runRisk() {
    if (busy) return;
    setBusy("risk");
    setRiskStep(0);
    setRiskMsg(null);
    const deadline = Date.now() + RISK_TIMEOUT_MS;
    const timeoutMsg =
      "Timed out after 2 minutes waiting for the backend — refresh the page to check the state, then retry.";
    try {
      // 1 — risk rules need a finished document.
      let life = await waitUntil(
        (l) => l.processing.state === "complete" || l.processing.state === "failed",
        deadline,
        timeoutMsg
      );
      if (life.processing.state !== "complete") {
        throw new Error(
          life.processing.error
            || `Document processing must finish first — it is currently ${analysisLabel(life.processing.state)}.`
        );
      }

      // 2 — classification runs only when it is actually missing or stale.
      setRiskStep(1);
      if (life.classification.state === "running") {
        life = await waitUntil((l) => l.classification.state !== "running", deadline, timeoutMsg);
      }
      if (life.classification.state !== "complete") {
        await postStep("/classify");
        life = await waitUntil((l) => l.classification.state !== "running", deadline, timeoutMsg);
      }
      if (life.classification.state === "failed") {
        throw new Error(life.classification.error || "Clause classification failed.");
      }

      // 3 — the deterministic risk rules (this is what the button promises).
      setRiskStep(2);
      await postStep("/risk-analysis");
      life = await waitUntil((l) => l.risk_analysis.state !== "running", deadline, timeoutMsg);
      if (life.risk_analysis.state === "failed") {
        throw new Error(life.risk_analysis.error || "Risk analysis failed.");
      }

      // 4 — refresh every panel, then judge the AUTHORITATIVE result.
      setRiskStep(3);
      const finalLife = await fetchLife();
      try { await load(); } catch { /* keep last known state */ }

      const c = finalLife.classification;
      const r = finalLife.risk_analysis;
      const counts = `${c.classified}/${c.clauses} clauses classified, ${r.unclassified} not analyzed.`;
      if (finalLife.overall === "complete") {
        const n = r.findings ?? 0;
        setRiskMsg({
          tone: "success",
          text: `Risk analysis complete — ${n} finding${n === 1 ? "" : "s"} across ${c.classified} of ${c.clauses} clauses.`,
        });
      } else if (c.error || r.error || finalLife.processing.error) {
        setRiskMsg({ tone: "error", text: `Risk analysis failed — ${c.error || r.error || finalLife.processing.error} (${counts})` });
      } else if (finalLife.overall === "failed") {
        setRiskMsg({ tone: "error", text: `Risk analysis failed — the backend reported a failed analysis run. (${counts})` });
      } else {
        setRiskMsg({
          tone: "incomplete",
          text: `Risk analysis did not complete — the assessment is ${analysisLabel(finalLife.overall)}: ${counts} Review the statuses above and retry.`,
        });
      }
    } catch (e) {
      setRiskMsg({ tone: "error", text: `Risk analysis failed — ${errMessage(e)}` });
      try { await load(); } catch { /* keep last known state */ }
    } finally {
      setBusy("");
      setRiskStep(-1);
    }
  }

  async function runIndex() {
    setIdxMsg({ tone: "info", text: "Preparing search…" });
    try {
      const res = await fetch(`${API_URL}/api/v1/retrieval/index`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "contract", contract_id: id }),
      });
      if (!res.ok) {
        const raw = await res.text();
        setIdxMsg({ tone: "error", text: `Indexing failed — ${errorText(res.status, raw, null)}` });
        return;
      }
      const j = await res.json();
      setIdxMsg({
        tone: j.status === "skipped" ? "info" : "success",
        text: j.status === "skipped"
          ? "Not ready yet — processing must finish first."
          : `Indexed (${j.indexed} clauses)`,
      });
    } catch {
      setIdxMsg({ tone: "error", text: "Unable to reach the backend." });
    }
    try { await load(); } catch { /* keep last known state */ }
  }

  async function runLifecycle() {
    setLifeMsg({ tone: "info", text: "Extracting lifecycle…" });
    try {
      const j = await postStep("/obligations/extract");
      const n = typeof j.obligations === "number" ? j.obligations : null;
      setLifeMsg({ tone: "success", text: `Extracted ${n ?? "?"} obligations.` });
    } catch (e) {
      setLifeMsg({ tone: "error", text: `Lifecycle extraction failed — ${errMessage(e)}` });
    }
    try { await load(); } catch { /* keep last known state */ }
  }

  if (err) return <main><p className="text-red-600">{err}</p><Link className="underline" href="/contracts">Back to contracts</Link></main>;
  if (!detail) return <main>Loading…</main>;

  // Risk-assessment is the authoritative lifecycle; the contract detail
  // endpoint is only the fallback when it could not be loaded.
  const life = ra?.analysis ?? detail.analysis_lifecycle ?? null;
  const clsStage: Stage = life?.classification ?? { state: detail.analysis_status };
  const riskStage: Stage = life?.risk_analysis ?? { state: "not_run", findings: 0 };
  const stateFor: Record<string, RaClause> = {};
  ra?.clauses.forEach((c) => { stateFor[c.id] = c; });

  // "View in document": select the clause, jump to its page, scroll to it.
  function jumpTo(clauseId: string) {
    const target = clauses.find((c) => c.id === clauseId);
    if (target) {
      setSel(target);
      if (target.page_start) setPage(target.page_start);
    }
    setTimeout(
      () => document.getElementById(`clause-${clauseId}`)?.scrollIntoView({ block: "center" }),
      60
    );
  }

  const pageText = doc?.pages.find((p) => p.page_number === page)?.text ?? "";
  const maxPage = doc?.page_count ?? 1;

  return (
    <main className="space-y-4">
      <Breadcrumbs items={[{ label: "Contracts", href: "/contracts" }, { label: detail.filename }]} />
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-2xl font-bold break-all">{detail.filename}</h1>
        <StatusBadge tone={processingTone(detail.processing_status)}
          title="Document processing state">
          {processingLabel(detail.processing_status)}
        </StatusBadge>
        {detail.model_versions && (
          <span className="text-xs text-ink-mute" title={detail.model_versions}>
            {formatModelVersions(detail.model_versions)}
          </span>
        )}
        {/* Re-processing = new clause ids → earlier classification/findings
            become stale (the API says so). Not a risk re-run. */}
        <button className="text-sm underline disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={runAnalysis} disabled={busy !== ""}>
          {busy === "reprocess" ? "Re-processing…" : "Re-process document"}
        </button>
        <button className="text-sm underline disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={runClassify} disabled={busy !== ""}>
          {busy === "classify" ? "Classifying…" : "Classify clauses"}
        </button>
        {/* One click, whole workflow: processing check → classification →
            risk rules → refresh. Disabled + stage-labelled while running. */}
        <button className="text-sm underline disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={runRisk} disabled={busy !== ""} data-testid="run-risk-analysis">
          {busy === "risk" ? "Analyzing contract…"
            : riskMsg?.tone === "error" || riskMsg?.tone === "incomplete"
              ? "Retry Risk Analysis"
              : "Run risk analysis"}
        </button>
        {/* Indexing is automatic; the button is only a recovery/re-index path. */}
        {(idxStatus === "failed" || idxStatus === "indexed") && (
          <button className="text-sm underline" onClick={runIndex}>
            {idxStatus === "failed" ? "Retry indexing" : "Re-index"}
          </button>
        )}
        <button className="text-sm underline" onClick={runLifecycle}>Extract lifecycle</button>
        <button className="text-sm underline" onClick={load}>Refresh</button>
        {procMsg && <MsgSpan m={procMsg} />}
        {clsMsg && <MsgSpan m={clsMsg} />}
        {riskMsg && <MsgSpan m={riskMsg} />}
        {lifeMsg && <MsgSpan m={lifeMsg} />}
        {idxMsg && <MsgSpan m={idxMsg} />}
      </div>

      {/* Live progress for the orchestrated run — the page is busy, not
          frozen: every stage is named as it completes. */}
      {riskStep >= 0 && (
        <div className="card p-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
          role="status" aria-live="polite" data-testid="risk-progress">
          <span className="font-semibold text-slate-700">Analyzing contract…</span>
          {RISK_STEPS.map((s, i) => (
            <span
              key={s.key}
              className={i < riskStep ? "text-emerald-700"
                : i === riskStep ? "text-brand-800 font-semibold" : "text-ink-mute"}
            >
              {i < riskStep ? "✓ " : i === riskStep ? "● " : "○ "}
              {s.label}
            </span>
          ))}
        </div>
      )}

      <div className="card p-3 grid grid-cols-1 md:grid-cols-4 gap-3 text-sm" aria-live="polite">
        <div>
          <p className="text-xs text-ink-mute">Processing</p>
          <StatusBadge tone={statusToneFor(detail.processing_status)}>
            {detail.processing_status === "processed" ? "✓ Complete"
              : detail.processing_status === "failed" ? "✗ Failed"
              : ["queued", "processing"].includes(detail.processing_status) ? "⏳ In progress…"
              : "Not started"}
          </StatusBadge>
        </div>
        <div>
          <p className="text-xs text-ink-mute">Classification</p>
          <StatusBadge tone={statusToneFor(clsStage.state)}>{analysisLabel(clsStage.state)}</StatusBadge>
          {typeof clsStage.classified === "number" && (
            <p className="text-xs text-ink-mute mt-1">
              {clsStage.classified}/{clsStage.clauses} clauses classified
            </p>
          )}
          {clsStage.error && <p className="text-xs text-red-700 mt-1 break-words">{clsStage.error}</p>}
        </div>
        <div>
          <p className="text-xs text-ink-mute">Risk analysis</p>
          <StatusBadge tone={statusToneFor(riskStage.state)}>{analysisLabel(riskStage.state)}</StatusBadge>
          <p className="text-xs text-ink-mute mt-1">
            {riskStage.state === "not_run" ? "not run yet"
              : riskStage.state === "complete" && (riskStage.findings ?? 0) === 0
                ? "complete — no detected risk"
                : `${riskStage.findings ?? 0} finding${(riskStage.findings ?? 0) === 1 ? "" : "s"}` +
                  ((riskStage.unclassified ?? 0) > 0
                    ? ` · ${riskStage.unclassified} clause${riskStage.unclassified === 1 ? "" : "s"} not analyzed`
                    : "")}
          </p>
          {riskStage.error && <p className="text-xs text-red-700 mt-1 break-words">{riskStage.error}</p>}
        </div>
        <div>
          <p className="text-xs text-ink-mute">Semantic search</p>
          <span className="inline-flex items-center gap-2 flex-wrap">
            <StatusBadge tone={statusToneFor(idxStatus)}>{indexLabel(idxStatus)}</StatusBadge>
            {idxStatus === "failed" && (
              <button className="text-sm underline" onClick={runIndex}>Retry</button>
            )}
          </span>
          {idxStatus === "failed" && detail.semantic_index_error && (
            <p className="text-xs text-red-700 mt-1 break-words">{detail.semantic_index_error}</p>
          )}
        </div>
      </div>

      {analysisNotice(life) && (
        <div className="border border-amber-300 bg-amber-50 p-3 rounded text-sm" role="status">
          <p className="font-semibold text-amber-900">Risk analysis is not complete</p>
          <p className="text-amber-800 mt-1">{analysisNotice(life)}</p>
        </div>
      )}

      {detail.processing_status === "failed" && (
        <div className="border border-red-300 bg-red-50 p-3 rounded text-sm">
          <p className="font-semibold text-red-700">Processing failed</p>
          <p className="text-red-800">{detail.processing_error}</p>
        </div>
      )}
      {(detail.processing_status === "queued" || detail.processing_status === "processing") && (
        <p className="text-sm text-ink-soft">Processing in progress — statuses refresh automatically.</p>
      )}

      {detail.processing_status === "processed" && (
        <>
          {/* Contract summary first, and it only answers "what is this
              contract about?" — no states, no counts, no risk wording. */}
          <ContractSummarySection
            filename={detail.filename}
            clauses={clauses}
            summary={summary}
            loading={summaryLoading}
            onJump={jumpTo}
          />
          {/* Risk assessment owns everything about risk: coverage, overall
              state, counts, findings and the clause-by-clause list. */}
          <RiskAssessmentSection life={life} ra={ra} loading={summaryLoading} onJump={jumpTo} />
        </>
      )}

      {detail.processing_status === "processed" && doc && (
        <>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <aside className="md:col-span-1 border rounded p-3 space-y-2 max-h-[70vh] overflow-auto">
            <h2 className="font-semibold text-sm">Sections ({doc.sections.length})</h2>
            {doc.sections.map((s) => (
              <button key={s.order_index} className="block text-left text-xs w-full hover:bg-slate-100 p-1 rounded"
                onClick={() => s.page_start && setPage(s.page_start)}>
                <span className="text-ink-mute">§{s.section_number ?? "–"} · p.{s.page_start ?? "?"}</span><br />{s.heading}
              </button>
            ))}
          </aside>

          <div className="md:col-span-2 border rounded p-3">
            <div className="flex items-center gap-2 mb-2">
              <button className="px-2 py-1 border rounded text-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
              <span className="text-sm">Page {page} / {maxPage}</span>
              <button className="px-2 py-1 border rounded text-sm" disabled={page >= maxPage} onClick={() => setPage(page + 1)}>Next</button>
            </div>
            <pre className="whitespace-pre-wrap text-sm max-h-[60vh] overflow-auto">
              {sel && sel.page_start === page ? highlighted(pageText, sel.text) : pageText}
            </pre>
          </div>

          <aside className="md:col-span-1 border rounded p-3 space-y-2 max-h-[70vh] overflow-auto">
            <h2 className="font-semibold text-sm">Clauses ({clauses.length}) · Findings ({findings.length})</h2>
            {clauses.map((c) => {
              const cf = findings.filter((f) => f.clause_id === c.id);
              const top = cf.some((f) => f.priority === "critical") ? "critical"
                : cf.some((f) => f.priority === "high") ? "high"
                : cf.some((f) => f.priority === "medium") ? "medium"
                : cf.length ? "low" : null;
              return (
              <button key={c.id} id={`clause-${c.id}`}
                className={`block text-left text-xs w-full p-2 rounded border ${sel?.id === c.id ? "bg-yellow-50 border-yellow-400" : "hover:bg-slate-100"}`}
                onClick={() => { setSel(c); if (c.page_start) setPage(c.page_start); }}>
                <span className="font-semibold">
                  {top && <span className={`inline-block w-2 h-2 rounded-full mr-1 ${top === "critical" || top === "high" ? "bg-red-500" : top === "medium" ? "bg-amber-500" : "bg-slate-400"}`} title={`${cf.length} finding(s), top: ${top}`} />}
                  {c.section_number ? `§${c.section_number} · ` : ""}{c.title}
                </span>
                <span className="block text-ink-mute">p.{c.page_start ?? "?"}–{c.page_end ?? "?"}</span>
                {clsMap[c.id] && (
                  <span className="inline-block mt-1 text-[11px] px-2 py-0.5 rounded bg-indigo-100 text-indigo-800">
                    {clsMap[c.id].predicted_category} · {(clsMap[c.id].confidence * 100).toFixed(0)}%
                  </span>
                )}
                {cf.length > 0 && (
                  <span className={`inline-block mt-1 ml-1 text-[11px] px-2 py-0.5 rounded ${PRI_STYLE[top ?? "low"]}`}>
                    {cf.length} finding{cf.length > 1 ? "s" : ""} · {top}
                  </span>
                )}
                {stateFor[c.id] && (
                  <span className={`inline-block mt-1 text-[11px] px-2 py-0.5 rounded ${stateChip(stateFor[c.id].risk_state)}`}
                    title={stateFor[c.id].state_reason}>
                    {clauseStateLabel(stateFor[c.id].risk_state)}
                  </span>
                )}
              </button>
              );
            })}
            {sel && (
              <div className="text-xs border-t pt-2">
                <p className="font-semibold">Selected clause source</p>
                <p className="text-ink-mute">ID {sel.id} · §{sel.section_number ?? "–"} · pages {sel.page_start}–{sel.page_end}</p>
                {stateFor[sel.id] && (
                  <p className="mt-1">
                    Analysis: <b>{clauseStateLabel(stateFor[sel.id].risk_state)}</b>
                    <span className="text-ink-mute"> · {stateReason(stateFor[sel.id].state_reason)}</span>
                  </p>
                )}
                {clsMap[sel.id] && (
                  <p className="mt-1">Predicted: <b>{clsMap[sel.id].predicted_category}</b> ({(clsMap[sel.id].confidence * 100).toFixed(1)}%) · model {clsMap[sel.id].model_version}</p>
                )}
                {findings.filter((f) => f.clause_id === sel.id).map((f) => (
                  <div key={f.id} className="mt-2 border rounded p-2 bg-white">
                    <p className="text-[10px] font-semibold text-ink-mute">DETERMINISTIC RISK ANALYSIS</p>
                    <p><span className={`inline-block text-[11px] px-2 py-0.5 rounded mr-1 ${PRI_STYLE[f.priority]}`}>{f.priority}</span>
                    <b>{f.title}</b></p>
                    <p className="mt-1 text-ink-soft">{f.reason}</p>
                    <p className="mt-1 text-ink-mute">{f.risk_category} · {f.finding_type} · rule {f.rule_id} v{f.rule_version}
                      {f.policy_version != null && ` · policy v${f.policy_version}`}
                      {f.classification_confidence != null && ` · ML conf ${(f.classification_confidence * 100).toFixed(0)}%`}</p>
                    {(f.priority_reasons?.length ?? 0) > 0 && (
                      <ul className="mt-1 list-disc ml-4 text-ink-soft">
                        {(f.priority_reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}
                      </ul>
                    )}
                    {f.explanation?.attention && (
                      <p className="mt-1 text-slate-700"><b>Attention:</b> {f.explanation.attention}</p>
                    )}
                    <ExplanationPanel contractId={id} findingId={f.id} />
                    <ReviewPanel contractId={id} findingId={f.id} />
                  </div>
                ))}
                <p className="mt-1 whitespace-pre-wrap">{sel.text}</p>
              </div>
            )}
          </aside>
        </div>
        </>
      )}

      {/* Other information — below the risk assessment, never above it. */}
      {detail.processing_status === "processed" && (
        <>
          <PolicyOverview contractId={id} reloadKey={dataVersion} />
          <LifecycleSection contractId={id} reloadKey={dataVersion} />
        </>
      )}
    </main>
  );
}

type OvRow = {
  category: string; policy_version?: number | null; contract_summary?: string | null;
  policy_summary?: string | null; status: string; finding_ids: string[];
};

type Expl = {
  id: string; status: string; summary: string; issue: string; why_it_matters: string;
  evidence_summary: string; policy_comparison: string; template_comparison: string;
  historical_context: string; recommendation: string; limitations: string;
  source_references: { source_index: number; quote: string }[];
  model: string; model_version: string; prompt_version: string; evidence_count: number;
  error?: string | null; created_at: string;
};

type EvItem = {
  evidence_id: string; source_type: string; source_id: string; title: string;
  page?: number | null; section?: string | null; similarity_score?: number | null;
  excerpt: string; contract_id?: string | null; clause_id?: string | null;
  template_id?: string | null;
};

function sourceLink(contractId: string, e: EvItem) {
  if ((e.source_type === "contract_clause" || e.source_type === "historical_clause")
      && e.contract_id && e.clause_id) {
    return `/contracts/${e.contract_id}#clause-${e.clause_id}`;
  }
  if (e.source_type === "template_clause" && e.template_id) {
    return `/templates/${e.template_id}`;
  }
  if (e.source_type === "policy") return "/policies";
  return null;
}

function ExplanationPanel({ contractId, findingId }: { contractId: string; findingId: string }) {
  const [expl, setExpl] = useState<Expl | null>(null);
  const [evidence, setEvidence] = useState<EvItem[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "done" | "failed" | "unavailable">("idle");
  const [msg, setMsg] = useState("");

  async function refresh() {
    const h = authHeaders();
    const [eRes, vRes] = await Promise.all([
      fetch(`${API_URL}/api/v1/contracts/${contractId}/findings/${findingId}/explanation`, { headers: h }),
      fetch(`${API_URL}/api/v1/contracts/${contractId}/findings/${findingId}/evidence`, { headers: h }),
    ]);
    if (eRes.ok) { setExpl(await eRes.json()); setState("done"); }
    else if (eRes.status === 404) setState("idle");
    else setState("failed");
    if (vRes.ok) setEvidence(await vRes.json());
  }
  useEffect(() => { refresh(); }, [contractId, findingId]);

  async function generate() {
    setState("loading");
    setMsg("");
    const res = await fetch(
      `${API_URL}/api/v1/contracts/${contractId}/findings/${findingId}/explanation`,
      { method: "POST", headers: authHeaders() });
    if (res.ok) { setExpl(await res.json()); setState("done"); }
    else if (res.status === 503) { setState("unavailable"); setMsg("Explanation service unavailable."); }
    else if (res.status === 403) { setState("idle"); setMsg("Your role cannot generate explanations."); }
    else { setState("failed"); setMsg(`Failed: ${await res.text()}`); }
    refresh();
  }

  const byIndex: Record<number, EvItem> = {};
  evidence.forEach((e, i) => { byIndex[i + 1] = e; });

  return (
    <div className="mt-2 border-t pt-2">
      <p className="text-[10px] font-semibold text-ink-mute">EVIDENCE</p>
      {evidence.map((e, i) => {
        const link = sourceLink(contractId, e);
        return (
          <p key={e.evidence_id} className="mt-1">
            [{i + 1}] <b>{e.source_type}</b> — {e.title}
            {e.similarity_score != null && <span className="text-ink-mute"> (sim {e.similarity_score.toFixed(2)})</span>}
            {link && <Link className="underline ml-2" href={link}>View source</Link>}
            <br /><span className="text-ink-soft">{e.excerpt.slice(0, 220)}</span>
          </p>
        );
      })}
      <p className="text-[10px] font-semibold text-ink-mute mt-2">AI-GENERATED EXPLANATION (does not determine risk)</p>
      {state === "idle" && (
        <button className="text-xs underline mt-1" onClick={generate}>Generate explanation</button>
      )}
      {state === "loading" && <p className="text-xs mt-1">Generating…</p>}
      {msg && <p className="text-xs mt-1 text-ink-soft">{msg}</p>}
      {state === "unavailable" && (
        <p className="text-xs mt-1 text-amber-700">Explanation service unavailable — deterministic analysis above is unaffected.</p>
      )}
      {state === "done" && expl && (
        <div className="text-xs mt-1 space-y-1">
          <p><b>Summary:</b> {expl.summary}</p>
          <p><b>Why it matters:</b> {expl.why_it_matters}</p>
          <p><b>Policy comparison:</b> {expl.policy_comparison}</p>
          {expl.template_comparison && <p><b>Template comparison:</b> {expl.template_comparison}</p>}
          {expl.historical_context && <p><b>Historical context:</b> {expl.historical_context}</p>}
          <p><b>Recommendation:</b> {expl.recommendation}</p>
          <p className="text-ink-mute"><b>Limitations:</b> {expl.limitations}</p>
          <p className="text-ink-mute">Sources: {expl.source_references.map((r) => {
            const target = byIndex[r.source_index];
            const link = target ? sourceLink(contractId, target) : null;
            return (
              <span key={r.source_index} className="mr-2">
                [{r.source_index}] “{r.quote.slice(0, 60)}”
                {link && <Link className="underline ml-1" href={link}>open</Link>}
              </span>
            );
          })} · {expl.model} · prompt {expl.prompt_version}</p>
          <button className="underline" onClick={generate}>Regenerate</button>
        </div>
      )}
    </div>
  );
}

const OV_STYLE: Record<string, string> = {
  deviation: "bg-red-100 text-red-800",
  within_policy: "bg-green-100 text-green-800",
  no_data: "bg-slate-200 text-ink-soft",
};

function PolicyOverview({ contractId, reloadKey }: { contractId: string; reloadKey?: number }) {
  const [rows, setRows] = useState<OvRow[]>([]);
  useEffect(() => {
    fetch(`${API_URL}/api/v1/contracts/${contractId}/policy-overview`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) setRows(j.rows); });
  }, [contractId, reloadKey]);
  if (!rows.length) return null;
  return (
    <div className="border rounded p-3">
      <h2 className="font-semibold text-sm mb-2">Contract policy review</h2>
      <table className="w-full text-xs border">
        <thead><tr className="bg-slate-100">
          <th className="p-2 text-left">Category</th><th className="p-2 text-left">Contract</th>
          <th className="p-2 text-left">Policy</th><th className="p-2">Status</th>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.category} className="border-t">
              <td className="p-2 font-medium">{r.category}{r.policy_version != null && ` (v${r.policy_version})`}</td>
              <td className="p-2">{r.contract_summary ?? "–"}</td>
              <td className="p-2">{r.policy_summary ?? "–"}</td>
              <td className="p-2 text-center">
                <span className={`inline-block px-2 py-0.5 rounded ${OV_STYLE[r.status] ?? OV_STYLE.no_data}`}>
                  {r.status.replace("_", " ")}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Ob = {
  id: string; clause_id: string; type: string; title: string;
  description?: string | null; party?: string | null; trigger?: string | null;
  period_days?: number | null; notice_period_days?: number | null;
  deadline?: string | null; renewal_date?: string | null; notice_deadline?: string | null;
  amount?: number | null; currency?: string | null; status: string;
  confidence?: number | null; extraction_method: string; risk_finding_id?: string | null;
};

type LcEvent = {
  id: string; obligation_id?: string | null; event_type: string;
  event_date?: string | null; status: string; source_clause_id?: string | null;
  description?: string | null;
};

type LcSummary = {
  contract_id: string; status: string; reason?: string;
  effective_date?: string | null; expiration_date?: string | null;
  renewal: Record<string, string | number | boolean | null>;
  open_obligations: number; upcoming_events: number; overdue_events: number;
  obligations: Ob[]; events: LcEvent[];
};

const OB_STYLE: Record<string, string> = {
  UNCONFIRMED: "bg-slate-200 text-slate-700",
  CONFIRMED: "bg-green-100 text-green-800",
  ACTIVE: "bg-green-100 text-green-800",
  COMPLETED: "bg-slate-200 text-ink-soft",
  OVERDUE: "bg-red-100 text-red-800",
  WAIVED: "bg-slate-200 text-ink-mute",
  NOT_APPLICABLE: "bg-slate-200 text-ink-mute",
};

const EV_STYLE: Record<string, string> = {
  UPCOMING: "bg-slate-200 text-slate-700",
  DUE: "bg-amber-100 text-amber-800",
  OVERDUE: "bg-red-100 text-red-800",
  COMPLETED: "bg-green-100 text-green-800",
  CANCELLED: "bg-slate-200 text-ink-mute",
};

function LifecycleSection({ contractId, reloadKey }: { contractId: string; reloadKey?: number }) {
  const [lc, setLc] = useState<LcSummary | null>(null);
  const [msg, setMsg] = useState("");
  const [comment, setComment] = useState<Record<string, string>>({});

  async function load() {
    const r = await fetch(`${API_URL}/api/v1/contracts/${contractId}/lifecycle`, { headers: authHeaders() });
    if (r.ok) setLc(await r.json());
  }
  useEffect(() => { load(); }, [contractId, reloadKey]);
  if (!lc || lc.obligations.length === 0) return null;

  async function review(obId: string, decision: string) {
    setMsg("");
    const res = await fetch(`${API_URL}/api/v1/obligations/${obId}/reviews`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ decision, comment: comment[obId] ?? undefined,
        ...(decision === "modify" ? { correction_type: "OTHER" } : {}) }),
    });
    if (!res.ok) return setMsg(`Failed: ${await res.text()}`);
    load();
  }

  const r = lc.renewal ?? {};
  return (
    <div className="border rounded p-3">
      <h2 className="font-semibold text-sm mb-2">Contract lifecycle</h2>
      <div className="text-xs flex gap-4 flex-wrap mb-2">
        <span>Contract status: <b>{lc.status}</b></span>
        <span>Expiration: <b>{lc.expiration_date ?? "–"}</b></span>
        <span>Renewal: <b>{r.renewal_type === "AUTO" ? `Auto-renewal${r.renewal_period_days ? ` — ${r.renewal_period_days} days` : ""}` : (r.renewal_type ?? "–")}</b></span>
        <span>Notice deadline: <b>{(r.notice_deadline as string) ?? "–"}</b></span>
        <span>Open obligations: <b>{lc.open_obligations}</b></span>
        <span>Upcoming events: <b>{lc.upcoming_events}</b></span>
        <span>Overdue: <b>{lc.overdue_events}</b></span>
      </div>
      {lc.reason && <p className="text-xs text-ink-mute mb-2">{lc.reason}</p>}
      {msg && <p className="text-xs text-ink-soft mb-2">{msg}</p>}
      <table className="w-full text-xs border">
        <thead><tr className="bg-slate-100">
          <th className="p-2 text-left">Obligation</th><th className="p-2 text-left">Date / deadline</th>
          <th className="p-2">Party</th><th className="p-2">Status</th><th className="p-2 text-left">Source / review</th>
        </tr></thead>
        <tbody>
          {lc.obligations.map((o) => (
            <tr key={o.id} className="border-t">
              <td className="p-2">
                <b>{o.type}</b> — {o.title}
                <span className="block text-ink-mute">
                  {o.trigger && `trigger: ${o.trigger}`}
                  {o.confidence != null && ` · extraction confidence: ${o.confidence.toFixed(2)}`}
                </span>
              </td>
              <td className="p-2">{o.deadline ?? o.renewal_date ?? o.notice_deadline ?? (o.period_days != null ? `${o.period_days} days` : "–")}</td>
              <td className="p-2 text-center">{o.party ?? "–"}</td>
              <td className="p-2 text-center">
                <span className={`inline-block px-2 py-0.5 rounded ${OB_STYLE[o.status] ?? ""}`}>{o.status.replace("_", " ")}</span>
                <span className="block text-ink-mute mt-1">{o.status === "UNCONFIRMED" ? "EXTRACTED" : "HUMAN CONFIRMED"}</span>
              </td>
              <td className="p-2">
                <Link className="underline" href={`/contracts/${contractId}#clause-${o.clause_id}`}>View source clause</Link>
                <span className="block mt-1 flex gap-1">
                  {(["confirm", "reject"] as const).map((d) => (
                    <button key={d} className="underline" onClick={() => review(o.id, d)}>{d}</button>
                  ))}
                </span>
                <input className="border rounded p-1 mt-1 w-full" placeholder="comment (required to reject)"
                  value={comment[o.id] ?? ""} onChange={(e) => setComment((s) => ({ ...s, [o.id]: e.target.value }))} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {lc.events.length > 0 && (
        <>
          <h3 className="font-semibold text-xs mt-3 mb-1">Events</h3>
          <table className="w-full text-xs border">
            <thead><tr className="bg-slate-100">
              <th className="p-2 text-left">Event</th><th className="p-2">Date</th><th className="p-2">State</th>
            </tr></thead>
            <tbody>
              {lc.events.map((e) => (
                <tr key={e.id} className="border-t">
                  <td className="p-2">{e.event_type.replace(/_/g, " ")}{e.description ? ` — ${e.description}` : ""}</td>
                  <td className="p-2 text-center">{e.event_date ?? "–"}</td>
                  <td className="p-2 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded ${EV_STYLE[e.status] ?? ""}`}>{e.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <RecurrencePanel obligations={lc.obligations} />
    </div>
  );
}

type Instance = {
  id: string; obligation_id: string; event_type: string; due_date?: string | null;
  status: string; completed_at?: string | null;
};

const FREQS = ["DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY", "BUSINESS_DAYS"];

function RecurrencePanel({ obligations }: { obligations: Ob[] }) {
  const [obId, setObId] = useState("");
  const [freq, setFreq] = useState("MONTHLY");
  const [anchor, setAnchor] = useState("");
  const [instances, setInstances] = useState<Instance[]>([]);
  const [recInfo, setRecInfo] = useState("");
  const [msg, setMsg] = useState("");

  async function loadInstances(id: string) {
    const r = await fetch(`${API_URL}/api/v1/obligations/${id}/instances`, { headers: authHeaders() });
    if (r.ok) setInstances(await r.json());
    const g = await fetch(`${API_URL}/api/v1/obligations/${id}/recurrence`, { headers: authHeaders() });
    if (g.ok) {
      const j = await g.json();
      setRecInfo(j ? `${j.frequency} → next ${j.next_due_date ?? "–"}` : "none configured");
    }
  }

  async function setup(e: React.FormEvent) {
    e.preventDefault();
    if (!obId) return;
    setMsg("");
    const res = await fetch(`${API_URL}/api/v1/obligations/${obId}/recurrence`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ frequency: freq, anchor_date: anchor || null }),
    });
    if (!res.ok) return setMsg(`Failed: ${await res.text()}`);
    loadInstances(obId);
  }

  async function transition(instId: string, action: "complete" | "skip") {
    const res = await fetch(
      `${API_URL}/api/v1/obligations/${obId}/instances/${instId}/${action}`,
      { method: "POST", headers: authHeaders() });
    if (!res.ok) return setMsg(`Failed: ${await res.text()}`);
    loadInstances(obId);
  }

  return (
    <div className="mt-3 border-t pt-2">
      <h3 className="font-semibold text-xs mb-1">Recurrence</h3>
      <form onSubmit={setup} className="flex gap-2 flex-wrap items-end text-xs">
        <label>Obligation
          <select className="block border rounded p-1 mt-1 w-64" value={obId}
            onChange={(e) => { setObId(e.target.value); if (e.target.value) loadInstances(e.target.value); }}>
            <option value="">— select —</option>
            {obligations.map((o) => <option key={o.id} value={o.id}>{o.type} — {o.title.slice(0, 40)}</option>)}
          </select>
        </label>
        <label>Frequency
          <select className="block border rounded p-1 mt-1" value={freq} onChange={(e) => setFreq(e.target.value)}>
            {FREQS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </label>
        <label>Anchor date (optional)
          <input className="block border rounded p-1 mt-1" placeholder="YYYY-MM-DD"
            value={anchor} onChange={(e) => setAnchor(e.target.value)} />
        </label>
        <button className="px-3 py-1 bg-slate-900 text-white rounded">Generate instances</button>
      </form>
      {msg && <p className="text-xs text-ink-soft mt-1">{msg}</p>}
      {recInfo && <p className="text-xs text-ink-mute mt-1">Active recurrence: {recInfo}</p>}
      {instances.length > 0 && (
        <table className="w-full text-xs border mt-2">
          <thead><tr className="bg-slate-100">
            <th className="p-2">Due date</th><th className="p-2">Status</th><th className="p-2">Action</th>
          </tr></thead>
          <tbody>
            {instances.map((i) => (
              <tr key={i.id} className="border-t">
                <td className="p-2 text-center">{i.due_date ?? "–"}</td>
                <td className="p-2 text-center">
                  <span className={`inline-block px-2 py-0.5 rounded ${EV_STYLE[i.status] ?? ""}`}>{i.status}</span>
                </td>
                <td className="p-2 text-center">
                  {(i.status === "UPCOMING" || i.status === "DUE" || i.status === "OVERDUE") && (
                    <>
                      <button className="underline mr-2" onClick={() => transition(i.id, "complete")}>Complete</button>
                      <button className="underline" onClick={() => transition(i.id, "skip")}>Skip</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
