"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { API_URL, authHeaders } from "@/lib/api";

type TplClause = {
  id: string; section_number?: string | null; title?: string | null; text: string;
  page_start?: number | null; predicted_category?: string | null;
};
type Contract = { id: string; filename: string };
type Finding = {
  id: string; template_clause_id?: string | null; contract_clause_id?: string | null;
  category?: string | null; difference_type: string; materiality: string; title: string;
  template_text?: string | null; contract_text?: string | null;
  value_diff: { field: string; template: unknown; contract: unknown }[]; reasons: string[];
};

const MAT_STYLE: Record<string, string> = {
  material: "border-red-400 bg-red-50",
  potentially_material: "border-amber-400 bg-amber-50",
  informational: "border-slate-300 bg-slate-50",
};

export default function TemplateDetail() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <TemplateDetailView />
    </RequireAuth>
  );
}

function TemplateDetailView() {
  const params = useParams();
  const id = params.id as string;
  const [clauses, setClauses] = useState<TplClause[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [contractId, setContractId] = useState("");
  const [findings, setFindings] = useState<Finding[]>([]);
  const [compId, setCompId] = useState("");
  const [msg, setMsg] = useState("");
  const [filter, setFilter] = useState("");

  async function load() {
    const h = authHeaders();
    const [cRes, tRes] = await Promise.all([
      fetch(`${API_URL}/api/v1/contracts`, { headers: h }),
      fetch(`${API_URL}/api/v1/templates/${id}/clauses`, { headers: h }),
    ]);
    if (cRes.ok) setContracts(await cRes.json());
    if (tRes.ok) setClauses(await tRes.json());
    else setMsg("Template not found or not authorized.");
  }
  useEffect(() => { load(); }, [id]);

  async function runCompare() {
    if (!contractId) return setMsg("Pick a contract first.");
    setMsg("Comparing…");
    const res = await fetch(`${API_URL}/api/v1/contracts/${contractId}/compare`, {
      method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: id }),
    });
    if (!res.ok) return setMsg(`Failed: ${await res.text()}`);
    const comp = await res.json();
    setCompId(comp.id);
    const fRes = await fetch(`${API_URL}/api/v1/comparisons/${comp.id}/findings`, { headers: authHeaders() });
    if (fRes.ok) setFindings(await fRes.json());
    const stats = comp.stats ?? {};
    setMsg(`Compared: ${stats.modified ?? 0} modified, ${stats.added ?? 0} added, ${stats.missing ?? 0} missing, ${stats.unchanged ?? 0} unchanged.`);
  }

  const shown = filter ? findings.filter((f) => f.materiality === filter) : findings;

  return (
    <main className="space-y-4">
      <Link className="underline text-sm" href="/templates">← Templates</Link>
      <h1 className="text-2xl font-bold">Template clauses ({clauses.length})</h1>
      <div className="flex gap-2 items-end flex-wrap">
        <label className="text-sm">Incoming contract
          <select className="block border p-2 rounded mt-1 w-72" value={contractId} onChange={(e) => setContractId(e.target.value)}>
            <option value="">— select —</option>
            {contracts.map((c) => <option key={c.id} value={c.id}>{c.filename}</option>)}
          </select>
        </label>
        <button className="px-4 py-2 bg-slate-900 text-white rounded text-sm" onClick={runCompare}>Compare</button>
        <label className="text-sm">Materiality
          <select className="block border p-2 rounded mt-1" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">all</option>
            <option value="material">material</option>
            <option value="potentially_material">potentially material</option>
            <option value="informational">informational</option>
          </select>
        </label>
      </div>
      {msg && <p className="text-sm text-slate-700">{msg}</p>}

      {shown.map((f) => (
        <div key={f.id} className={`border rounded p-3 ${MAT_STYLE[f.materiality]}`}>
          <p className="text-sm">
            <b>{f.materiality.replace("_", " ").toUpperCase()}</b> · {f.difference_type} · {f.category ?? "uncategorized"}
          </p>
          <p className="font-semibold mt-1">{f.title}</p>
          <div className="grid md:grid-cols-2 gap-3 mt-2 text-sm">
            <div>
              <p className="text-xs font-semibold text-ink-mute">APPROVED TEMPLATE</p>
              <p className="whitespace-pre-wrap">{f.template_text ?? <i className="text-ink-mute">— absent —</i>}</p>
            </div>
            <div>
              <p className="text-xs font-semibold text-ink-mute">INCOMING CONTRACT</p>
              {f.contract_clause_id ? (
                <Link className="underline whitespace-pre-wrap block"
                  href={`/contracts/${contractId}#clause-${f.contract_clause_id}`}>
                  {f.contract_text}
                </Link>
              ) : (
                <p className="whitespace-pre-wrap">{f.contract_text ?? <i className="text-ink-mute">— absent —</i>}</p>
              )}
            </div>
          </div>
          {f.value_diff.length > 0 && (
            <table className="text-xs mt-2 border bg-white">
              <thead><tr className="bg-slate-100"><th className="p-1">Field</th><th className="p-1">Template</th><th className="p-1">Contract</th></tr></thead>
              <tbody>{f.value_diff.map((v, i) => (
                <tr key={i} className="border-t"><td className="p-1">{v.field}</td>
                  <td className="p-1">{String(v.template)}</td><td className="p-1 font-semibold">{String(v.contract)}</td></tr>
              ))}</tbody>
            </table>
          )}
          {f.reasons.length > 0 && <p className="text-xs mt-1 text-ink-soft">{f.reasons.join(" · ")}</p>}
        </div>
      ))}

      {!compId && (
        <>
          <h2 className="font-semibold">Approved clauses</h2>
          {clauses.map((c) => (
            <div key={c.id} className="border rounded p-2 text-sm">
              <p className="font-semibold">{c.section_number ? `§${c.section_number} · ` : ""}{c.title}
                <span className="font-normal text-ink-mute"> (p.{c.page_start ?? "?"}{c.predicted_category ? ` · ${c.predicted_category}` : ""})</span></p>
              <p className="whitespace-pre-wrap mt-1">{c.text}</p>
            </div>
          ))}
        </>
      )}
    </main>
  );
}
