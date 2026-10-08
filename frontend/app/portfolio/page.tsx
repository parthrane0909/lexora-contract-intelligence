"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useEffect, useState } from "react";
import { Breadcrumbs, ErrorState } from "@/components/ui";
import Link from "next/link";
import { API_URL, authHeaders } from "@/lib/api";

type Attention = {
  contract_id: string; contract_name: string; contract_type?: string | null;
  counterparty?: string | null; status: string; attention_score: number;
  score_breakdown: Record<string, number>; scoring_version: string;
  high_risk_count: number; critical_risk_count: number; unreviewed_count: number;
  overdue_obligation_count: number; upcoming_event_count: number;
  renewal_date?: string | null; policy_deviation_count: number;
};

/** One term of the backend's portfolio-health formula (rendered, never
 *  recomputed — the backend is authoritative for every number shown). */
type HealthComponent = {
  key: string; label: string; weight: number; rate: number; points: number;
  numerator: number; denominator: number; basis: string;
};

type Health = {
  scoring_version: string;
  computed_on: string;
  formula: string;
  portfolio_health: {
    score: number | null;
    raw_score: number | null;
    label: string;
    orientation: string;
    analysis_coverage: number | null;
    explanation: string;
  };
  drivers: Record<string, number>;
  coverage: {
    analysis_coverage: number | null;
    analyzed_contracts: number;
    total_contracts: number;
    unanalyzed_contracts: number;
    stale_contracts: number;
    failed_contracts: number;
    by_status: Record<string, number>;
  };
  components: HealthComponent[];
  disclaimer: string;
};

/** Plain-language driver lines; only labels + backend counts, no scoring. */
function driverLines(d: Record<string, number>): string[] {
  const out: string[] = [];
  if (d.critical_findings) out.push(`${d.critical_findings} unresolved critical finding(s)`);
  if (d.high_findings) out.push(`${d.high_findings} unresolved high-priority finding(s)`);
  if (d.unreviewed_findings) out.push(`${d.unreviewed_findings} unreviewed finding(s)`);
  if (d.overdue_obligations) out.push(`${d.overdue_obligations} overdue obligation(s)`);
  const soon = (d.renewals_next_30_days ?? 0) + (d.notice_deadlines_next_30_days ?? 0);
  if (soon) out.push(`${soon} renewal / notice deadline(s) within 30 days`);
  if (d.contracts_with_policy_deviations) {
    out.push(`${d.contracts_with_policy_deviations} contract(s) with policy deviations`);
  }
  if (d.contracts_needing_analysis) {
    out.push(`${d.contracts_needing_analysis} contract(s) not yet analyzed`);
  }
  if (d.contracts_needing_review) {
    out.push(`${d.contracts_needing_review} contract(s) awaiting review`);
  }
  return out;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border rounded p-3 text-sm">
      <p className="font-semibold mb-2">{title}</p>
      {children}
    </div>
  );
}

function Kv({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  if (!entries.length) return <p className="text-ink-mute">None</p>;
  return (
    <ul>{entries.map(([k, v]) => <li key={k}>{k}: <b>{v}</b></li>)}</ul>
  );
}

export default function PortfolioPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <PortfolioView />
    </RequireAuth>
  );
}

function PortfolioView() {
  const [summary, setSummary] = useState<any>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState("");
  const [queue, setQueue] = useState<Attention[]>([]);
  const [risks, setRisks] = useState<any>(null);
  const [oblig, setOblig] = useState<any>(null);
  const [renew, setRenew] = useState<any>(null);
  const [dev, setDev] = useState<any>(null);
  const [cp, setCp] = useState<any>(null);
  const [trends, setTrends] = useState<any>(null);
  const [fb, setFb] = useState<any>(null);
  const [notif, setNotif] = useState<any>(null);
  const [comp, setComp] = useState<any>(null);
  const [err, setErr] = useState("");
  // Bumped by "Retry" so a failed section can be re-fetched without a reload.
  const [reload, setReload] = useState(0);

  // Portfolio health gets its own loader/error so a failure never hides the
  // rest of the page, and never falls back to a locally computed number.
  const loadHealth = () => {
    setHealthErr("");
    fetch(`${API_URL}/api/v1/portfolio/health`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: Health) => setHealth(j))
      .catch(() => setHealthErr("Portfolio health is unavailable."));
  };

  useEffect(() => {
    const h = authHeaders();
    const get = (p: string, set: (j: any) => void) =>
      fetch(`${API_URL}${p}`, { headers: h })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => { if (j) set(j); })
        .catch(() => setErr("Load failed (login first?)"));
    loadHealth();
    get("/api/v1/portfolio/summary", setSummary);
    get("/api/v1/portfolio/attention", setQueue);
    get("/api/v1/portfolio/risks", setRisks);
    get("/api/v1/portfolio/obligations", setOblig);
    get("/api/v1/portfolio/renewals?window_days=90", setRenew);
    get("/api/v1/portfolio/policy-deviations", setDev);
    get("/api/v1/portfolio/counterparties", setCp);
    get("/api/v1/portfolio/trends?granularity=week", setTrends);
    get("/api/v1/portfolio/review-feedback", setFb);
    get("/api/v1/portfolio/notifications", setNotif);
    get("/api/v1/portfolio/comparisons", setComp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  const ph = health?.portfolio_health;
  const coverage = health?.coverage;
  const scoreText = ph && ph.score !== null ? `${ph.score}` : "–";
  const coverageText =
    coverage && coverage.analysis_coverage !== null
      ? `${coverage.analysis_coverage}%`
      : "–";

  return (
    <main className="space-y-4">
      <Breadcrumbs items={[{ label: "Portfolio" }]} />
      <h1 className="font-serif text-3xl font-bold tracking-tight text-ink">Portfolio Intelligence</h1>
      <p className="text-xs text-ink-mute">
        Operational prioritization metrics aggregated from stored analysis — not legal-risk probabilities or legal advice.
      </p>
      {err && (
        <ErrorState
          message={err}
          onRetry={() => { setErr(""); setReload((n) => n + 1); }}
        />
      )}

      {/* ---------------- Portfolio intelligence summary ---------------- */}
      <section className="card p-4" aria-label="Portfolio intelligence summary">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="font-serif font-semibold text-lg">Portfolio intelligence</h2>
          <p className="text-xs text-ink-mute">
            {health
              ? `Backend score v${health.scoring_version} · computed ${health.computed_on}`
              : "Backend score"}
          </p>
        </div>

        {healthErr ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <p className="text-amber-700">{healthErr}</p>
            <button className="btn btn-secondary btn-sm" onClick={loadHealth}>Retry</button>
          </div>
        ) : !health || !ph || !coverage ? (
          <p className="text-sm text-ink-mute">Loading portfolio health…</p>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-3">
              {/* one overall score, backend-calculated */}
              <div className="rounded-lg border border-slate-200 p-4 text-center">
                <p className="text-xs uppercase tracking-wide text-ink-mute">
                  Portfolio Health
                </p>
                <p className="font-serif text-4xl font-bold mt-1" data-testid="portfolio-health-score">
                  {scoreText}
                  <span className="text-lg text-ink-mute font-normal"> / 100</span>
                </p>
                <p className="text-sm font-medium mt-1">{ph.label}</p>
                <p className="text-[11px] text-ink-mute mt-1">
                  100 = healthiest · higher is healthier
                </p>
              </div>

              {/* coverage is reported separately, never folded into "fine" */}
              <div className="rounded-lg border border-slate-200 p-4 text-center">
                <p className="text-xs uppercase tracking-wide text-ink-mute">
                  Analysis Coverage
                </p>
                <p className="font-serif text-4xl font-bold mt-1" data-testid="portfolio-coverage">
                  {coverageText}
                </p>
                <p className="text-sm mt-1">
                  {coverage.analyzed_contracts} of {coverage.total_contracts} contract(s) analyzed
                </p>
                <div
                  className="h-2 rounded-full bg-slate-200 mt-2 overflow-hidden"
                  role="progressbar"
                  aria-label="Analysis coverage"
                  aria-valuenow={coverage.analysis_coverage ?? 0}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className="h-2"
                    style={{
                      width: `${coverage.analysis_coverage ?? 0}%`,
                      background: "var(--brand-800)",
                    }}
                  />
                </div>
                {coverage.unanalyzed_contracts > 0 && (
                  <p className="text-[11px] text-amber-700 mt-2">
                    {coverage.unanalyzed_contracts} contract(s) have no complete
                    analysis — they are <b>not</b> counted as healthy.
                    {coverage.stale_contracts > 0 && ` ${coverage.stale_contracts} stale;`}
                    {coverage.failed_contracts > 0 && ` ${coverage.failed_contracts} failed;`}
                  </p>
                )}
              </div>

              {/* drivers straight from the backend payload */}
              <div className="rounded-lg border border-slate-200 p-4">
                <p className="text-xs uppercase tracking-wide text-ink-mute">
                  Main drivers
                </p>
                {(() => {
                  const lines = driverLines(health.drivers);
                  return lines.length ? (
                    <ul className="text-sm mt-2 space-y-1 list-disc pl-4">
                      {lines.slice(0, 6).map((l) => <li key={l}>{l}</li>)}
                    </ul>
                  ) : (
                    <p className="text-sm mt-2 text-ink-soft">
                      No unresolved findings, overdue obligations or near deadlines
                      in the analyzed contracts.
                    </p>
                  );
                })()}
                <p className="text-[11px] text-ink-mute mt-3">{health.disclaimer}</p>
              </div>
            </div>

            {/* plain-language explanation, detail only on demand */}
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer underline">
                How is this calculated?
              </summary>
              <div className="mt-2 space-y-2 text-slate-700">
                <p>{ph.explanation}</p>
                <ul className="list-disc pl-5 space-y-1">
                  <li>
                    <b>Higher is better.</b> 100 is the healthiest portfolio, 0 needs
                    the most attention. It is an operational prioritization metric —
                    not a legal-risk probability, not model confidence and not legal
                    advice.
                  </li>
                  <li>
                    <b>Normalization.</b> Every input is a rate (count ÷ a stated
                    denominator), capped at 100%, then weighted; the six weights add
                    up to 100%. Adding contracts therefore does not make a portfolio
                    look worse merely because it is bigger.
                  </li>
                  <li>
                    <b>Analysis coverage</b> = {coverage.analyzed_contracts} of{" "}
                    {coverage.total_contracts} contracts whose analysis completed.
                    The score shown is the analyzed health × coverage, so missing
                    analysis can only lower the score, never raise it.
                  </li>
                  <li>
                    <b>Per-contract Attention scores</b> (table below) are a separate,
                    additive weight used to order work per contract — they are not
                    this portfolio score and are never summed to produce it.
                  </li>
                </ul>
                <div className="overflow-x-auto"><table className="w-full text-xs border mt-1">
                  <thead>
                    <tr className="bg-slate-50 text-left">
                      <th className="p-2">Component</th>
                      <th className="p-2">Weight</th>
                      <th className="p-2">Rate</th>
                      <th className="p-2">Count</th>
                      <th className="p-2">Points off 100</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.components.map((c) => (
                      <tr key={c.key} className="border-t">
                        <td className="p-2">{c.label}</td>
                        <td className="p-2 text-center">
                          {(c.weight * 100).toFixed(0)}%
                        </td>
                        <td className="p-2 text-center">
                          {(c.rate * 100).toFixed(1)}%
                        </td>
                        <td className="p-2 text-center">
                          {c.numerator} / {c.denominator}
                        </td>
                        <td className="p-2 text-center">{c.points.toFixed(2)}</td>
                      </tr>
                    ))}
                    <tr className="border-t bg-slate-50">
                      <td className="p-2" colSpan={4}>
                        raw = 100 − Σ points, then × coverage
                      </td>
                      <td className="p-2 text-center font-semibold">
                        {ph.raw_score ?? "–"}
                      </td>
                    </tr>
                  </tbody>
                </table></div>
                <p className="text-xs text-ink-mute">{health.formula}</p>
              </div>
            </details>
          </>
        )}
      </section>

      {summary && (
        <Card title="Portfolio summary (counts)">
          <p>{summary.total_contracts} contracts ({summary.active_contracts} active) ·{" "}
            {summary.open_obligations} open obligations · {summary.overdue_obligations} overdue ·{" "}
            {summary.upcoming_renewals_30d} renewals ≤30d · {summary.contracts_requiring_review} need review ·{" "}
            {summary.unresolved_high_critical} unresolved high/critical</p>
          <p className="text-ink-soft">Findings by review state:{" "}
            {Object.entries(summary.findings_by_review_state as Record<string, number>)
              .map(([k, v]) => `${k} ${v}`).join(" · ") || "none"}</p>
        </Card>
      )}

      <Card title="Contract attention queue (per-contract operational score)">
        <p className="text-xs text-ink-soft mb-2">
          Each <b>Attention score</b> belongs to that one contract and orders work
          (risk + review + lifecycle + renewal + deviation). Higher = more attention
          needed. It is <b>not</b> the Portfolio Health score above, not a
          probability and not legal advice.
        </p>
        <div className="overflow-x-auto"><table className="w-full text-xs border">
          <thead><tr className="bg-slate-100">
            <th className="p-2 text-left">Contract</th><th className="p-2">Attention score</th>
            <th className="p-2">High/Crit</th><th className="p-2">Unreviewed</th>
            <th className="p-2">Overdue</th><th className="p-2">Renewal</th><th className="p-2">Deviation</th>
          </tr></thead>
          <tbody>
            {queue.map((r) => (
              <tr key={r.contract_id} className="border-t">
                <td className="p-2">
                  <Link className="inline-flex min-h-6 items-center py-1 underline" href={`/contracts/${r.contract_id}`}>{r.contract_name}</Link>
                  <span className="block text-ink-mute">
                    {r.counterparty ?? "Unspecified"}
                    {Object.entries(r.score_breakdown).map(([k, v]) => ` · ${k}+${v}`).join("")}
                  </span>
                </td>
                <td className="p-2 text-center font-bold">{r.attention_score}</td>
                <td className="p-2 text-center">{r.high_risk_count}/{r.critical_risk_count}</td>
                <td className="p-2 text-center">{r.unreviewed_count}</td>
                <td className="p-2 text-center">{r.overdue_obligation_count}</td>
                <td className="p-2 text-center">{r.renewal_date ?? "–"}</td>
                <td className="p-2 text-center">{r.policy_deviation_count}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>

      <div className="grid md:grid-cols-2 gap-4">
        <Card title="Risk distribution">
          {risks ? (<>
            <p className="text-xs text-ink-mute">Priority</p><Kv data={risks.by_priority} />
            <p className="text-xs text-ink-mute mt-2">Category</p><Kv data={risks.by_category} />
            <p className="text-xs text-ink-mute mt-2">Review state</p><Kv data={risks.by_review_state} />
            <p className="text-xs text-ink-mute">Total findings: {risks.total}</p>
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Obligations">
          {oblig ? (<>
            <p>Total: <b>{oblig.total}</b> · overdue: <b>{oblig.overdue_ids.length}</b></p>
            <p className="text-xs text-ink-mute">By status</p><Kv data={oblig.by_status} />
            <p className="text-xs text-ink-mute mt-2">By type</p><Kv data={oblig.by_type} />
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Renewals (90d)">
          {renew ? (<ul>{renew.renewals.map((r: any) => (
            <li key={r.contract_id}>
              <Link className="inline-flex min-h-6 items-center py-1 underline" href={`/contracts/${r.contract_id}`}>{r.contract_name}</Link>
              {" "}→ {r.renewal_date ?? "–"}{r.notice_deadline ? ` (notice ${r.notice_deadline})` : ""}
            </li>))}</ul>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Policy deviations">
          {dev ? (<>
            <p>Total: <b>{dev.total}</b></p>
            <p className="text-xs text-ink-mute">By category</p><Kv data={dev.by_category} />
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Counterparties">
          {cp ? (<>
            {!cp.counterparty_data_available && (
              <p className="text-xs text-ink-mute">No member-supplied counterparty data yet — set it on a contract to enable this view.</p>)}
            <ul>{cp.counterparties.map((c: any) => (
              <li key={c.counterparty}>{c.counterparty}: {c.contract_count} contracts,{" "}
                {c.high_risk_count} high-risk, {c.overdue_obligation_count} overdue,{" "}
                {c.upcoming_renewal_count} renewals</li>))}</ul>
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Review feedback">
          {fb ? (<>
            <p>Total: <b>{fb.total}</b> · reviewer-rejected: <b>{fb.reviewer_rejected}</b></p>
            <p className="text-xs text-ink-mute">Corrections</p><Kv data={fb.by_correction_type} />
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Template comparison">
          {comp ? (<>
            <p className="text-xs text-ink-mute">Latest comparison per contract</p>
            <Kv data={comp.materiality_totals} />
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
        <Card title="Notification health">
          {notif ? (<>
            <p>Upcoming events: <b>{notif.upcoming_events}</b> · overdue: <b>{notif.overdue_events}</b> ·{" "}
              completed instances: <b>{notif.completed_instances}</b> · failed deliveries:{" "}
              <b>{notif.failed_deliveries}</b></p>
            <p className="text-xs text-ink-mute">Notifications by status</p>
            <Kv data={notif.notifications_by_status} />
          </>) : <p className="text-ink-mute">Loading…</p>}
        </Card>
      </div>

      <Card title="Trends (weekly)">
        {trends ? (
          <div className="overflow-x-auto"><table className="w-full text-xs border">
            <thead><tr className="bg-slate-100">
              <th className="p-2 text-left">Week</th><th className="p-2">Findings</th>
              <th className="p-2">Deviations</th><th className="p-2">Reviews</th><th className="p-2">Obligation reviews</th>
            </tr></thead>
            <tbody>
              {trends.risk_findings.map((p: any) => {
                const at = (arr: any[], b: string) => arr.find((x: any) => x.bucket === b)?.count ?? 0;
                return (
                  <tr key={p.bucket} className="border-t">
                    <td className="p-2">{p.bucket}</td>
                    <td className="p-2 text-center">{p.count}</td>
                    <td className="p-2 text-center">{at(trends.policy_deviations, p.bucket)}</td>
                    <td className="p-2 text-center">{at(trends.reviews, p.bucket)}</td>
                    <td className="p-2 text-center">{at(trends.obligation_reviews, p.bucket)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        ) : <p className="text-ink-mute">Loading…</p>}
      </Card>
    </main>
  );
}
