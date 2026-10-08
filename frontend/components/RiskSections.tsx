"use client";

import { useState } from "react";
import { StatusBadge, analysisLabel, clauseStateLabel, statusToneFor } from "@/components/ui";
import {
  ContractSummaryPayload,
  Life,
  RISK_FILTERS,
  RiskAssessment,
  RiskFinding,
  SummaryClause,
  analysisHeadline,
  analysisNotice,
  clauseAttention,
  clauseLabel,
  clauseWhy,
  contractTerms,
  factLines,
  matchesFilter,
  priorityRank,
  sortForReview,
  stateChip,
  summaryParagraphs,
} from "@/lib/risk";

type Jump = (clauseId: string) => void;

/** Priority badge colours, shared by both sections. */
const PRI_STYLE: Record<string, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-slate-200 text-slate-700",
};

const COUNT_STYLE: Record<string, string> = {
  high: "border-red-200 bg-red-50 text-red-800",
  medium: "border-amber-200 bg-amber-50 text-amber-800",
  low: "border-slate-200 bg-slate-50 text-slate-700",
  none: "border-emerald-200 bg-emerald-50 text-emerald-800",
  review: "border-slate-300 bg-slate-100 text-slate-700",
  findings: "border-brand-200 bg-brand-50 text-brand-800",
  attention: "border-brand-300 bg-brand-100 text-brand-900",
};

function Count({ tone, label, value }: { tone: string; label: string; value: number }) {
  return (
    <div className={`border rounded p-2 text-center ${COUNT_STYLE[tone] ?? COUNT_STYLE.low}`}>
      <p className="text-lg font-bold leading-tight">{value}</p>
      <p className="text-[11px] uppercase tracking-wide">{label}</p>
    </div>
  );
}

// ===========================================================================
// CONTRACT SUMMARY — one job only: "what does this agreement say?"
// A document briefing in reading order: overview, agreement type, purpose,
// parties, commercial structure, term & renewal, key responsibilities and
// the main agreement areas — all descriptive, all traceable to the
// document itself. It deliberately renders NO risk state, NO counts, NO
// findings, NO rules and NO recommendations — every one of those belongs
// to the risk assessment below, which reads
// GET /api/v1/contracts/{id}/risk-assessment.
// ===========================================================================

type SummaryProps = {
  filename: string;
  clauses: SummaryClause[];
  summary: ContractSummaryPayload | null;
  loading: boolean;
  onJump: Jump;
};

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <tr className="border-t align-top">
      <th className="p-2 text-left font-semibold bg-slate-50 w-44">{label}</th>
      <td className="p-2">{value}</td>
    </tr>
  );
}

export function ContractSummarySection({ filename, clauses, summary, loading, onJump }: SummaryProps) {
  if (loading) return <p className="text-sm text-ink-mute">Preparing summary…</p>;

  if (!summary && clauses.length === 0) {
    return (
      <section className="card p-4 space-y-3" id="summary" aria-label="Contract summary">
        <h2 className="font-serif text-xl font-bold">Contract Summary</h2>
        <p className="text-sm text-amber-800 border border-amber-300 bg-amber-50 p-2 rounded" role="status">
          The contract summary could not be loaded.
        </p>
      </section>
    );
  }

  // Fallback: the /summary payload failed but the clause list loaded —
  // still purely descriptive, never a substitute for the risk section.
  if (!summary) {
    const paragraphs = summaryParagraphs(filename, clauses);
    const terms = contractTerms(clauses);
    return (
      <section className="card p-4 space-y-4" id="summary" aria-label="Contract summary">
        <div>
          <h2 className="font-serif text-xl font-bold">Contract Summary</h2>
          <p className="text-xs text-ink-mute">What does this agreement say?</p>
        </div>
        <p className="text-xs text-amber-800 border border-amber-300 bg-amber-50 p-2 rounded" role="status">
          The full summary could not be loaded — this brief is built from the
          clause list alone.
        </p>
        <div className="space-y-2">
          {paragraphs.map((p, i) => (
            <p key={i} className="text-sm">{p}</p>
          ))}
        </div>
        {terms.length > 0 && (
          <div>
            <h3 className="font-semibold text-sm mb-1">Key Contract Terms</h3>
            <table className="w-full text-xs border">
              <tbody>
                {terms.map((t) => (
                  <tr key={t.label} className="border-t align-top">
                    <th className="p-2 text-left font-semibold bg-slate-50 w-40">{t.label}</th>
                    <td className="p-2">
                      {t.value}
                      {t.clauseId && (
                        <button className="underline ml-2 whitespace-nowrap" onClick={() => onJump(t.clauseId!)}>
                          open clause
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[11px] text-ink-mute">
          Assembled from this contract&apos;s own clauses and extracted structure — no
          generated claims, no scoring.
        </p>
      </section>
    );
  }

  const areas = summary.agreement_areas ?? [];
  const duties = summary.key_responsibilities ?? [];
  const obligations = summary.obligations ?? [];

  return (
    <section className="card p-4 space-y-4" id="summary" aria-label="Contract summary">
      <div>
        <h2 className="font-serif text-xl font-bold">Contract Summary</h2>
        <p className="text-xs text-ink-mute">What does this agreement say?</p>
      </div>

      {/* Overview — 2–5 plain-language sentences about the whole agreement. */}
      <div>
        <h3 className="font-semibold text-sm mb-1">Overview</h3>
        <p className="text-sm leading-relaxed">{summary.overview}</p>
      </div>

      {/* Agreement details — rows appear only when the document states them. */}
      <div>
        <h3 className="font-semibold text-sm mb-1">Agreement details</h3>
        <table className="w-full text-xs border">
          <tbody>
            <DetailRow
              label="Agreement type"
              value={summary.contract_type || "Unspecified (not stated in the document)"}
            />
            <DetailRow label="Purpose" value={summary.purpose} />
            <DetailRow label="Parties / roles" value={summary.parties.description} />
            {summary.counterparty && (
              <DetailRow label="Counterparty" value={summary.counterparty} />
            )}
            {summary.commercial_terms && (
              <DetailRow label="Commercial structure" value={summary.commercial_terms} />
            )}
            {summary.term_and_renewal && (
              <DetailRow label="Term & renewal" value={summary.term_and_renewal} />
            )}
          </tbody>
        </table>
      </div>

      {duties.length > 0 && (
        <div>
          <h3 className="font-semibold text-sm mb-1">Key responsibilities</h3>
          <ul className="text-sm list-disc ml-5 space-y-1">
            {duties.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Main agreement areas — what each major subject DOES, in document
          order, with the clauses it came from. Never whether it is risky. */}
      {areas.length > 0 && (
        <div>
          <h3 className="font-semibold text-sm mb-1">Main agreement areas</h3>
          <ul className="text-sm space-y-2">
            {areas.map((a) => (
              <li key={a.category} className="border rounded p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <b>{a.label}</b>
                  {a.sections.length > 0 && (
                    <span className="text-xs text-ink-mute">
                      {a.sections.map((s) => `§${s}`).join(", ")}
                    </span>
                  )}
                  {a.clause_ids[0] && (
                    <button
                      className="text-xs underline ml-auto whitespace-nowrap"
                      onClick={() => onJump(a.clause_ids[0])}
                    >
                      open clause
                    </button>
                  )}
                </div>
                <p className="text-xs text-slate-700 mt-1">{a.summary}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {obligations.length > 0 && (
        <div>
          <h3 className="font-semibold text-sm mb-1">Recorded obligations ({obligations.length})</h3>
          <ul className="text-xs space-y-1">
            {obligations.slice(0, 8).map((o) => (
              <li key={o.id}>
                {o.title}{" "}
                <span className="text-ink-mute">
                  · {o.status}
                  {o.party ? ` · ${o.party}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[11px] text-ink-mute">
        Assembled from this contract&apos;s own text and structure — descriptive only: no
        generated claims, no scoring, no recommendations.
      </p>
    </section>
  );
}

// ===========================================================================
// RISK ASSESSMENT — clause by clause: which clause, where, what state, why,
// which rule, what evidence, and whether a human has to look at it.
// Every clause is listed, including the ones with no detected risk.
// ===========================================================================

type RiskProps = {
  life: Life | null;
  ra: RiskAssessment | null;
  loading?: boolean;
  onJump: Jump;
};

export default function RiskAssessmentSection({ life, ra, loading, onJump }: RiskProps) {
  const [filter, setFilter] = useState("all");

  const activeLife = life ?? ra?.analysis ?? null;

  if (!ra) {
    return (
      <section className="card p-4 space-y-2" id="risk-assessment" aria-label="Risk assessment">
        <h2 className="font-serif text-xl font-bold">Risk assessment</h2>
        {loading ? (
          <p className="text-sm text-ink-mute" role="status">Loading risk assessment…</p>
        ) : (
          <p className="text-sm text-amber-800 border border-amber-300 bg-amber-50 p-2 rounded" role="status">
            Risk assessment data could not be loaded, so no clause states are shown here.
            Nothing below should be read as “no risk detected”.
          </p>
        )}
      </section>
    );
  }

  const rs = ra.summary;
  const sorted = sortForReview(ra.clauses);
  const rows = sorted.filter((c) => matchesFilter(c.risk_state, filter));
  const byId: Record<string, RiskFinding[]> = {};
  ra.findings.forEach((f) => {
    (byId[f.clause_id] = byId[f.clause_id] || []).push(f);
  });

  // "What should I look at first?" — the most severe findings, ordered by the
  // backend's own priority. This list (and only this section) may rank.
  const topAttention = [...ra.findings]
    .sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority))
    .slice(0, 5)
    .map((f) => {
      const clause = ra.clauses.find((c) => c.id === f.clause_id);
      return {
        key: f.id,
        priority: f.priority,
        label: clause ? clauseLabel(clause) : (f.clause_title ?? "Clause"),
        why: f.explanation?.why || f.reason,
        action:
          f.explanation?.attention
          || (f.requires_attention ? "Requires human review." : "No action required."),
        clauseId: f.clause_id,
      };
    });

  return (
    <section className="card p-4 space-y-4" id="risk-assessment" aria-label="Risk assessment">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-serif text-xl font-bold">Risk assessment</h2>
        {activeLife && (
          <StatusBadge tone={statusToneFor(activeLife.overall)}>
            {analysisLabel(activeLife.overall)}
          </StatusBadge>
        )}
      </div>

      <p className="text-sm font-medium" role="status">
        {analysisHeadline(activeLife, rs)}
      </p>
      {analysisNotice(activeLife) && (
        <p className="text-xs text-amber-800 border border-amber-300 bg-amber-50 p-2 rounded">
          {analysisNotice(activeLife)}
        </p>
      )}

      {/* Coverage — moved here from the contract summary: this section owns
          how much of the contract was actually analyzed. */}
      <div className="border rounded p-3 bg-slate-50">
        <h3 className="font-semibold text-sm mb-1">Analysis coverage</h3>
        <ul className="text-sm space-y-0.5">
          <li><b>{rs.classified_clauses} / {rs.total_clauses}</b> clauses classified</li>
          <li><b>{rs.analyzed_clauses} / {rs.total_clauses}</b> clauses risk-evaluated</li>
          <li><b>{rs.needs_review}</b> clauses require review</li>
        </ul>
      </div>

      {/* Risk overview */}
      <div>
        <h3 className="font-semibold text-sm mb-1">Risk overview</h3>
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-7 gap-2">
          <Count tone="high" label="High" value={rs.high_risk} />
          <Count tone="medium" label="Medium" value={rs.medium_risk} />
          <Count tone="low" label="Low" value={rs.low_risk} />
          <Count tone="none" label="No risk" value={rs.no_detected_risk} />
          <Count tone="review" label="Review" value={rs.needs_review} />
          <Count tone="findings" label="Findings" value={rs.findings} />
          <Count tone="attention" label="Attention" value={rs.needs_attention} />
        </div>
        <p className="text-xs text-ink-soft mt-1">
          <b>{rs.needs_attention}</b> clauses need attention (high + medium priority, plus any
          clause that has not been analyzed).
        </p>
      </div>

      {/* Top attention items — highest-priority findings first. This lives
          here rather than in the contract summary: ranking, recommendations
          and "what to open first" are risk content. */}
      <div>
        <h3 className="font-semibold text-sm mb-1">Top attention items</h3>
        {topAttention.length === 0 ? (
          <p className="text-sm text-ink-soft">
            {rs.findings === 0 && activeLife?.overall === "complete"
              ? "No detected risk — no rule matched any clause."
              : "No findings recorded yet."}
          </p>
        ) : (
          <ol className="space-y-2" aria-label="Top attention items list">
            {topAttention.map((it, i) => (
              <li key={it.key} className="border rounded p-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`text-[11px] px-2 py-0.5 rounded font-semibold ${PRI_STYLE[it.priority] ?? PRI_STYLE.low}`}>
                    {(it.priority || "unknown").toUpperCase()} RISK
                  </span>
                  <b>
                    {i + 1}. {it.label}
                  </b>
                </div>
                <p className="mt-1 text-slate-700">
                  <b>Why:</b> {it.why}
                </p>
                <p className="text-slate-700">
                  <b>Action:</b> {it.action}
                </p>
                {it.clauseId && (
                  <button className="text-xs underline mt-1" onClick={() => onJump(it.clauseId!)}>
                    View clause
                  </button>
                )}
              </li>
            ))}
            {ra.findings.length > topAttention.length && (
              <li className="text-xs text-ink-mute">
                Showing the {topAttention.length} highest-priority findings of {ra.findings.length} —
                the full list is in the clause-by-clause assessment below.
              </li>
            )}
          </ol>
        )}
      </div>

      {/* Clause-by-clause */}
      <div>
        <h3 className="font-semibold text-sm mb-1">Clause-by-clause assessment</h3>
        <div className="flex flex-wrap gap-1.5 mb-3" role="group" aria-label="Filter clauses by risk state">
          {RISK_FILTERS.map((f) => {
            const active = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(f.key)}
                className={`text-xs px-2 py-1 rounded border ${
                  active ? "bg-slate-900 text-white border-slate-900" : "bg-white text-slate-700 hover:bg-slate-100"
                }`}
              >
                {f.label}
              </button>
            );
          })}
          <span className="text-xs text-ink-mute self-center ml-1">
            {rows.length} of {sorted.length} clauses
          </span>
        </div>

        {rows.length === 0 ? (
          <p className="text-sm text-ink-soft border rounded p-3">
            {sorted.length === 0
              ? "No clauses were extracted from this document."
              : "No clauses match this filter."}
          </p>
        ) : (
          <ol className="space-y-3" aria-label="Clause-by-clause assessment list">
            {rows.map((c, i) => {
              const findings = (byId[c.id] ?? []).sort(
                (a, b) => priorityRank(a.priority) - priorityRank(b.priority)
              );
              const top = findings[0];
              const att = clauseAttention(c, findings);
              const category = (c.classification?.category ?? top?.risk_category ?? "")
                .replace(/_/g, " ");
              const facts = factLines(
                top?.explanation?.evidence?.facts ?? top?.extracted_facts
              );
              const reasons = top?.priority_reasons ?? [];
              return (
                <li
                  key={c.id}
                  id={`risk-clause-${c.id}`}
                  className="border rounded p-3 space-y-2 bg-white"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-ink-mute">{i + 1}.</span>
                    <h4 className="font-semibold text-sm">{clauseLabel(c)}</h4>
                    <span className={`text-[11px] px-2 py-0.5 rounded font-semibold ${stateChip(c.risk_state)}`}>
                      {clauseStateLabel(c.risk_state).toUpperCase()}
                    </span>
                    {/* Exact engine priority is shown on each finding below
                        (it can be "critical" while the state says High risk). */}
                  </div>

                  <div className="text-xs text-ink-soft flex flex-wrap gap-x-4 gap-y-1">
                    <span>
                      <b>Location:</b> Page {c.page_start ?? "?"}
                      {c.page_end && c.page_end !== c.page_start ? `–${c.page_end}` : ""}
                    </span>
                    <span>
                      <b>Category:</b> {category || "not classified"}
                    </span>
                    <span className={att.required ? "text-red-700 font-semibold" : ""}>
                      <b>Attention:</b> {att.required ? "REQUIRED" : "Not required"}
                    </span>
                  </div>

                  <p className="text-sm">
                    <b>Why:</b> {clauseWhy(c, findings)}
                  </p>
                  <p className="text-xs text-slate-700">
                    <b>Action:</b> {att.text}
                  </p>

                  {findings.map((f) => (
                    <div key={f.id} className="border-l-4 border-slate-200 pl-2 text-sm space-y-1">
                      <p className="flex flex-wrap items-center gap-2">
                        <span className={`text-[11px] px-2 py-0.5 rounded font-semibold ${PRI_STYLE[f.priority] ?? PRI_STYLE.low}`}>
                          {f.priority}
                        </span>
                        <b>{f.title}</b>
                      </p>
                      {top && f.id !== top.id && (
                        <p className="text-slate-700">
                          <b>Why:</b> {f.reason}
                        </p>
                      )}
                      <p className="text-xs text-ink-soft">
                        <b>Rule:</b> {f.rule_id} v{f.rule_version} · {f.risk_category} ·{" "}
                        {f.finding_type.replace(/_/g, " ")}
                        {f.classification_confidence != null &&
                          ` · classification ${(f.classification_confidence * 100).toFixed(0)}%`}
                      </p>
                      {reasons.length > 0 && f.id === top?.id && (
                        <ul className="list-disc ml-4 text-xs text-ink-soft">
                          {reasons.map((r, idx) => (
                            <li key={idx}>{r}</li>
                          ))}
                        </ul>
                      )}
                      {f.explanation?.what && (
                        <p className="text-xs text-slate-700">
                          <b>Explanation:</b> {f.explanation.what}
                        </p>
                      )}
                    </div>
                  ))}

                  {/* Evidence always appears for a clause with a finding — the
                      engine either recorded facts or it did not, and an empty
                      block must be stated rather than silently skipped. */}
                  {findings.length > 0 && (
                    <div className="text-xs">
                      <b>Evidence / extracted facts:</b>
                      {facts.length > 0 ? (
                        <ul className="list-disc ml-4 text-ink-soft">
                          {facts.map((line, idx) => (
                            <li key={idx}>{line}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-ink-soft">
                          No structured facts were recorded for this rule — see the
                          original clause below.
                        </p>
                      )}
                    </div>
                  )}

                  <details className="text-xs">
                    <summary className="cursor-pointer underline text-ink-soft">
                      Original clause
                    </summary>
                    <pre className="whitespace-pre-wrap mt-1 p-2 bg-slate-50 border rounded text-slate-800">
                      {c.text}
                    </pre>
                  </details>

                  <button className="text-xs underline text-ink-soft" onClick={() => onJump(c.id)}>
                    View in document
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <p className="text-[11px] text-ink-mute">
        Potential risks flagged by the deterministic rule engine for review — not a legal
        conclusion. The final decision stays with the human reviewer.
      </p>
    </section>
  );
}

