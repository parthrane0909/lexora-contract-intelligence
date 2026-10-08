"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useEffect, useState } from "react";
import { Breadcrumbs } from "@/components/ui";
import Link from "next/link";
import { API_URL, authHeaders } from "@/lib/api";

type Dataset = {
  id: string; version: string; source_count: number; usable_count: number;
  excluded_count: number; manifest: any; stats?: any;
};
type Model = {
  id: string; name: string; version: string; status: string;
  dataset_version?: string | null; base_model?: string | null;
  metrics: any; artifact_location?: string | null;
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border rounded p-3 text-sm">
      <p className="font-semibold mb-2">{title}</p>
      {children}
    </div>
  );
}

function Kv({ data }: { data: Record<string, any> }) {
  return (
    <ul>{Object.entries(data).map(([k, v]) => (
      <li key={k}>{k}: <b>{typeof v === "number" ? v.toFixed(4) : String(v)}</b></li>
    ))}</ul>
  );
}

export default function MLEvaluationPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <MLEvaluationView />
    </RequireAuth>
  );
}

function MLEvaluationView() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [selModel, setSelModel] = useState<Model | null>(null);
  const [errors, setErrors] = useState<any>(null);
  const [calib, setCalib] = useState<any>(null);
  const [msgs, setMsgs] = useState("");
  const [buildVersion, setBuildVersion] = useState("");
  const [quality, setQuality] = useState<any>(null);

  const [matrices, setMatrices] = useState<any[]>([]);
  const [selMatrix, setSelMatrix] = useState<any>(null);
  const [card, setCard] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [prot, setProt] = useState<any>(null);
  const [selection, setSelection] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [feedback, setFeedback] = useState<any>(null);

  const h = () => authHeaders();
  async function load() {
    const [d, m, mx, sel, hist, fb] = await Promise.all([
      fetch(`${API_URL}/api/v1/ml-evaluation/datasets`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/classification/experiments/matrix`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/classification/selection`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/promotions/history`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/feedback/audit`, { headers: h() }),
    ]);
    if (d.ok) setDatasets(await d.json());
    if (m.ok) setModels(await m.json());
    if (mx.ok) setMatrices(await mx.json());
    if (sel.ok) setSelection(await sel.json());
    if (hist.ok) setHistory(await hist.json());
    if (fb.ok) setFeedback(await fb.json());
  }
  useEffect(() => { load(); }, []);

  async function buildDataset(e: React.FormEvent) {
    e.preventDefault();
    setMsgs("");
    const r = await fetch(`${API_URL}/api/v1/ml-evaluation/datasets/build`, {
      method: "POST", headers: { ...h(), "Content-Type": "application/json" },
      body: JSON.stringify({ version: buildVersion, seed: 42 }),
    });
    setMsgs(r.ok ? `Dataset ${buildVersion} built.` : `Failed: ${await r.text()}`);
    load();
  }

  async function inspect(mid: string) {
    const [m, e, c, mc, st, pr] = await Promise.all([
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/errors`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/calibration`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/model-card`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/statistics`, { headers: h() }),
      fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/protected`, { headers: h() }),
    ]);
    if (m.ok) setSelModel(await m.json());
    if (e.ok) setErrors(await e.json());
    if (c.ok) setCalib(await c.json());
    if (mc.ok) setCard(await mc.json());
    if (st.ok) setStats(await st.json()); else setStats(null);
    if (pr.ok) setProt(await pr.json()); else setProt(null);
  }

  async function showMatrix(matrix_id: string) {
    const r = await fetch(
      `${API_URL}/api/v1/ml-evaluation/classification/experiments/matrix/${matrix_id}`,
      { headers: h() });
    if (r.ok) setSelMatrix(await r.json());
  }

  async function rollback(mid: string) {
    if (!confirm("Roll back production to this model? This changes live inference.")) return;
    const r = await fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/rollback`, {
      method: "POST", headers: { ...h(), "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "rollback from UI" }),
    });
    const j = await r.json();
    setMsgs(r.ok ? `Rolled back to ${j.restored_version}.` : `Failed: ${JSON.stringify(j)}`);
    load();
  }

  async function showQuality(version: string) {
    const r = await fetch(`${API_URL}/api/v1/ml-evaluation/datasets/${version}/quality`,
      { headers: h() });
    if (r.ok) setQuality(await r.json());
  }

  async function promote(mid: string) {
    if (!confirm("Promote this candidate to production? This changes live inference.")) return;
    const r = await fetch(`${API_URL}/api/v1/ml-evaluation/models/${mid}/promote`, {
      method: "POST", headers: { ...h(), "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "promoted from UI" }),
    });
    const j = await r.json();
    setMsgs(r.ok
      ? (j.promoted ? `Promoted. Previous: ${j.previous_production ?? "none"}.`
        : `Rejected by gates: ${j.gates.gates.filter((g: any) => !g.passed).map((g: any) => g.name).join(", ")}`)
      : `Failed: ${JSON.stringify(j)}`);
    load();
  }

  const prod = models.find((m) => m.status === "production");
  const cands = models.filter((m) => m.status !== "production");

  return (
    <main className="space-y-4">
      <Breadcrumbs items={[{ label: "ML Evaluation" }]} />
      <h1 className="font-serif text-3xl font-bold tracking-tight text-ink">ML Evaluation</h1>
      <p className="text-xs text-ink-mute">
        Engineering metrics only — not legal accuracy. Human feedback never
        auto-retrains or replaces the production model; promotion is explicit.
      </p>
      {msgs && <p className="text-sm text-slate-700">{msgs}</p>}

      <Section title="Model overview">
        {prod ? (
          <p>Production: <b>{prod.name}/{prod.version}</b> · dataset {prod.dataset_version ?? "?"} ·{" "}
            macro F1 {(prod.metrics?.summary?.macro?.f1 ?? NaN).toFixed?.(4) ?? "?"} ·{" "}
            accuracy {(prod.metrics?.summary?.accuracy ?? NaN).toFixed?.(4) ?? "?"}</p>
        ) : <p className="text-ink-mute">No production model registered.</p>}
        {cands.length > 0 && (
          <table className="w-full text-xs border mt-2">
            <thead><tr className="bg-slate-100">
              <th className="p-2 text-left">Candidate</th><th className="p-2">Status</th>
              <th className="p-2">Macro F1</th><th className="p-2">Actions</th>
            </tr></thead>
            <tbody>{cands.map((m) => (
              <tr key={m.id} className="border-t">
                <td className="p-2">{m.name}/{m.version}</td>
                <td className="p-2 text-center">{m.status}</td>
                <td className="p-2 text-center">{m.metrics?.summary?.macro?.f1?.toFixed?.(4) ?? "–"}</td>
                <td className="p-2 text-center">
                  <button className="underline mr-2" onClick={() => inspect(m.id)}>Inspect</button>
                  <button className="underline mr-2" onClick={() => promote(m.id)}>Promote</button>
                  <button className="underline" onClick={() => rollback(m.id)}>Rollback</button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Section>

      {selModel && (
        <Section title={`Model comparison: ${selModel.name}/${selModel.version} vs production`}>
          <p className="text-xs">Status: {selModel.status} · dataset: {selModel.dataset_version ?? "?"}</p>
          {prod && selModel.version !== prod.version && (
            <p className="text-xs">Production macro F1: {prod.metrics?.summary?.macro?.f1?.toFixed?.(4) ?? "–"} ·{" "}
              candidate: {selModel.metrics?.summary?.macro?.f1?.toFixed?.(4) ?? "–"}</p>
          )}
          {errors && (
            <>
              <p className="font-semibold text-xs mt-2">Weakest classes (false negatives)</p>
              <ul className="text-xs">{Object.entries(errors.errors?.per_class ?? {})
                .map(([k, v]: any) => ({ k, fn: v.false_negatives }))
                .sort((a: any, b: any) => b.fn - a.fn).slice(0, 5)
                .map((r: any) => <li key={r.k}>{r.k}: {r.fn} misses</li>)}</ul>
              <p className="font-semibold text-xs mt-2">Top misclassification pairs</p>
              <ul className="text-xs">{(errors.errors?.misclassification_pairs ?? []).slice(0, 5)
                .map((p: any, i: number) => (
                  <li key={i}>{p.actual} → {p.predicted}: {p.count}</li>))}</ul>
            </>
          )}
          {calib && (
            <p className="text-xs mt-2">ECE: <b>{calib.calibration?.ece ?? "–"}</b> ·
              mean confidence correct/incorrect:{" "}
              {calib.calibration?.stats?.mean_correct?.toFixed?.(3) ?? "–"} /{" "}
              {calib.calibration?.stats?.mean_incorrect?.toFixed?.(3) ?? "–"}</p>
          )}
          {stats && (
            <>
              <p className="font-semibold text-xs mt-2">
                Multi-seed aggregate ({stats.aggregate?.n_runs ?? "?"} runs)
              </p>
              <p className="text-xs">Macro F1 mean{" "}
                {stats.aggregate?.macro?.mean?.toFixed?.(4) ?? "–"} ±{" "}
                {stats.aggregate?.macro?.std?.toFixed?.(4) ?? "–"} ·
                min/max {stats.aggregate?.macro?.min?.toFixed?.(4) ?? "–"}/
                {stats.aggregate?.macro?.max?.toFixed?.(4) ?? "–"}</p>
            </>
          )}
          {prot && (
            <>
              <p className="font-semibold text-xs mt-2">
                Protected classes vs production ({prot.production_version ?? "no production"})
              </p>
              <ul className="text-xs">{Object.entries(prot.protected ?? {}).map(([k, v]: any) => (
                <li key={k}>{k}: cand {v.candidate_f1?.toFixed?.(4) ?? "–"} ·
                  prod {v.production_f1?.toFixed?.(4) ?? "–"} ·
                  Δ {v.delta?.toFixed?.(4) ?? "–"}</li>))}</ul>
            </>
          )}
          {errors?.truncation && (
            <p className="text-xs mt-2">Truncation:{" "}
              {JSON.stringify(errors.truncation?.overall ?? errors.truncation).slice(0, 200)}</p>
          )}
          {card && (
            <>
              <p className="font-semibold text-xs mt-2">Model card ({card.model_card_version ?? "?"})</p>
              <p className="text-xs">Task: {card.model?.task} · arch: {card.model?.architecture} ·
                max_len: {card.training?.seq_len ?? card.model?.seq_len ?? "–"}</p>
              <p className="text-xs">Frozen test:{" "}
                {card.frozen_test && Object.keys(card.frozen_test).length > 0
                  ? `acc ${card.frozen_test?.accuracy} · macro F1 ${card.frozen_test?.macro_f1}`
                  : "not recorded"}</p>
              <p className="text-xs">Weaknesses: {(card.known_weaknesses ?? []).join("; ")}</p>
              <p className="text-xs text-ink-mute">{card.legal_disclaimer}</p>
            </>
          )}
        </Section>
      )}

      <Section title="Experiment matrix (multi-seed, validation evidence)">
        {matrices.length === 0 && <p className="text-ink-mute text-xs">No matrices yet.</p>}
        {matrices.map((mx: any) => (
          <p key={mx.matrix_id} className="text-xs">
            <button className="underline" onClick={() => showMatrix(mx.matrix_id)}>
              {mx.matrix_id}
            </button> · {mx.n_runs} runs
          </p>
        ))}
        {selMatrix && (
          <table className="w-full text-xs border mt-2">
            <thead><tr className="bg-slate-100">
              <th className="p-2 text-left">Experiment</th><th className="p-2">Seed</th>
              <th className="p-2">Status</th><th className="p-2">Val macro F1</th>
            </tr></thead>
            <tbody>{selMatrix.experiments.map((e: any) => (
              <tr key={e.id} className="border-t">
                <td className="p-2">{e.name}</td>
                <td className="p-2 text-center">{e.seed}</td>
                <td className="p-2 text-center">{e.status}</td>
                <td className="p-2 text-center">{e.metrics?.macro?.f1?.toFixed?.(4) ?? "–"}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Section>

      <Section title="Final selection (validation evidence only)">
        {!selection && <p className="text-ink-mute text-xs">No validation candidates yet.</p>}
        {selection && (
          <>
            <p className="text-xs">Selected: <b>{selection.selected_version ?? "none"}</b></p>
            <ul className="text-xs">{(selection.rationale ?? []).map((r: string, i: number) => (
              <li key={i}>• {r}</li>))}</ul>
            {(selection.rejected ?? []).length > 0 && (
              <p className="text-xs text-ink-mute">
                Rejected: {(selection.rejected ?? []).map((r: any) => r.version).join(", ")}
              </p>)}
            <p className="text-xs text-ink-mute">{selection.warning}</p>
          </>
        )}
      </Section>

      <Section title="Human feedback audit (supervised eligibility)">
        {!feedback && <p className="text-ink-mute text-xs">No feedback data yet.</p>}
        {feedback && (
          <>
            <p className="text-xs">Reviewed: <b>{feedback.audit?.total ?? 0}</b> ·
              supervised-eligible: <b>{feedback.audit?.buckets?.SUPERVISED_ELIGIBLE?.count ?? 0}</b> ·
              eval-only: {feedback.audit?.buckets?.EVALUATION_ONLY?.count ?? 0} ·
              excluded: {(feedback.audit?.buckets?.EXCLUDED_AMBIGUOUS?.count ?? 0) +
                (feedback.audit?.buckets?.EXCLUDED_INVALID?.count ?? 0) +
                (feedback.audit?.buckets?.EXCLUDED_NON_CORRECTION?.count ?? 0)}</p>
            <p className="text-xs">Evidence verdict: <b>{feedback.verdict?.verdict ?? "–"}</b></p>
            <p className="text-xs text-ink-mute">
              Only modify-with-valid-correction rows become supervised labels. Human
              feedback is used only when it passes the supervised eligibility rules.
            </p>
          </>
        )}
      </Section>

      <Section title="Promotion history & rollback">
        {history.length === 0 && <p className="text-ink-mute text-xs">No promotion events yet.</p>}
        {history.slice(0, 10).map((e: any, i: number) => (
          <p key={i} className="text-xs">{e.model_version}: {e.from_status} → {e.to_status} ·
            {e.reason ?? ""}</p>
        ))}
      </Section>

      <Section title="Datasets">
        <form onSubmit={buildDataset} className="flex gap-2 mb-2">
          <input className="border p-2 rounded text-sm" placeholder="new version, e.g. v3"
            value={buildVersion} onChange={(e) => setBuildVersion(e.target.value)} />
          <button className="px-3 py-2 bg-slate-900 text-white rounded text-sm">Build (admin)</button>
        </form>
        <table className="w-full text-xs border">
          <thead><tr className="bg-slate-100">
            <th className="p-2 text-left">Version</th><th className="p-2">Source</th>
            <th className="p-2">Usable</th><th className="p-2">Excluded</th><th className="p-2">Detail</th>
          </tr></thead>
          <tbody>{datasets.map((d) => (
            <tr key={d.id} className="border-t">
              <td className="p-2">{d.version}</td>
              <td className="p-2 text-center">{d.source_count}</td>
              <td className="p-2 text-center">{d.usable_count}</td>
              <td className="p-2 text-center">{d.excluded_count}</td>
              <td className="p-2 text-center">
                <button className="underline" onClick={() => showQuality(d.version)}>Quality</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
        {quality && (
          <div className="text-xs mt-2">
            <p className="font-semibold">Quality — {quality.version}</p>
            <p>Usable supervised: {quality.quality?.n_usable_supervised} ·
              duplicates: {(quality.quality?.duplicate_text ?? []).length} ·
              empty: {(quality.quality?.empty_text ?? []).length} ·
              contradictory: {(quality.quality?.contradictory_labels ?? []).length}</p>
            <p className="text-ink-mute">Class distribution:</p>
            <Kv data={quality.quality?.class_distribution_usable ?? {}} />
          </div>
        )}
      </Section>
    </main>
  );
}
