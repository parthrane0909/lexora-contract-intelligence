"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { API_URL, ApiError, authHeaders, messageForStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/Toast";
import { Dialog } from "@/components/Dialog";
import {
  Breadcrumbs,
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
  TableSkeleton,
  TabBar,
  TabPanel,
} from "@/components/ui";

type Policy = {
  id: string;
  category: string;
  params: Record<string, unknown>;
  version: number;
  is_active: boolean;
  created_by?: string | null;
  created_at: string;
};

/** Editable fields per category (mirrors backend POLICY_SHAPES). */
const FIELDS: Record<
  string,
  { key: string; label: string; type: "number" | "text" | "bool" | "list" }[]
> = {
  liability: [
    { key: "max_amount", label: "Max liability amount", type: "number" },
    { key: "currency", label: "Currency", type: "text" },
  ],
  payment: [{ key: "max_days", label: "Max payment days", type: "number" }],
  termination: [{ key: "min_notice_days", label: "Min notice days", type: "number" }],
  renewal: [{ key: "allow_auto_renew", label: "Allow auto-renew", type: "bool" }],
  governing_law: [
    { key: "preferred_jurisdictions", label: "Preferred jurisdictions (comma-separated)", type: "list" },
  ],
  jurisdiction: [
    { key: "preferred_jurisdictions", label: "Preferred jurisdictions (comma-separated)", type: "list" },
  ],
  insurance: [
    { key: "min_coverage", label: "Min coverage amount", type: "number" },
    { key: "currency", label: "Currency", type: "text" },
  ],
};
const CATS = Object.keys(FIELDS);

type Tab = "positions" | "playbook" | "history";

export default function PoliciesPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <PoliciesView />
    </RequireAuth>
  );
}

/**
 * The single Policies destination (Phase 11).
 *
 * "Company Policies" used to be a second nav entry pointing at
 * `/policies?view=playbook` — a read-only rendering of the same active
 * positions. It is now a tab of this page, alongside the editable positions
 * and the version history, so one route carries the whole story.
 */
function PoliciesView() {
  const { user } = useAuth();
  const toast = useToast();
  const role = user?.role ?? "";
  const canWrite = role === "admin" || role === "legal_reviewer";

  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [history, setHistory] = useState<Policy[] | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [histCat, setHistCat] = useState<string>(CATS[0]);
  const [tab, setTab] = useState<Tab>("positions");
  const [cat, setCat] = useState<string>(CATS[0]);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [impact, setImpact] = useState("standard");
  const [saving, setSaving] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [deactivateTarget, setDeactivateTarget] = useState<string | null>(null);
  const [deactivating, setDeactivating] = useState(false);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("view") === "playbook") {
      setTab("playbook");
    }
  }, []);

  const load = useCallback(async () => {
    setLoadErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/policies`, { headers: authHeaders() });
      if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));
      setPolicies((await res.json()) as Policy[]);
    } catch (e) {
      setPolicies((p) => p ?? []);
      setLoadErr(e instanceof ApiError ? e.message : "Could not reach the server.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    fetch(`${API_URL}/api/v1/policies/history?category=${histCat}`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(messageForStatus(r.status)))))
      .then((rows) => {
        if (!cancelled) setHistory(rows as Policy[]);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [histCat]);

  function setVal(k: string, v: string) {
    setVals((s) => ({ ...s, [k]: v }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setFormErr("");
    const params: Record<string, unknown> = { impact };
    for (const f of FIELDS[cat]) {
      const raw = (vals[`${cat}.${f.key}`] ?? "").trim();
      if (!raw) continue;
      if (f.type === "number") {
        const n = Number(raw);
        if (!Number.isFinite(n)) return setFormErr(`${f.label} must be a number.`);
        params[f.key] = n;
      } else if (f.type === "bool") {
        params[f.key] = raw.toLowerCase() === "true";
      } else if (f.type === "list") {
        params[f.key] = raw
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      } else params[f.key] = raw;
    }
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/policies`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ category: cat, params }),
      });
      if (!res.ok) {
        const detail = (await res.text()).trim().slice(0, 300);
        throw new ApiError(res.status, detail || messageForStatus(res.status));
      }
      const created = (await res.json()) as Policy;
      toast.success(`Saved ${cat} v${created.version}`);
      setVals({});
      await load();
    } catch (err) {
      const m = err instanceof ApiError ? err.message : "Could not save the position.";
      setFormErr(m);
      toast.error(m);
    } finally {
      setSaving(false);
    }
  }

  async function restore(id: string) {
    try {
      const res = await fetch(`${API_URL}/api/v1/policies/${id}/restore`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));
      toast.success("Restored as a new version");
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not restore that version.");
    }
  }

  async function confirmDeactivate() {
    const category = deactivateTarget;
    if (!category) return;
    setDeactivating(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/policies/${category}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));
      toast.success(`Deactivated the ${category} position`);
      setDeactivateTarget(null);
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not deactivate that position.");
    } finally {
      setDeactivating(false);
    }
  }

  const activeByCat: Record<string, Policy> = {};
  (policies ?? []).forEach((p) => {
    activeByCat[p.category] = p;
  });
  const activeCount = Object.keys(activeByCat).length;

  return (
    <main className="space-y-6">
      <Breadcrumbs items={[{ label: "Policies" }]} />

      <PageHeader
        eyebrow="Negotiating positions"
        title="Policies"
        description={
          <>
            The positions your organization holds on each topic. The
            deterministic risk engine reads the active position for a
            contract&apos;s clause category while it analyses, and records a
            <em> policy deviation</em> finding when the contract departs from it.
          </>
        }
      />

      <div className="space-y-4">
        <TabBar<Tab>
          label="Policy views"
          value={tab}
          onChange={setTab}
          tabs={[
            { key: "positions", label: "Positions", count: activeCount },
            { key: "playbook", label: "Playbook" },
            { key: "history", label: "Version history" },
          ]}
        />

        {/* ---------------------------------------------------- positions */}
        <TabPanel tabKey="positions" active={tab === "positions"}>
          <div className="space-y-6">
            <section className="space-y-3" aria-labelledby="active-positions">
              <h2 id="active-positions" className="font-serif text-xl font-semibold text-ink">
                Active positions
              </h2>
              {loadErr ? (
                <ErrorState message={loadErr} onRetry={load} />
              ) : policies === null ? (
                <TableSkeleton rows={4} cols={5} />
              ) : activeCount === 0 ? (
                <EmptyState
                  title="No positions recorded yet"
                  hint={
                    canWrite
                      ? "Set one below — the next time a contract is analysed, the engine will compare it against this position."
                      : "Your organization has not recorded a negotiating position yet."
                  }
                />
              ) : (
                <div className="card overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="table">
                      <caption className="sr-only">
                        Active policy positions by category
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Category</th>
                          <th scope="col">Position</th>
                          <th scope="col">Version</th>
                          <th scope="col">Changed by / at</th>
                          {canWrite && <th scope="col">Action</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(activeByCat).map(([c, p]) => (
                          <tr key={c}>
                            <td className="font-semibold">{c.replace("_", " ")}</td>
                            <td className="max-w-[26rem]">{describe(p)}</td>
                            <td>v{p.version}</td>
                            <td className="text-xs text-ink-soft">
                              {p.created_by ?? "—"} · {formatWhen(p.created_at)}
                            </td>
                            {canWrite && (
                              <td>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => setDeactivateTarget(c)}
                                  aria-label={`Deactivate the ${c} position`}
                                >
                                  Deactivate
                                </Button>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </section>

            <section aria-labelledby="create-position">
              <h2 id="create-position" className="font-serif text-xl font-semibold text-ink">
                Create or update a position
              </h2>
              {canWrite ? (
                <form onSubmit={save} className="card mt-3 max-w-xl space-y-3 p-4">
                  <label className="block text-sm">
                    <span className="field-label">Category</span>
                    <select
                      className="field mt-1"
                      value={cat}
                      onChange={(e) => setCat(e.target.value)}
                    >
                      {CATS.map((c) => (
                        <option key={c} value={c}>
                          {c.replace("_", " ")}
                        </option>
                      ))}
                    </select>
                  </label>

                  {FIELDS[cat].map((f) => (
                    <label key={f.key} className="block text-sm">
                      <span className="field-label">{f.label}</span>
                      {f.type === "bool" ? (
                        <select
                          className="field mt-1"
                          value={vals[`${cat}.${f.key}`] ?? ""}
                          onChange={(e) => setVal(`${cat}.${f.key}`, e.target.value)}
                        >
                          <option value="">—</option>
                          <option value="true">true</option>
                          <option value="false">false</option>
                        </select>
                      ) : (
                        <input
                          className="field mt-1"
                          value={vals[`${cat}.${f.key}`] ?? ""}
                          onChange={(e) => setVal(`${cat}.${f.key}`, e.target.value)}
                        />
                      )}
                    </label>
                  ))}

                  <label className="block text-sm">
                    <span className="field-label">Impact</span>
                    <select
                      className="field mt-1"
                      value={impact}
                      onChange={(e) => setImpact(e.target.value)}
                    >
                      <option value="standard">standard</option>
                      <option value="high">high</option>
                    </select>
                  </label>

                  {formErr && (
                    <p className="text-sm text-brand-700" role="alert">
                      {formErr}
                    </p>
                  )}

                  <Button type="submit" variant="primary" loading={saving}>
                    {saving ? "Saving…" : "Save new version"}
                  </Button>
                  <p className="text-xs text-ink-mute">
                    Saving creates a new version; earlier versions stay in the
                    history and can be restored. Existing analyses are not
                    rewritten.
                  </p>
                </form>
              ) : (
                <p className="card mt-3 p-4 text-sm text-ink-soft">
                  Your role ({role || "unknown"}) is read-only for policies.
                </p>
              )}
            </section>
          </div>
        </TabPanel>

        {/* ----------------------------------------------------- playbook */}
        <TabPanel tabKey="playbook" active={tab === "playbook"}>
          <div className="space-y-3">
            <p className="lede max-w-3xl">
              Your organization&apos;s current contract playbook — the
              positions reviewers enforce today, in plain language.
            </p>
            {loadErr ? (
              <ErrorState message={loadErr} onRetry={load} />
            ) : policies === null ? (
              <TableSkeleton rows={3} cols={3} />
            ) : activeCount === 0 ? (
              <EmptyState
                title="No active positions yet"
                hint="Once a position is recorded it appears here as a plain-language summary."
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {Object.entries(activeByCat).map(([c, p]) => (
                  <article key={c} className="card p-5">
                    <p className="eyebrow">{c.replace("_", " ")}</p>
                    <p className="mt-2 font-serif text-lg text-ink">{describe(p)}</p>
                    <p className="mt-3 text-xs text-ink-mute">
                      v{p.version} ·{" "}
                      {(p.params as { impact?: string }).impact === "high"
                        ? "high impact"
                        : "standard impact"}
                    </p>
                  </article>
                ))}
              </div>
            )}
            <p className="text-xs text-ink-mute">
              Edit positions in the{" "}
              <button
                type="button"
                className="underline underline-offset-2"
                onClick={() => setTab("positions")}
              >
                Positions
              </button>{" "}
              tab.
            </p>
          </div>
        </TabPanel>

        {/* ----------------------------------------------------- history */}
        <TabPanel tabKey="history" active={tab === "history"}>
          <div className="space-y-3">
            <label className="block max-w-xs text-sm">
              <span className="field-label">Category</span>
              <select
                className="field mt-1"
                value={histCat}
                onChange={(e) => setHistCat(e.target.value)}
                aria-label="Policy history category"
              >
                {CATS.map((c) => (
                  <option key={c} value={c}>
                    {c.replace("_", " ")}
                  </option>
                ))}
              </select>
            </label>

            {history === null ? (
              <TableSkeleton rows={4} cols={5} />
            ) : history.length === 0 ? (
              <EmptyState
                title="No versions for this category yet"
                hint="Every save and restore adds a version here."
              />
            ) : (
              <div className="card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="table">
                    <caption className="sr-only">
                      Version history for {histCat.replace("_", " ")}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Version</th>
                        <th scope="col">Position</th>
                        <th scope="col">Active</th>
                        <th scope="col">Changed by / at</th>
                        {canWrite && <th scope="col">Action</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((p) => (
                        <tr key={p.id}>
                          <td>v{p.version}</td>
                          <td className="max-w-[26rem]">{describe(p)}</td>
                          <td>
                            {p.is_active ? (
                              <span className="badge border-emerald-200 bg-emerald-50 text-emerald-800">
                                Active
                              </span>
                            ) : (
                              <span className="badge border-line-strong bg-cream-100 text-ink-soft">
                                Superseded
                              </span>
                            )}
                          </td>
                          <td className="text-xs text-ink-soft">
                            {p.created_by ?? "—"} · {formatWhen(p.created_at)}
                          </td>
                          {canWrite && (
                            <td>
                              {!p.is_active && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => restore(p.id)}
                                  aria-label={`Restore policy version ${p.version}`}
                                >
                                  Restore
                                </Button>
                              )}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </TabPanel>
      </div>

      <Dialog
        open={deactivateTarget !== null}
        title="Deactivate this position?"
        description={
          <>
            The <strong>{(deactivateTarget ?? "").replace("_", " ")}</strong>{" "}
            position stops being applied to future analyses. Nothing is
            deleted: the version stays in the history and can be restored.
          </>
        }
        onClose={() => setDeactivateTarget(null)}
        busy={deactivating}
        testId="deactivate-policy-dialog"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeactivateTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" loading={deactivating} onClick={confirmDeactivate}>
              {deactivating ? "Deactivating…" : "Deactivate"}
            </Button>
          </>
        }
      />

      <p className="text-xs text-ink-mute">
        Policies shape risk analysis findings; they are compared against
        clauses, never against your templates.{" "}
        <Link href="/templates" className="underline underline-offset-2">
          Templates
        </Link>{" "}
        hold clause text.
      </p>
    </main>
  );
}

/** One human sentence per policy — never a raw JSON blob. */
function describe(p: Policy): string {
  const v = p.params as Record<string, unknown>;
  switch (p.category) {
    case "payment":
      return `Payment terms preferred at ${v.max_days ?? "?"} days or fewer.`;
    case "liability":
      return `Liability cap up to ${v.max_amount ?? "?"} ${v.currency ?? ""}.`.trim();
    case "termination":
      return `Termination notice of at least ${v.min_notice_days ?? "?"} days.`;
    case "renewal":
      return v.allow_auto_renew
        ? "Automatic renewal is allowed."
        : "No automatic renewal without review.";
    case "governing_law":
    case "jurisdiction":
      return `Preferred: ${listOf(v.preferred_jurisdictions)}.`;
    case "insurance":
      return `Minimum coverage ${v.min_coverage ?? "?"} ${v.currency ?? ""}.`.trim();
    default:
      return Object.entries(v)
        .filter(([k]) => k !== "impact")
        .map(([k, val]) => `${k}: ${String(val)}`)
        .join(" · ");
  }
}

function listOf(value: unknown): string {
  if (Array.isArray(value) && value.length > 0) return value.join(", ");
  return "no preference recorded";
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
