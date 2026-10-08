"use client";

// Shared contract-list vocabulary and helpers (Phases 6, 7 and 9).
//
// Everything here is presentation + query-building only: the authoritative
// values come from the backend (contracts/category.CATEGORIES,
// risk_engine.state.ANALYSIS_STATUSES, contracts/service.
// PROCESSING_STATUSES) and the same strings are sent back as query
// parameters, so what the UI shows and what the API filters on can never
// drift apart.

/** The backend's validated contract-category vocabulary, in display order. */
export const CATEGORIES = [
  "Non-Disclosure Agreement",
  "Employment Agreement",
  "SaaS Subscription Agreement",
  "Vendor Agreement",
  "Licensing Agreement",
  "Services Agreement",
  "Sales Agreement",
  "Other / Unspecified",
] as const;

/** Explicit label for "no category was ever chosen". */
export const UNSPECIFIED = "Other / Unspecified";

/**
 * Sort keys understood by the two list endpoints.
 *
 * `relevance` exists only for Elasticsearch: the PostgreSQL listing has no
 * notion of relevance and never receives it (the UI only offers it while a
 * search query is active).
 */
export type SortKey = "relevance" | "newest" | "oldest" | "filename" | "category";

export const SORTS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "filename", label: "Filename (A–Z)" },
  { key: "category", label: "Category" },
];

/** Default ordering while a search query is typed — best match first. */
export const RELEVANCE_SORT: { key: SortKey; label: string } = {
  key: "relevance",
  label: "Relevance (best match)",
};

export const PAGE_SIZES = [10, 25, 50];

export const ANALYSIS_STATUSES = [
  "pending",
  "queued",
  "classified",
  "partial",
  "stale",
  "complete",
  "failed",
] as const;

export const PROCESSING_STATUSES = [
  "uploaded",
  "queued",
  "processing",
  "processed",
  "failed",
] as const;

/** One row of the contract table (identical shape from both endpoints). */
export type ContractRow = {
  id: string;
  filename: string;
  contract_type?: string | null;
  contract_category: string;
  counterparty?: string | null;
  processing_status: string;
  analysis_status: string;
  semantic_index_status?: string;
  is_important: boolean;
  is_archived?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  /** Search results carry a relevance score; the listing does not. */
  score?: number;
};

export type Tone = "ok" | "warn" | "bad" | "info" | "brand" | "neutral";

export type Filters = {
  category: string; // "" = any
  analysis: string; // "" = any
  processing: string; // "" = any
  important: boolean; // true = starred only
  highRisk: boolean; // KPI filter
  renewingWithinDays: number | null; // KPI filter
  includeArchived: boolean;
  sort: SortKey;
  page: number;
  pageSize: number;
};

export const DEFAULT_FILTERS: Filters = {
  category: "",
  analysis: "",
  processing: "",
  important: false,
  highRisk: false,
  renewingWithinDays: null,
  includeArchived: false,
  sort: "newest",
  page: 1,
  pageSize: 10,
};

/** Filters that are "on" — drives the active-filter chips and Reset state. */
export function activeFilterCount(f: Filters): number {
  return (
    (f.category ? 1 : 0) +
    (f.analysis ? 1 : 0) +
    (f.processing ? 1 : 0) +
    (f.important ? 1 : 0) +
    (f.highRisk ? 1 : 0) +
    (f.renewingWithinDays !== null ? 1 : 0) +
    (f.includeArchived ? 1 : 0)
  );
}

export function isDefaultFilters(f: Filters): boolean {
  return activeFilterCount(f) === 0 && f.sort === DEFAULT_FILTERS.sort;
}

/**
 * Query parameters shared by the PostgreSQL listing and the Elasticsearch
 * search endpoint. Pagination is expressed as page/limit for the listing and
 * as offset/limit for search — both derived from the same `page`.
 */
export function filterParams(f: Filters): Record<string, string> {
  const p: Record<string, string> = {};
  if (f.category) p.category = f.category;
  if (f.analysis) p.analysis_status = f.analysis;
  if (f.processing) p.processing_status = f.processing;
  if (f.important) p.important = "true";
  if (f.highRisk) p.high_risk = "true";
  if (f.renewingWithinDays !== null) p.renewing_within_days = String(f.renewingWithinDays);
  if (f.includeArchived) p.include_archived = "true";
  return p;
}

export function pageParams(f: Filters): Record<string, string> {
  return { page: String(f.page), limit: String(f.pageSize) };
}

export function searchPageParams(f: Filters): Record<string, string> {
  return {
    offset: String((f.page - 1) * f.pageSize),
    limit: String(f.pageSize),
    sort: f.sort,
  };
}

/* ------------------------------------------------------------------ labels */

/**
 * Readable processing state — never a raw enum, never an unexplained code.
 * "Ready" only ever means PDF parsing finished; it says nothing about risk.
 */
export function processingLabel(status: string | null | undefined): string {
  switch (status) {
    case "processed":
      return "Ready";
    case "uploaded":
      return "Uploaded";
    case "queued":
    case "processing":
      return "Processing";
    case "failed":
      return "Failed";
    default:
      return "Processing";
  }
}

export function processingTone(status: string | null | undefined): Tone {
  switch (status) {
    case "processed":
      return "ok";
    case "failed":
      return "bad";
    case "uploaded":
    case "queued":
    case "processing":
      return "warn";
    default:
      return "info";
  }
}

/**
 * The authoritative analysis state, expressed in plain language.
 *
 * Deliberate rule: PDF processing completing is NOT analysis. A contract that
 * is only parsed reads "Analysis incomplete" until the risk analysis says
 * otherwise — "Analysis complete" is reserved for analysis_status=complete.
 */
export function analysisLabel(
  processing: string | null | undefined,
  analysis: string | null | undefined
): string {
  if (processing === "failed") return "Failed";
  // Only claim work is in flight when the API actually says so: an uploaded
  // document nobody has queued yet is not "processing", it is waiting.
  if (processing === "queued" || processing === "processing") return "Processing";
  if (processing !== "processed") return "Not processed yet";
  switch (analysis) {
    case "complete":
      return "Analysis complete";
    case "failed":
      return "Failed";
    case "queued":
      return "Processing";
    case "pending":
    case "classified":
    case "partial":
    case "stale":
      return "Analysis incomplete";
    default:
      return "Analysis incomplete";
  }
}

export function analysisTone(
  processing: string | null | undefined,
  analysis: string | null | undefined
): Tone {
  const label = analysisLabel(processing, analysis);
  if (label === "Analysis complete") return "ok";
  if (label === "Failed") return "bad";
  if (label === "Processing") return "warn";
  return "info";
}

/** Semantic (Qdrant) index state, in user-facing words. */
export function indexLabel(status: string | null | undefined): string {
  switch (status) {
    case "indexed":
      return "Indexed";
    case "failed":
      return "Indexing failed";
    case "queued":
    case "processing":
      return "Indexing…";
    case "pending":
      return "Not indexed";
    default:
      return "Not indexed";
  }
}

export function indexTone(status: string | null | undefined): Tone {
  switch (status) {
    case "indexed":
      return "ok";
    case "failed":
      return "bad";
    case "queued":
    case "processing":
      return "warn";
    default:
      return "neutral";
  }
}

/** Risk level of the analysis as a whole, for the Risk column. */
export function categoryTone(category: string | null | undefined): Tone {
  if (!category || category === UNSPECIFIED) return "neutral";
  return "brand";
}

/** Short, human date — stable across locales (ISO-like, no timezone guessing). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Build a `?query` string from a param map, skipping empty values. */
export function qs(params: Record<string, string | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** Deep links straight into a section of the contract detail page. */
export function summaryHref(contractId: string): string {
  return `/contracts/${contractId}?view=summary`;
}

export function riskHref(contractId: string): string {
  return `/contracts/${contractId}?view=risk`;
}
