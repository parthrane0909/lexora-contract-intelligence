"use client";

// Contract DISCOVERY search client (Part 2).
//
// This talks to the Elasticsearch-backed contract search API:
//     GET /api/v1/search/contracts?q=...
// It answers "which contract/file am I looking for?".
//
// It is a SEPARATE system from semantic search, which answers "which clauses
// mean something similar?" and goes through MiniLM-L6-v2 → Qdrant → cosine:
//     POST /api/v1/retrieval/search   (see app/search/page.tsx)
// Never mix them: this module never generates an embedding, and the retrieval
// endpoint never uses Elasticsearch.

import { API_URL, ApiError, authHeaders, messageForStatus } from "@/lib/api";

/**
 * Debounce window for search-as-you-type (Step 12).
 *
 * 280 ms sits in the recommended 200–350 ms band: fast enough that results
 * feel attached to the typing, slow enough that one word ("balanced" ≈ 8
 * keystrokes) produces a single HTTP request instead of eight. Enter (see
 * useContractSearch.submit) bypasses the wait.
 */
export const SEARCH_DEBOUNCE_MS = 280;

/**
 * Everything besides `q` that shapes one search request.
 *
 * The filter/sort/page vocabulary is IDENTICAL to the PostgreSQL listing
 * (GET /api/v1/contracts) so switching between the two paths — empty query
 * uses the listing, non-empty uses Elasticsearch — never changes what a
 * filter means.
 */
export type SearchOptions = {
  category?: string;
  analysis_status?: string;
  processing_status?: string;
  important?: boolean;
  high_risk?: boolean;
  renewing_within_days?: number | null;
  include_archived?: boolean;
  sort?: string;
  limit?: number;
  offset?: number;
};

export type ContractHit = {
  contract_id: string;
  filename: string;
  counterparty?: string | null;
  contract_type?: string | null;
  contract_category?: string | null;
  is_important?: boolean;
  processing_status: string;
  analysis_status: string;
  semantic_index_status?: string;
  is_archived?: boolean;
  created_at?: string | null;
  /** Relevance only — not a legal or risk score. */
  score: number;
};

export type SearchResponse = {
  query: string;
  total: number;
  results: ContractHit[];
  limit?: number;
  offset?: number;
  sort?: string;
  took_ms?: number | null;
  index?: string | null;
};

function searchUrl(query: string, options?: SearchOptions): string {
  const sp = new URLSearchParams();
  sp.set("q", query);
  const o = options ?? {};
  if (o.category) sp.set("category", o.category);
  if (o.analysis_status) sp.set("analysis_status", o.analysis_status);
  if (o.processing_status) sp.set("processing_status", o.processing_status);
  if (o.important !== undefined && o.important !== null) sp.set("important", String(o.important));
  if (o.high_risk) sp.set("high_risk", "true");
  if (o.renewing_within_days !== undefined && o.renewing_within_days !== null) {
    sp.set("renewing_within_days", String(o.renewing_within_days));
  }
  if (o.include_archived) sp.set("include_archived", "true");
  if (o.sort) sp.set("sort", o.sort);
  if (o.limit !== undefined) sp.set("limit", String(o.limit));
  if (o.offset !== undefined) sp.set("offset", String(o.offset));
  return `${API_URL}/api/v1/search/contracts?${sp.toString()}`;
}


/**
 * idle     → no query: the normal PostgreSQL-backed contract listing
 * typing   → a query is pending its debounce (no request yet)
 * searching→ a request is in flight
 * results  → at least one contract matched
 * empty    → the search ran and nothing matched
 * error    → the search backend failed (nothing is shown as fresh)
 */
export type SearchStatus =
  | "idle"
  | "typing"
  | "searching"
  | "results"
  | "empty"
  | "error";

/** Run one contract search request. Aborts propagate as-is (AbortError), so
 *  callers can tell cancellation apart from failure. */
export async function fetchContractSearch(
  query: string,
  options?: SearchOptions,
  signal?: AbortSignal
): Promise<SearchResponse> {
  let res: Response;
  try {
    res = await fetch(searchUrl(query, options), {
      headers: authHeaders(),
      signal,
    });
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") throw e; // cancelled
    throw new ApiError(0, "Unable to reach the backend.");
  }

  if (res.status === 503) {
    // Search-specific failure: surface the backend's own explanation.
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch { /* fall through to the generic wording */ }
    throw new ApiError(503, detail || "Search is temporarily unavailable. Retry.");
  }
  if (res.status === 401) throw new ApiError(401, "Please log in again.");
  if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));

  try {
    return (await res.json()) as SearchResponse;
  } catch {
    throw new ApiError(res.status, "Received an unexpected response from the server.");
  }
}
