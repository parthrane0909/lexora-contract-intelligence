"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { API_URL, ApiError, authHeaders, messageForStatus } from "@/lib/api";
import {
  Breadcrumbs,
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
  StatusBadge,
} from "@/components/ui";

type Hit = {
  score: number;
  object_type?: string | null;
  object_id?: string | null;
  contract_id?: string | null;
  template_id?: string | null;
  category?: string | null;
  section_number?: string | null;
  page_start?: number | null;
  text: string;
  contract_filename?: string | null;
  template_name?: string | null;
};

type IndexStatus = {
  objects: number;
  by_status: Record<string, number>;
  models: string[];
};

/**
 * Example searches (Phase 12). Deliberately phrased as *concepts* rather
 * than filenames, so they show what embeddings do that keyword search
 * cannot.
 */
const EXAMPLES = [
  "contracts with unlimited liability",
  "contracts with automatic renewal",
  "vendor owns deliverables",
  "who can end this agreement",
  "payment is due within 30 days",
];

const TYPE_LABEL: Record<string, string> = {
  clause: "Contract clause",
  template_clause: "Template clause",
  policy: "Policy position",
};

export default function SearchPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <SearchView />
    </RequireAuth>
  );
}

/**
 * Semantic search over the existing MiniLM → Qdrant index (Phase 12).
 *
 * Nothing here replaces Elasticsearch: keyword search of filenames lives on
 * the Contracts page, this page searches *meaning*. The embedding model,
 * index size and vector-store reachability are surfaced so a user can tell
 * the difference between "no match" and "nothing indexed".
 */
function SearchView() {
  const [q, setQ] = useState(EXAMPLES[0]);
  const [category, setCategory] = useState("");
  const [objectType, setObjectType] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [model, setModel] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "error" | "done">("idle");
  const [msg, setMsg] = useState("");
  const [ranQuery, setRanQuery] = useState("");

  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [statusErr, setStatusErr] = useState("");

  const loadStatus = useCallback(async () => {
    setStatusErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/retrieval/status`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));
      setStatus((await res.json()) as IndexStatus);
    } catch (e) {
      setStatusErr(e instanceof ApiError ? e.message : "Could not read the index status.");
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  async function run(text?: string, e?: React.FormEvent) {
    e?.preventDefault();
    const query = (text ?? q).trim();
    if (!query) {
      setState("error");
      return setMsg("Enter a search query first.");
    }
    setState("loading");
    setMsg("");
    setRanQuery(query);
    try {
      const res = await fetch(`${API_URL}/api/v1/retrieval/search`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          top_k: 10,
          category: category.trim() || null,
          object_type: objectType || null,
        }),
      });
      if (!res.ok) {
        const detail = (await res.text()).trim().slice(0, 300);
        throw new ApiError(res.status, detail || messageForStatus(res.status));
      }
      const j = (await res.json()) as { results: Hit[]; model: string };
      setHits(j.results);
      setModel(j.model);
      setMsg("");
      setState("done");
    } catch (err) {
      setState("error");
      setMsg(err instanceof ApiError ? err.message : "The semantic backend could not be reached.");
    }
  }

  const statusLine = status
    ? [
        `${status.objects} object${status.objects === 1 ? "" : "s"} indexed`,
        ...Object.entries(status.by_status)
          .filter(([k]) => k !== "indexed")
          .map(([k, v]) => `${v} ${k}`),
      ].join(" · ")
    : "";

  return (
    <main className="space-y-6">
      <Breadcrumbs items={[{ label: "Semantic Search" }]} />

      <PageHeader
        eyebrow="Meaning, not keywords"
        title="Semantic Search"
        description={
          <>
            Describe what you are looking for in plain language. Clause text is
            embedded with MiniLM and matched by cosine similarity in Qdrant —
            so &ldquo;who can end this agreement&rdquo; finds termination
            language without the words &ldquo;terminate&rdquo;.
          </>
        }
      />

      {/* ------------------------------------------------------------ form */}
      <section aria-label="Search form" className="card space-y-4 p-5">
        <form onSubmit={(e) => run(undefined, e)} className="flex flex-wrap items-end gap-3">
          <label className="min-w-[16rem] flex-1 text-sm">
            <span className="field-label">Describe what you are looking for</span>
            <input
              className="field mt-1"
              value={q}
              aria-label="Semantic search query"
              placeholder="contracts with unlimited liability"
              onChange={(e) => setQ(e.target.value)}
            />
          </label>

          <label className="text-sm">
            <span className="field-label">Source</span>
            <select
              className="field mt-1"
              value={objectType}
              aria-label="Filter by object type"
              onChange={(e) => setObjectType(e.target.value)}
            >
              <option value="">Everything</option>
              <option value="clause">Contract clauses</option>
              <option value="template_clause">Template clauses</option>
              <option value="policy">Policy positions</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="field-label">Clause category</span>
            <input
              className="field mt-1 w-40"
              placeholder="liability"
              aria-label="Filter by clause category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </label>

          <Button type="submit" variant="primary" loading={state === "loading"}>
            {state === "loading" ? "Searching…" : "Search"}
          </Button>
        </form>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="eyebrow">Try</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              className="chip"
              onClick={() => {
                setQ(ex);
                run(ex);
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      </section>

      {/* -------------------------------------------------- index status */}
      <section
        aria-label="Index status"
        className="card flex flex-wrap items-center justify-between gap-3 p-4"
      >
        <div className="space-y-1 text-sm">
          <p className="eyebrow">Index status</p>
          {statusErr ? (
            <p className="text-brand-700" role="status">
              {statusErr}{" "}
              <button type="button" className="underline" onClick={loadStatus}>
                Try again
              </button>
            </p>
          ) : status === null ? (
            <Spinner label="Reading index status…" />
          ) : (
            <>
              <p className="text-ink-soft">
                <span
                  aria-hidden="true"
                  className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-500 align-middle"
                />
                {statusLine}
                {status.models.length > 0 && (
                  <span className="text-ink-mute"> · {status.models.join(", ")}</span>
                )}
              </p>
              <p className="text-xs text-ink-mute">
                Contract and template clauses are embedded automatically as
                they finish processing. Policy positions are only searched once
                they have been indexed explicitly — a policy you just saved
                will not appear here on its own.
              </p>
            </>
          )}
        </div>

        <p className="max-w-sm text-xs text-ink-mute">
          Indexing is automatic. To rebuild one contract&apos;s index, open it
          and use <strong>Re-index</strong> there —{" "}
          <Link href="/contracts" className="underline underline-offset-2">
            go to Contracts
          </Link>
          .
        </p>
      </section>

      {/* --------------------------------------------------------- states */}
      {state === "loading" && (
        <div className="space-y-3" role="status" aria-live="polite">
          <Spinner label="Embedding your query and searching the vector index…" />
          <div className="card space-y-3 p-4" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-16 w-full" />
            ))}
          </div>
        </div>
      )}

      {state === "error" && <ErrorState message={msg} onRetry={() => run()} />}

      {state === "done" && hits !== null && hits.length === 0 && (
        <EmptyState
          title="No indexed objects matched those filters"
          hint="Nearest-neighbour search always returns something when the index has content — an empty result therefore means the source/category filter excluded everything, or nothing has been indexed yet. Check the index status above."
        />
      )}

      {state === "done" && hits !== null && hits.length > 0 && (
        <section aria-label="Results" className="space-y-3">
          <p className="text-xs text-ink-mute">
            {hits.length} closest match{hits.length === 1 ? "" : "es"} for
            &ldquo;{ranQuery}&rdquo; ·{" "}
            <span title="Cosine similarity between your query and the clause text">
              similarity score (0–1, higher means closer meaning)
            </span>{" "}
            · results under 0.3 are flagged <em>weak match</em>
            {" "}· text similarity is not a legal judgement
          </p>

          <ol className="space-y-3">
            {hits.map((h, i) => (
              <li key={`${h.object_type}-${h.object_id}`} className="card p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <StatusBadge tone="brand" title="What kind of object this is">
                    {TYPE_LABEL[h.object_type ?? ""] ?? h.object_type ?? "result"}
                  </StatusBadge>
                  {h.category && <span className="badge">{h.category}</span>}
                  {h.score < 0.3 && (
                    <span
                      className="badge border-amber-200 bg-amber-50 text-amber-800"
                      title="Below 0.3 similarity — the closest thing in the index, but not a close match"
                    >
                      weak match
                    </span>
                  )}
                  {h.page_start != null && <span className="text-ink-mute">page {h.page_start}</span>}
                  {h.section_number && (
                    <span className="text-ink-mute">§{h.section_number}</span>
                  )}
                  <span className="ml-auto text-ink-mute" title="Cosine similarity, 0–1">
                    similarity {h.score.toFixed(3)}
                  </span>
                </div>

                <p className="mt-2 text-sm leading-relaxed text-ink">
                  {h.text.slice(0, 700)}
                  {h.text.length > 700 ? "…" : ""}
                </p>

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line-soft pt-3 text-xs">
                  <span className="text-ink-mute">
                    {h.contract_filename ?? h.template_name ?? "Policy position"}
                    <span className="mx-1.5 text-line-strong">·</span>
                    match {i + 1} of {hits.length}
                  </span>
                  <span className="flex gap-3">
                    {h.object_type === "clause" && h.contract_id && (
                      <Link
                        className="font-semibold text-brand-700 underline-offset-2 hover:underline"
                        href={`/contracts/${h.contract_id}#clause-${h.object_id}`}
                      >
                        Open in contract →
                      </Link>
                    )}
                    {h.object_type === "template_clause" && h.template_id && (
                      <Link
                        className="font-semibold text-brand-700 underline-offset-2 hover:underline"
                        href={`/templates/${h.template_id}`}
                      >
                        Open template →
                      </Link>
                    )}
                    {h.object_type === "policy" && (
                      <Link
                        className="font-semibold text-brand-700 underline-offset-2 hover:underline"
                        href="/policies"
                      >
                        Open policies →
                      </Link>
                    )}
                  </span>
                </div>
              </li>
            ))}
          </ol>

          <p className="text-xs text-ink-mute">
            Embedding model: <code>{model || "unknown"}</code>. This searches
            clause text only — for filenames, use{" "}
            <Link href="/contracts" className="underline underline-offset-2">
              contract search
            </Link>
            .
          </p>
        </section>
      )}

    </main>
  );
}
