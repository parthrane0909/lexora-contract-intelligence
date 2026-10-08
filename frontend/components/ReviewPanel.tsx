"use client";
import { useEffect, useState } from "react";
import { API_URL, authHeaders } from "@/lib/api";

type Review = {
  id: string; reviewer_email?: string | null; reviewer_role?: string | null;
  decision: string; comment?: string | null; correction_type?: string | null;
  corrected_category?: string | null; corrected_priority?: string | null;
  explanation_id?: string | null; explanation_version?: string | null;
  created_at: string;
};

type State = {
  status: string; decision?: string | null; reviewer_email?: string | null;
  created_at?: string | null;
};

const CORRECTIONS = [
  "WRONG_CATEGORY", "WRONG_PRIORITY", "FALSE_POSITIVE", "MISSING_CONTEXT",
  "POLICY_MISINTERPRETATION", "TEMPLATE_MISMATCH", "EXTRACTION_ERROR",
  "CLAUSE_CLASSIFICATION_ERROR", "EXPLANATION_PROBLEM", "OTHER",
];
const PRIORITIES = ["critical", "high", "medium", "low"];

const DEC_STYLE: Record<string, string> = {
  accept: "bg-green-100 text-green-800",
  reject: "bg-red-100 text-red-800",
  modify: "bg-amber-100 text-amber-800",
  needs_more_review: "bg-slate-200 text-slate-700",
};

export default function ReviewPanel({ contractId, findingId }: { contractId: string; findingId: string }) {
  const [state, setState] = useState<State | null>(null);
  const [history, setHistory] = useState<Review[]>([]);
  const [decision, setDecision] = useState("accept");
  const [comment, setComment] = useState("");
  const [correction, setCorrection] = useState("OTHER");
  const [corrPriority, setCorrPriority] = useState("");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(false);

  const base = `${API_URL}/api/v1/contracts/${contractId}/findings/${findingId}`;

  async function load() {
    const h = authHeaders();
    const [sRes, hRes] = await Promise.all([
      fetch(`${base}/review`, { headers: h }),
      fetch(`${base}/reviews`, { headers: h }),
    ]);
    if (sRes.ok) {
      const j = await sRes.json();
      setState(j.review);
    }
    if (hRes.ok) setHistory(await hRes.json());
  }
  useEffect(() => { load(); }, [contractId, findingId]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    const body: Record<string, string> = { decision };
    if (comment.trim()) body.comment = comment.trim();
    if (decision === "modify") {
      body.correction_type = correction;
      if (corrPriority) body.corrected_priority = corrPriority;
    }
    const res = await fetch(`${base}/reviews`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return setMsg(`Failed: ${await res.text()}`);
    setComment("");
    setCorrPriority("");
    setOpen(false);
    load();
  }

  return (
    <div className="mt-2 border-t pt-2">
      <p className="text-[10px] font-semibold text-ink-mute">HUMAN REVIEW (reviewer is final)</p>
      {state && (
        <p className="mt-1">
          Status: <b>{state.status.replace("_", " ")}</b>
          {state.decision && (
            <span className={`inline-block text-[11px] px-2 py-0.5 rounded ml-2 ${DEC_STYLE[state.decision] ?? ""}`}>
              {state.decision.replace(/_/g, " ")}
            </span>
          )}
          {state.reviewer_email && <span className="text-ink-mute"> · {state.reviewer_email}</span>}
        </p>
      )}
      {!open ? (
        <button className="text-xs underline mt-1" onClick={() => setOpen(true)}>Submit review</button>
      ) : (
        <form onSubmit={submit} className="mt-1 space-y-2">
          <div className="flex gap-1 flex-wrap">
            {(["accept", "reject", "modify", "needs_more_review"] as const).map((d) => (
              <button key={d} type="button" onClick={() => setDecision(d)}
                className={`text-xs px-2 py-1 rounded border ${decision === d ? "bg-slate-900 text-white" : ""}`}>
                {d.replace(/_/g, " ")}
              </button>
            ))}
          </div>
          <textarea className="w-full border rounded p-2" rows={2}
            placeholder={decision === "accept" ? "Comment (optional)" : "Comment (required)"}
            value={comment} onChange={(e) => setComment(e.target.value)} />
          {decision === "modify" && (
            <div className="flex gap-2">
              <select className="border rounded p-1" value={correction} onChange={(e) => setCorrection(e.target.value)}>
                {CORRECTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <select className="border rounded p-1" value={corrPriority} onChange={(e) => setCorrPriority(e.target.value)}>
                <option value="">priority unchanged</option>
                {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          )}
          <div className="flex gap-2">
            <button className="text-xs px-3 py-1 bg-slate-900 text-white rounded">Submit</button>
            <button type="button" className="text-xs underline" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
      {msg && <p className="text-xs mt-1 text-ink-soft">{msg}</p>}
      {history.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] font-semibold text-ink-mute">REVIEW HISTORY (append-only)</p>
          {history.map((r) => (
            <div key={r.id} className="mt-1 border rounded p-2 bg-slate-50">
              <p>
                <span className={`inline-block text-[11px] px-2 py-0.5 rounded mr-1 ${DEC_STYLE[r.decision] ?? ""}`}>
                  {r.decision.replace(/_/g, " ")}
                </span>
                <span className="text-ink-mute">{r.reviewer_email ?? "?"} · {new Date(r.created_at).toLocaleString()}</span>
              </p>
              {r.correction_type && (
                <p className="text-ink-soft">Correction: {r.correction_type}
                  {r.corrected_priority ? ` → ${r.corrected_priority}` : ""}
                  {r.corrected_category ? ` → ${r.corrected_category}` : ""}</p>
              )}
              {r.explanation_version && (
                <p className="text-ink-mute">Reviewed against explanation {r.explanation_version}</p>
              )}
              {r.comment && <p className="mt-1 whitespace-pre-wrap">{r.comment}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
