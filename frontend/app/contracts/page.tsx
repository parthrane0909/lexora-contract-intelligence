"use client";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { API_URL, ApiError, authHeaders, messageForStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  CATEGORIES,
  DEFAULT_FILTERS,
  PAGE_SIZES,
  RELEVANCE_SORT,
  SORTS,
  UNSPECIFIED,
  activeFilterCount,
  analysisLabel,
  analysisTone,
  categoryTone,
  filterParams,
  formatDate,
  indexLabel,
  indexTone,
  pageParams,
  processingLabel,
  processingTone,
  qs,
  riskHref,
  searchPageParams,
  summaryHref,
  type ContractRow,
  type Filters,
  type SortKey,
} from "@/lib/contracts";
import { useContractSearch } from "@/lib/useContractSearch";
import { Dialog } from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { RequireAuth } from "@/components/RequireAuth";
import {
  Button,
  EmptyState,
  ErrorState,
  FilterChip,
  KpiCard,
  PageHeader,
  Pagination,
  Spinner,
  StatusBadge,
  TableSkeleton,
} from "@/components/ui";

/** One organization's dashboard KPI numbers (GET /portfolio/summary). */
type Summary = {
  total_contracts: number;
  contracts_with_high_critical_unresolved: number;
  contracts_renewing_30d: number;
  important_contracts: number;
  upcoming_renewals_30d: number;
};

export default function ContractsPage() {
  return (
    <RequireAuth>
      <ContractsView />
    </RequireAuth>
  );
}

/** Roles allowed to change metadata or archive (backend PATCH/DELETE RBAC). */
function canWrite(role?: string): boolean {
  return role === "admin" || role === "contract_manager";
}

function ContractsView() {
  const { user } = useAuth();
  const toast = useToast();
  const writable = canWrite(user?.role);

  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  /**
   * Ordering used while a search query is active.
   *
   * "Relevance" is the natural default for Elasticsearch results and has no
   * meaning for the plain PostgreSQL listing, so it is kept beside
   * `filters.sort` instead of inside it — the sort control simply shows a
   * "Relevance (best match)" option while a query is typed and hides it
   * again once the search is cleared.
   */
  const [searchSort, setSearchSort] = useState<SortKey>(RELEVANCE_SORT.key);
  /** Latest (trimmed) search query, readable from the options callback. */
  const queryRef = useRef("");
  const [rows, setRows] = useState<ContractRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [listErr, setListErr] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryErr, setSummaryErr] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null); // star/category save
  const [archiveTarget, setArchiveTarget] = useState<ContractRow | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [archiveErr, setArchiveErr] = useState("");

  const writableRef = useRef(writable);
  writableRef.current = writable;

  /* ------------------------------------------------------------ filtering */

  const update = useCallback((patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch, page: 1 }));
  }, []);

  const resetFilters = useCallback(() => {
    setFilters((f) => ({ ...DEFAULT_FILTERS, sort: f.sort, pageSize: f.pageSize }));
  }, []);

  const filtersKey = useMemo(
    () =>
      JSON.stringify(filterParams(filters)) +
      "|" +
      JSON.stringify(searchPageParams(filters)) +
      "|" +
      searchSort,
    [filters, searchSort]
  );

  /* ------------------------------------------- Elasticsearch-backed search */

  const getOptions = useCallback(
    () => ({
      category: filters.category || undefined,
      analysis_status: filters.analysis || undefined,
      processing_status: filters.processing || undefined,
      important: filters.important ? true : undefined,
      high_risk: filters.highRisk || undefined,
      renewing_within_days: filters.renewingWithinDays,
      include_archived: filters.includeArchived || undefined,
      sort: queryRef.current ? searchSort : filters.sort,
      limit: filters.pageSize,
      offset: (filters.page - 1) * filters.pageSize,
    }),
    [filters, searchSort]
  );

  const cs = useContractSearch(getOptions, filtersKey);
  const rawQuery = cs.query.trim();
  const searching = rawQuery.length > 0;
  queryRef.current = rawQuery; // read by getOptions when the search runs

  /* --------------------------------------------------- PostgreSQL listing */

  const loadList = useCallback(async () => {
    setListErr("");
    try {
      const url = `${API_URL}/api/v1/contracts${qs({
        ...filterParams(filters),
        ...pageParams(filters),
      })}`;
      const res = await fetch(url, { headers: authHeaders() });
      if (res.status === 401) {
        setListErr("Please log in again.");
        return;
      }
      if (!res.ok) {
        setListErr(messageForStatus(res.status));
        return;
      }
      setRows((await res.json()) as ContractRow[]);
      setTotal(Number(res.headers.get("X-Total-Count") ?? 0));
    } catch {
      setListErr("Unable to reach the backend.");
    }
  }, [filters]);

  const loadSummary = useCallback(async () => {
    setSummaryErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/portfolio/summary`, {
        headers: authHeaders(),
      });
      if (!res.ok) {
        setSummaryErr("KPI counts are temporarily unavailable.");
        return;
      }
      setSummary((await res.json()) as Summary);
    } catch {
      setSummaryErr("KPI counts are temporarily unavailable.");
    }
  }, []);

  // The listing is ALWAYS loaded (even while a search is active): it is what
  // renders underneath a search error, so a failed search can never leave the
  // page looking frozen.
  useEffect(() => {
    void loadList();
    void loadSummary();
  }, [filtersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ------------------------------------------- background processing poll */

  const inFlight =
    rows?.some(
      (c) =>
        ["uploaded", "queued", "processing"].includes(c.processing_status) ||
        ["queued", "processing"].includes(c.semantic_index_status ?? "pending")
    ) ?? false;

  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(() => {
      void loadList();
      void loadSummary();
      if (searching) cs.rerun();
    }, 4000);
    return () => clearInterval(t);
  }, [inFlight, searching]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ------------------------------------------------------------- mutations */

  const patchContract = useCallback(
    async (id: string, body: Record<string, unknown>): Promise<ContractRow> => {
      const res = await fetch(`${API_URL}/api/v1/contracts/${id}`, {
        method: "PATCH",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        if (res.status === 403) throw new ApiError(403, "You do not have permission to change this contract.");
        if (res.status === 404) throw new ApiError(404, "Contract not found.");
        throw new ApiError(res.status, messageForStatus(res.status));
      }
      return (await res.json()) as ContractRow;
    },
    []
  );

  const applyRow = useCallback((updated: ContractRow) => {
    setRows((prev) =>
      prev ? prev.map((r) => (r.id === updated.id ? { ...r, ...updated } : r)) : prev
    );
  }, []);

  /** Optimistic star toggle with rollback on failure. */
  const toggleImportant = useCallback(
    async (row: ContractRow) => {
      if (pendingId) return;
      const next = !row.is_important;
      setPendingId(row.id);
      applyRow({ ...row, is_important: next }); // optimistic
      try {
        const saved = await patchContract(row.id, { is_important: next });
        applyRow(saved);
        void loadSummary(); // the Important KPI must track the star
        if (searching) cs.rerun();
        toast.success(next ? `Starred ${row.filename}` : `Removed star from ${row.filename}`);
      } catch (e) {
        applyRow({ ...row, is_important: !next }); // rollback
        const msg = e instanceof ApiError ? e.message : "Could not update the star.";
        toast.error(msg);
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, applyRow, patchContract, loadSummary, searching, cs, toast]
  );

  const changeCategory = useCallback(
    async (row: ContractRow, category: string) => {
      if (pendingId) return;
      const previous = row.contract_category || UNSPECIFIED;
      const next = category;
      if (next === previous) return;
      setPendingId(row.id);
      applyRow({ ...row, contract_category: next }); // optimistic
      try {
        const saved = await patchContract(row.id, { contract_category: next });
        applyRow(saved);
        toast.success(`Category updated to ${saved.contract_category}`);
        // The backend re-indexed the document; refresh search results too.
        if (searching) cs.rerun();
      } catch (e) {
        applyRow({ ...row, contract_category: previous }); // rollback
        const msg = e instanceof ApiError ? e.message : "Could not save the category.";
        toast.error(msg);
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, applyRow, patchContract, searching, cs, toast]
  );

  const confirmArchive = useCallback(async () => {
    if (!archiveTarget) return;
    setArchiving(true);
    setArchiveErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/contracts/${archiveTarget.id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        let msg = messageForStatus(res.status);
        try {
          const parsed = JSON.parse(detail);
          if (typeof parsed?.detail === "string") msg = parsed.detail;
        } catch {
          /* keep the generic message */
        }
        throw new ApiError(res.status, msg);
      }
      setArchiveTarget(null);
      toast.success(`${archiveTarget.filename} moved to the archived view.`);
      await Promise.all([loadList(), loadSummary()]);
      if (searching) cs.rerun();
    } catch (e) {
      setArchiveErr(e instanceof ApiError ? e.message : "Archiving failed. Nothing was changed.");
    } finally {
      setArchiving(false);
    }
  }, [archiveTarget, loadList, loadSummary, searching, cs, toast]);

  /* ---------------------------------------------------------------- upload */

  const upload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      e.target.value = "";
      if (!f) return;
      setUploadErr("");
      if (!/\.(pdf|docx)$/i.test(f.name)) {
        setUploadErr("Only PDF or DOCX files are accepted.");
        return;
      }
      setUploading(true);
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await fetch(`${API_URL}/api/v1/contracts/upload`, {
          method: "POST",
          headers: authHeaders(),
          body: fd,
        });
        if (!res.ok) {
          setUploadErr(messageForStatus(res.status));
          return;
        }
        // Upload only stores the file and publishes it to the search index;
        // the document pipeline runs when someone asks for it on the contract
        // page, so never claim that processing has already started.
        toast.success(`${f.name} uploaded — open it to run the analysis pipeline.`);
        await Promise.all([loadList(), loadSummary()]);
        if (searching) cs.rerun();
      } catch {
        setUploadErr("Unable to reach the backend.");
      } finally {
        setUploading(false);
      }
    },
    [loadList, loadSummary, searching, cs, toast]
  );

  /* --------------------------------------------------------------- derived */

  const searchRows: ContractRow[] | null =
    cs.results === null
      ? null
      : cs.results.map((h) => ({
          id: h.contract_id,
          filename: h.filename,
          contract_type: h.contract_type,
          contract_category: h.contract_category || UNSPECIFIED,
          counterparty: h.counterparty,
          processing_status: h.processing_status,
          analysis_status: h.analysis_status,
          semantic_index_status: h.semantic_index_status,
          is_important: h.is_important ?? false,
          is_archived: h.is_archived,
          created_at: h.created_at,
          score: h.score,
        }));

  const activeCount = activeFilterCount(filters);
  const isSearchError = searching && cs.status === "error";
  const displayed = searching ? searchRows : rows;
  const displayedTotal = searching ? (cs.total ?? 0) : total;
  const loading = searching ? cs.status === "typing" || cs.status === "searching" : rows === null;

  function statusLine(): string {
    if (!searching) return "";
    switch (cs.status) {
      case "typing":
        return "Update pending…";
      case "searching":
        return "Searching contracts…";
      case "results":
        return `${searchRows?.length ?? 0} contract${
          (searchRows?.length ?? 0) === 1 ? "" : "s"
        } match “${rawQuery}”`;
      case "empty":
        return `No contracts match “${rawQuery}”.`;
      case "error":
        return cs.error || "Search is temporarily unavailable.";
      default:
        return "";
    }
  }

  const chips: { key: string; label: string; value?: string; remove: () => void }[] = [];
  if (filters.category)
    chips.push({ key: "category", label: "Category", value: filters.category, remove: () => update({ category: "" }) });
  if (filters.analysis)
    chips.push({ key: "analysis", label: "Analysis", value: filters.analysis.replace(/_/g, " "), remove: () => update({ analysis: "" }) });
  if (filters.processing)
    chips.push({ key: "processing", label: "Processing", value: processingLabel(filters.processing), remove: () => update({ processing: "" }) });
  if (filters.important)
    chips.push({ key: "important", label: "Starred only", remove: () => update({ important: false }) });
  if (filters.highRisk)
    chips.push({ key: "high", label: "High-risk only", remove: () => update({ highRisk: false }) });
  if (filters.renewingWithinDays !== null)
    chips.push({ key: "renewing", label: "Renewing within", value: `${filters.renewingWithinDays} days`, remove: () => update({ renewingWithinDays: null }) });
  if (filters.includeArchived)
    chips.push({ key: "archived", label: "Including archived", remove: () => update({ includeArchived: false }) });

  /* ---------------------------------------------------------------- render */

  return (
    <main className="space-y-6">
      <PageHeader
        eyebrow="Contract register"
        title="Contracts"
        description={
          <>
            Search, categorize, prioritize and archive every agreement your
            organization holds — from one place.
          </>
        }
        actions={
          <>
            <label
              className={`btn btn-primary cursor-pointer ${uploading ? "pointer-events-none opacity-60" : ""}`}
              aria-live="polite"
            >
              {uploading ? "Uploading…" : "Upload Contract"}
              <input
                type="file"
                className="hidden"
                accept=".pdf,.docx"
                onChange={upload}
                disabled={uploading}
                aria-label="Upload contract file"
              />
            </label>
          </>
        }
      />

      {uploading && <Spinner label="Uploading contract…" />}
      {uploadErr && <ErrorState message={uploadErr} />}
      {/* ------------------------------------------------------ search bar */}
      <section aria-label="Search contracts" className="mx-auto w-full max-w-2xl">
        <div className="relative">
          <span
            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-mute"
            aria-hidden="true"
          >
            ⌕
          </span>
          <input
            className="field py-3.5 pl-11 pr-24 text-base shadow-card"
            placeholder="Search by filename — partial words and typos are fine"
            aria-label="Search contracts by filename"
            aria-describedby="contract-search-status"
            value={cs.query}
            onChange={(e) => cs.setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                cs.submit(cs.query);
              } else if (e.key === "Escape") {
                e.preventDefault();
                cs.clear();
              }
            }}
          />
          <span className="absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-2">
            {(cs.status === "searching" || cs.status === "typing") && (
              <span className="ui-spinner" aria-hidden="true" />
            )}
            {cs.query.length > 0 && (
              <button
                type="button"
                onClick={cs.clear}
                aria-label="Clear search"
                title="Clear search (Esc)"
                className="grid h-6 w-6 place-items-center rounded-full text-ink-mute transition hover:bg-cream-200 hover:text-ink"
              >
                ✕
              </button>
            )}
          </span>
        </div>
        <p
          id="contract-search-status"
          role="status"
          aria-live="polite"
          className="mt-2 min-h-[1.25rem] text-center text-sm text-ink-soft"
        >
          {statusLine()}
        </p>
      </section>

      {/* -------------------------------------------------- filters + sort */}
      <section aria-label="Filters and sorting" className="space-y-3">
        <div className="card flex flex-wrap items-end gap-3 p-4">
          <label className="min-w-[11rem] flex-1">
            <span className="field-label">Category</span>
            <select
              className="field"
              value={filters.category}
              onChange={(e) => update({ category: e.target.value })}
              aria-label="Filter by contract category"
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>

          <label className="min-w-[10rem] flex-1">
            <span className="field-label">Analysis</span>
            <select
              className="field"
              value={filters.analysis}
              onChange={(e) => update({ analysis: e.target.value })}
              aria-label="Filter by analysis status"
            >
              <option value="">Any analysis state</option>
              <option value="complete">Analysis complete</option>
              <option value="pending">Analysis incomplete</option>
              <option value="partial">Partial</option>
              <option value="stale">Stale</option>
              <option value="classified">Classified only</option>
              <option value="queued">Processing</option>
              <option value="failed">Failed</option>
            </select>
          </label>

          <label className="min-w-[10rem] flex-1">
            <span className="field-label">Processing</span>
            <select
              className="field"
              value={filters.processing}
              onChange={(e) => update({ processing: e.target.value })}
              aria-label="Filter by processing status"
            >
              <option value="">Any processing state</option>
              <option value="processed">Ready</option>
              <option value="uploaded">Uploaded</option>
              <option value="queued">Queued</option>
              <option value="processing">Processing</option>
              <option value="failed">Failed</option>
            </select>
          </label>

          <label className="min-w-[10rem] flex-1">
            <span className="field-label">Sort</span>
            <select
              className="field"
              value={searching ? searchSort : filters.sort}
              onChange={(e) => {
                const v = e.target.value as SortKey;
                // A search runs on Elasticsearch (relevance applies); the
                // plain listing runs on PostgreSQL. Each keeps its own order.
                if (searching) setSearchSort(v);
                else update({ sort: v });
              }}
              aria-label="Sort contracts"
            >
              {(searching ? [RELEVANCE_SORT, ...SORTS] : SORTS).map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>

          <div className="flex flex-wrap items-center gap-2 pb-0.5">
            <Toggle
              pressed={filters.important}
              onClick={() => update({ important: !filters.important })}
              label="Starred only"
            />
            <Toggle
              pressed={filters.highRisk}
              onClick={() => update({ highRisk: !filters.highRisk })}
              label="High risk"
            />
            <Toggle
              pressed={filters.renewingWithinDays === 30}
              onClick={() =>
                update({ renewingWithinDays: filters.renewingWithinDays === 30 ? null : 30 })
              }
              label="Renewing in 30 days"
            />
            <Toggle
              pressed={filters.includeArchived}
              onClick={() => update({ includeArchived: !filters.includeArchived })}
              label="Show archived"
            />
            {activeCount > 0 && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={resetFilters}>
                Reset filters
              </button>
            )}
          </div>
        </div>

        {chips.length > 0 && (
          <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
            <span className="eyebrow">Active</span>
            {chips.map((c) => (
              <FilterChip key={c.key} label={c.label} value={c.value} onRemove={c.remove} />
            ))}
          </div>
        )}
      </section>

      {/* -------------------------------------------------------- KPI cards */}
      <section aria-label="Key figures">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            testId="kpi-total"
            label="Total contracts"
            value={summary ? summary.total_contracts : "–"}
            caption="Active contracts in your organization"
            hint="Show all"
            active={activeCount === 0}
            onClick={resetFilters}
          />
          <KpiCard
            testId="kpi-high-risk"
            label="High-risk contracts"
            value={summary ? summary.contracts_with_high_critical_unresolved : "–"}
            caption="Distinct contracts with unresolved high/critical findings"
            hint="Filter the table"
            tone="brand"
            active={filters.highRisk}
            onClick={() => update({ highRisk: !filters.highRisk })}
          />
          <KpiCard
            testId="kpi-renewals"
            label="Renewals in 30 days"
            value={summary ? summary.contracts_renewing_30d : "–"}
            caption="Distinct contracts with a known renewal date"
            hint="Filter the table"
            tone="gold"
            active={filters.renewingWithinDays === 30}
            onClick={() =>
              update({ renewingWithinDays: filters.renewingWithinDays === 30 ? null : 30 })
            }
          />
          <KpiCard
            testId="kpi-important"
            label="Important contracts"
            value={summary ? summary.important_contracts : "–"}
            caption="Contracts you starred"
            hint="Filter the table"
            active={filters.important}
            onClick={() => update({ important: !filters.important })}
          />
        </div>
        {summaryErr && (
          <p className="mt-2 text-xs text-amber-700" role="status">
            {summaryErr}
          </p>
        )}
      </section>

      {/* ------------------------------------------------------------- table */}
      <section aria-label="Contract list" className="overflow-hidden">
        {isSearchError ? (
          // A failed search never blanks the page: the error is explicit and
          // the ordinary PostgreSQL-backed listing stays visible underneath.
          <div className="space-y-4">
            <ErrorState message={cs.error} onRetry={cs.rerun} />
            {rows !== null && rows.length > 0 && (
              <ContractTable
                rows={rows}
                writable={writable}
                pendingId={pendingId}
                onStar={toggleImportant}
                onCategory={changeCategory}
                onArchive={(r) => {
                  setArchiveErr("");
                  setArchiveTarget(r);
                }}
              />
            )}
          </div>
        ) : loading ? (
          <TableSkeleton rows={Math.min(filters.pageSize, 6)} />
        ) : searching && cs.status === "empty" ? (
          <EmptyState
            title={`No contracts match “${rawQuery}”.`}
            hint="Try part of a filename — partial words and typos are fine."
            action={
              <Button variant="secondary" onClick={cs.clear}>
                Clear search
              </Button>
            }
          />
        ) : (displayed?.length ?? 0) === 0 ? (
          <EmptyState
            title={
              activeCount > 0
                ? "No contracts match these filters"
                : filters.includeArchived
                ? "No contracts yet"
                : "No contracts yet"
            }
            hint={
              activeCount > 0
                ? "Adjust or reset the filters to see more contracts."
                : "Upload your first contract to begin analysis."
            }
            action={
              activeCount > 0 ? (
                <Button variant="secondary" onClick={resetFilters}>
                  Reset filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <ContractTable
                rows={displayed ?? []}
                writable={writable}
                pendingId={pendingId}
                onStar={toggleImportant}
                onCategory={changeCategory}
                onArchive={(r) => {
                  setArchiveErr("");
                  setArchiveTarget(r);
                }}
              />
            </div>
            <div className="border-t border-line">
              <Pagination
                page={filters.page}
                pageSize={filters.pageSize}
                total={displayedTotal}
                onPage={(p) => setFilters((f) => ({ ...f, page: p }))}
                onPageSize={(n) => setFilters((f) => ({ ...f, pageSize: n, page: 1 }))}
                pageSizes={PAGE_SIZES}
              />
            </div>
          </div>
        )}
      </section>

      {listErr && searching && (
        <p className="text-xs text-amber-700" role="status">
          Background list: {listErr}
        </p>
      )}

      {/* ---------------------------------------------------- archive dialog */}
      <Dialog
        open={archiveTarget !== null}
        title="Archive this contract?"
        description={
          <>
            <strong className="text-ink">{archiveTarget?.filename}</strong> will
            disappear from the active contract list and from search results. It
            is an <strong>archive, not a permanent deletion</strong> — the
            document, its clauses, findings and analysis are all kept and remain
            visible under “Show archived”.
          </>
        }
        onClose={() => {
          if (!archiving) setArchiveTarget(null);
        }}
        busy={archiving}
        testId="archive-dialog"
        footer={
          <>
            <Button variant="secondary" onClick={() => setArchiveTarget(null)} disabled={archiving}>
              Cancel
            </Button>
            <Button variant="danger" loading={archiving} onClick={confirmArchive} data-testid="confirm-archive">
              {archiving ? "Archiving…" : "Archive contract"}
            </Button>
          </>
        }
      >
        {archiveErr && (
          <p className="rounded border border-brand-200 bg-brand-50 p-2 text-sm text-brand-800" role="alert">
            {archiveErr}
          </p>
        )}
      </Dialog>

      <p className="text-xs text-ink-mute">
        Need the archived ones back?{" "}
        <button
          type="button"
          className="inline-flex min-h-6 items-center underline hover:text-brand-800"
          onClick={() => update({ includeArchived: true })}
        >
          Show archived contracts
        </button>
        .
      </p>
    </main>
  );
}

/* -------------------------------------------------------------- subviews */

function Toggle({
  pressed,
  onClick,
  label,
}: {
  pressed: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`btn btn-sm min-h-8 rounded-full border transition ${
        pressed
          ? "border-brand-600 bg-brand-50 text-brand-800"
          : "border-line-strong bg-paper text-ink-soft hover:border-brand-300"
      }`}
    >
      {pressed && <span aria-hidden="true">✓</span>}
      {label}
    </button>
  );
}

function ContractTable({
  rows,
  writable,
  pendingId,
  onStar,
  onCategory,
  onArchive,
}: {
  rows: ContractRow[];
  writable: boolean;
  pendingId: string | null;
  onStar: (r: ContractRow) => void;
  onCategory: (r: ContractRow, c: string) => void;
  onArchive: (r: ContractRow) => void;
}) {
  return (
    <table className="table min-w-[62rem]">
      <caption className="sr-only">
        Contracts, with importance, category, analysis state and available actions
      </caption>
      <thead>
        <tr>
          <th scope="col" className="w-12">
            <span className="sr-only">Important</span>
            <span aria-hidden="true">★</span>
          </th>
          <th scope="col">Contract</th>
          <th scope="col">Category</th>
          <th scope="col">Analysis</th>
          <th scope="col">Summary</th>
          <th scope="col">Risk assessment</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const busy = pendingId === r.id;
          const cat = r.contract_category || UNSPECIFIED;
          const legacy = !!r.contract_type && !CATEGORIES.includes(cat as never);
          return (
            <tr key={r.id} data-contract-row={r.id}>
              <td>
                <button
                  type="button"
                  onClick={() => onStar(r)}
                  disabled={!writable || busy}
                  aria-pressed={r.is_important}
                  aria-label={
                    r.is_important
                      ? `Remove ${r.filename} from important contracts`
                      : `Mark ${r.filename} as important`
                  }
                  title={
                    writable
                      ? r.is_important
                        ? "Important — click to remove"
                        : "Mark as important"
                      : "Only admins and contract managers can mark contracts"
                  }
                  data-testid={`star-${r.id}`}
                  className={`grid h-8 w-8 place-items-center rounded-full text-lg transition ${
                    r.is_important
                      ? "text-gold-500 hover:bg-gold-100"
                      : "text-line-strong hover:bg-cream-200 hover:text-gold-500"
                  } ${!writable ? "cursor-not-allowed opacity-50" : ""}`}
                >
                  <span aria-hidden="true">{r.is_important ? "★" : "☆"}</span>
                </button>
              </td>

              <td className="max-w-[22rem]">
                <a
                  href={`/contracts/${r.id}`}
                  className="contract-filename inline-flex min-h-6 items-center py-1 font-semibold text-ink underline-offset-2 hover:text-brand-800 hover:underline"
                >
                  {r.filename}
                </a>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-mute">
                  <StatusBadge tone={processingTone(r.processing_status)} title="PDF processing state">
                    {processingLabel(r.processing_status)}
                  </StatusBadge>
                  <span>{formatDate(r.created_at)}</span>
                  {r.counterparty && <span>· {r.counterparty}</span>}
                  {r.is_archived && (
                    <span className="badge border-line-strong bg-cream-100 text-ink-soft">
                      Archived
                    </span>
                  )}
                  <span
                    className="inline-flex items-center gap-1"
                    title="Semantic index state (Qdrant)"
                  >
                    <StatusBadge tone={indexTone(r.semantic_index_status)}>
                      {indexLabel(r.semantic_index_status)}
                    </StatusBadge>
                  </span>
                </div>
              </td>

              <td className="min-w-[11rem]">
                {writable ? (
                  <select
                    className="field max-w-[11rem] py-1 text-xs"
                    value={cat}
                    disabled={busy}
                    onChange={(e) => onCategory(r, e.target.value)}
                    aria-label={`Category for ${r.filename}`}
                    data-testid={`category-${r.id}`}
                  >
                    {legacy && (
                      <option key={cat} value={cat}>
                        {cat} (legacy)
                      </option>
                    )}
                    {CATEGORIES.filter((c) => c !== cat || !legacy).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                ) : (
                  <StatusBadge tone={categoryTone(cat)} title="Contract category">
                    {cat}
                  </StatusBadge>
                )}
                {legacy && !writable && (
                  <p className="mt-1 text-[0.6875rem] text-ink-mute">Legacy free-text type</p>
                )}
              </td>

              <td>
                <StatusBadge tone={analysisTone(r.processing_status, r.analysis_status)}>
                  {analysisLabel(r.processing_status, r.analysis_status)}
                </StatusBadge>
              </td>

              <td>
                <Link
                  href={summaryHref(r.id)}
                  className="btn btn-ghost btn-sm whitespace-nowrap text-brand-700 hover:text-brand-900"
                  aria-label={`Open contract summary for ${r.filename}`}
                  title="Summary"
                >
                  Summary&nbsp;→
                </Link>
              </td>

              <td>
                <Link
                  href={riskHref(r.id)}
                  className="btn btn-ghost btn-sm whitespace-nowrap text-brand-700 hover:text-brand-900"
                  aria-label={`Open risk assessment for ${r.filename}`}
                  title="Risk assessment"
                >
                  Risk&nbsp;→
                </Link>
              </td>

              <td className="whitespace-nowrap">
                {r.is_archived ? (
                  <span className="text-xs text-ink-mute">Archived</span>
                ) : writable ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onArchive(r)}
                    aria-label={`Archive ${r.filename}`}
                    data-testid={`archive-${r.id}`}
                  >
                    Archive
                  </Button>
                ) : (
                  <span className="text-xs text-ink-mute">Read-only</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
