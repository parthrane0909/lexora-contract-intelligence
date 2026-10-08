"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  ContractHit,
  SEARCH_DEBOUNCE_MS,
  SearchOptions,
  SearchStatus,
  fetchContractSearch,
} from "@/lib/contractSearch";

/**
 * Live contract search state machine (Part 2, Steps 12–16).
 *
 * Guarantees implemented here:
 *  - DEBOUNCE: one request per pause in typing (SEARCH_DEBOUNCE_MS), never one
 *    per keystroke;
 *  - RACE SAFETY: every run gets a monotonically increasing id AND aborts the
 *    previous request with AbortController. A late response for "bal" can
 *    neither abort nor overwrite the newer "balanced" result (Step 13);
 *  - STATES: idle / typing / searching / results / empty / error, all visible;
 *  - EMPTY QUERY: cancels everything and restores the normal listing — an
 *    empty string is never sent to the search backend (Step 15);
 *  - ERROR: results are cleared, so older rows are never presented as fresh.
 *
 * Callers may pass a `getOptions` callback (filters, sort, paging window) —
 * it is read fresh for every request, and `optionsKey` re-runs the current
 * query the moment those options change, so a filter never has to wait for
 * another keystroke to take effect.
 */
export function useContractSearch(
  getOptions?: () => SearchOptions,
  optionsKey?: string
) {
  const [status, setStatus] = useState<SearchStatus>("idle");
  const [query, setQueryState] = useState("");
  const [results, setResults] = useState<ContractHit[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [tookMs, setTookMs] = useState<number | null>(null);

  const queryRef = useRef(""); // always-current text (no stale closures)
  const runQueryRef = useRef(""); // query the in-flight run belongs to
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef(0); // stale responses lose the race by id
  const optionsRef = useRef(getOptions);
  optionsRef.current = getOptions;

  const run = useCallback(async (raw: string) => {
    const q = raw.trim();
    if (!q) return;
    const runId = ++runIdRef.current;
    abortRef.current?.abort(); // supersede whatever is still in flight
    const controller = new AbortController();
    abortRef.current = controller;
    runQueryRef.current = q;
    setStatus("searching");
    setError("");
    try {
      const data = await fetchContractSearch(
        q,
        optionsRef.current?.(),
        controller.signal
      );
      if (runId !== runIdRef.current) return; // a newer query already won
      setResults(data.results);
      setTotal(data.total);
      setTookMs(data.took_ms ?? null);
      setStatus(data.results.length > 0 ? "results" : "empty");
    } catch (e) {
      // Cancellation is not a failure: the newer run owns the state now.
      if (controller.signal.aborted || runId !== runIdRef.current) return;
      setResults(null); // never present older rows as if they were fresh
      setTotal(null);
      setTookMs(null);
      setError(e instanceof ApiError ? e.message : "Search failed. Please retry.");
      setStatus("error");
    }
  }, []);

  const setQuery = useCallback(
    (value: string) => {
      setQueryState(value);
      queryRef.current = value;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      const trimmed = value.trim();
      if (!trimmed) {
        // Step 15: empty query → cancel pending work, restore normal listing.
        runIdRef.current += 1;
        abortRef.current?.abort();
        abortRef.current = null;
        setResults(null);
        setTotal(null);
        setError("");
        setTookMs(null);
        setStatus("idle");
        return;
      }
      if (trimmed !== runQueryRef.current) {
        // The query CHANGED: cancel the in-flight request outright and mark
        // its response stale, so a slow answer for the previous text can
        // never be applied (or labelled) as the current result.
        runIdRef.current += 1;
        abortRef.current?.abort();
        abortRef.current = null;
      }
      setStatus("typing");
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        void run(value);
      }, SEARCH_DEBOUNCE_MS);
    },
    [run]
  );

  /** Enter (or any deliberate action): run now, skipping the debounce. */
  const submit = useCallback(
    (value: string) => {
      if (!value.trim()) return;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      void run(value);
    },
    [run]
  );

  const rerun = useCallback(() => {
    const q = queryRef.current.trim();
    if (q) void run(q);
  }, [run]);

  const setQueryStable = setQuery;
  const clear = useCallback(() => setQueryStable(""), [setQueryStable]);

  // Unmount: drop the timer and abort any in-flight request.
  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      abortRef.current?.abort();
    },
    []
  );

  // A control changed (filter / sort / paging) rather than the text: re-run
  // the current query immediately — no debounce, because nobody is typing.
  const lastKeyRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const previous = lastKeyRef.current;
    lastKeyRef.current = optionsKey;
    if (previous === undefined || previous === optionsKey) return;
    const q = queryRef.current.trim();
    if (!q) return;
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    void run(q);
  }, [optionsKey, run]);

  return {
    status,
    query,
    results,
    total, // server-reported match count (may exceed results.length)
    error,
    tookMs, // server-side search time, kept for callers that want to show it
    setQuery,
    clear,
    submit,
    rerun,
    active: status !== "idle",
  };
}

/** Convenience type for consumers. */
export type ContractSearch = ReturnType<typeof useContractSearch>;
