"use client";
import React from "react";
import Link from "next/link";

/* ---------------------------------------------------------------- loading */

export function Spinner({ label }: { label?: string }) {
  return (
    <span
      className="inline-flex items-center gap-2 text-sm text-ink-soft"
      role="status"
      aria-live="polite"
    >
      <span className="ui-spinner" aria-hidden="true" />
      {label ?? "Loading…"}
    </span>
  );
}

/** Shimmering placeholder block — used wherever real content is loading. */
export function Skeleton({
  className = "h-4 w-32",
}: {
  className?: string;
}) {
  return <span className={`skeleton inline-block ${className}`} aria-hidden="true" />;
}

export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="card overflow-hidden" role="status" aria-live="polite">
      <span className="sr-only">Loading contracts…</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div
          key={r}
          className="flex items-center gap-4 border-b border-line-soft px-4 py-4 last:border-b-0"
        >
          {Array.from({ length: cols }).map((__, c) => (
            <Skeleton
              key={c}
              className={`h-3.5 ${c === 1 ? "flex-1" : "w-24"} ${
                r % 2 ? "opacity-70" : ""
              }`}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ badges */

const TONE: Record<string, string> = {
  ok: "bg-emerald-50 text-emerald-800 border-emerald-200",
  warn: "bg-amber-50 text-amber-800 border-amber-200",
  bad: "bg-red-50 text-red-800 border-red-200",
  info: "bg-slate-100 text-slate-700 border-slate-200",
  brand: "bg-brand-50 text-brand-800 border-brand-200",
  neutral: "bg-cream-100 text-ink-soft border-line-strong",
};

export type ToneKey = keyof typeof TONE;

export function StatusBadge({
  tone = "info",
  children,
  title,
}: {
  tone?: ToneKey | string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span className={`badge ${TONE[tone] ?? TONE.info}`} title={title}>
      {children}
    </span>
  );
}

export function statusToneFor(status: string): ToneKey {
  const s = (status || "").toLowerCase();
  if (["processed", "completed", "approved", "active", "done", "ready", "indexed", "complete"].includes(s))
    return "ok";
  if (["queued", "processing", "uploading", "pending", "running", "classified", "partial", "stale"].includes(s))
    return "warn";
  if (["failed", "rejected", "error", "overdue", "processing_failed"].includes(s)) return "bad";
  return "info";
}

/** User-facing wording for one stage of the analysis lifecycle
 *  (processing → classification → risk analysis). Never says "complete"
 *  unless the backend state is `complete`. */
export function analysisLabel(state?: string | null): string {
  switch (state ?? "") {
    case "complete": return "✓ Complete";
    case "failed": return "✗ Failed";
    case "running": return "⏳ In progress…";
    case "partial": return "◐ Partial";
    case "classified": return "◐ Classified";
    case "classified_only": return "◐ Classified only";
    case "processing_failed": return "✗ Processing failed";
    case "stale": return "↻ Stale";
    case "pending": return "⏳ Pending";
    case "queued": return "⏳ Queued…";
    case "not_ready": case "not_run": case "not_started": return "Not started";
    default: return state || "Not started";
  }
}

/** Short, human wording for a clause's analysis state (backend decides). */
export function clauseStateLabel(state?: string | null): string {
  switch (state ?? "needs_review") {
    case "high_risk": return "High risk";
    case "medium_risk": return "Medium risk";
    case "low_risk": return "Low risk";
    case "no_detected_risk": return "No detected risk";
    default: return "Needs review";
  }
}

/** User-facing wording for the background search-indexing state
 *  (pending → queued → processing → indexed | failed). */
export function indexLabel(status?: string | null): string {
  switch (status ?? "pending") {
    case "indexed": return "Ready for search";
    case "failed": return "Indexing failed";
    case "processing": return "Indexing…";
    case "queued": return "Queued…";
    default: return "Preparing…";
  }
}

/* ------------------------------------------------------------------ states */

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="card border-dashed p-10 text-center">
      <p className="font-serif text-lg font-semibold text-ink">{title}</p>
      {hint && <p className="mx-auto mt-1.5 max-w-lg text-sm text-ink-mute">{hint}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-brand-200 bg-brand-50 p-4 text-sm" role="alert">
      <p className="font-semibold text-brand-800">Something went wrong</p>
      <p className="mt-1 text-brand-700">{message}</p>
      {onRetry && (
        <button className="btn btn-secondary btn-sm mt-3" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Card({ title, action, children }: {
  title: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="card p-4" aria-label={title}>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-serif text-lg font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** Page heading block: eyebrow + serif title + description (+ actions). */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="font-serif text-3xl font-bold tracking-tight text-ink md:text-4xl">
          {title}
        </h1>
        {description && <div className="lede mt-2">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* --------------------------------------------------------------- controls */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "gold";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`btn btn-${variant} ${size === "sm" ? "btn-sm" : ""} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className="ui-spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

/** One removable active-filter chip. */
export function FilterChip({
  label,
  value,
  onRemove,
}: {
  label: string;
  value?: string;
  onRemove: () => void;
}) {
  return (
    <span className="chip">
      <span className="font-semibold text-ink">{label}</span>
      {value && <span>{value}</span>}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}${value ? ` ${value}` : ""}`}
        className="ml-0.5 grid h-5 w-5 place-items-center rounded-full text-ink-mute transition hover:bg-cream-200 hover:text-ink"
      >
        ✕
      </button>
    </span>
  );
}

/* ------------------------------------------------------------------- KPIs */

export function KpiCard({
  label,
  value,
  caption,
  hint,
  active = false,
  onClick,
  testId,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  caption?: string;
  hint?: string;
  active?: boolean;
  onClick?: () => void;
  testId?: string;
  tone?: "default" | "brand" | "gold";
}) {
  const accent =
    tone === "brand" ? "text-brand-800" : tone === "gold" ? "text-gold-700" : "text-ink";
  const inner = (
    <>
      <span className="eyebrow block">{label}</span>
      <span className={`mt-2 block font-serif text-4xl font-bold leading-none ${accent}`}>
        {value}
      </span>
      {caption && <span className="mt-2 block text-xs text-ink-mute">{caption}</span>}
      {hint && (
        <span className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-brand-700 opacity-0 transition group-hover:opacity-100">
          {hint} →
        </span>
      )}
    </>
  );
  const cls = `card card-hover group flex flex-col justify-between p-5 text-left ${
    active ? "border-brand-600 ring-1 ring-brand-600" : ""
  } ${onClick ? "cursor-pointer" : ""}`;

  if (!onClick) {
    return (
      <div className={cls} data-testid={testId}>
        {inner}
      </div>
    );
  }
  return (
    <button type="button" className={cls} onClick={onClick} data-testid={testId}>
      {inner}
    </button>
  );
}

/* ------------------------------------------------------------- pagination */

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  pageSizes = [10, 25, 50],
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  onPageSize?: (n: number) => void;
  pageSizes?: number[];
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <p className="text-xs text-ink-mute" aria-live="polite">
        Showing <span className="font-semibold text-ink-soft">{from}–{to}</span> of{" "}
        <span className="font-semibold text-ink-soft">{total}</span> contract
        {total === 1 ? "" : "s"}
      </p>
      <div className="flex items-center gap-3">
        {onPageSize && (
          <label className="flex items-center gap-1.5 text-xs text-ink-mute">
            Per page
            <select
              className="field w-16 py-1 text-xs"
              value={pageSize}
              onChange={(e) => onPageSize(Number(e.target.value))}
              aria-label="Contracts per page"
            >
              {pageSizes.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onPage(page - 1)}
            disabled={page <= 1}
            aria-label="Previous page"
          >
            ← Prev
          </button>
          <span className="px-2 text-xs text-ink-soft">
            Page {page} of {pages}
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onPage(page + 1)}
            disabled={page >= pages}
            aria-label="Next page"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  );
}

export function ProgressSteps({ steps }: { steps: { label: string; state: "done" | "active" | "todo" }[] }) {
  return (
    <ol className="space-y-1 text-sm" aria-live="polite">
      {steps.map((s) => (
        <li key={s.label} className="flex items-center gap-2">
          <span aria-hidden="true">
            {s.state === "done" ? "✓" : s.state === "active" ? "●" : "○"}
          </span>
          <span className={s.state === "todo" ? "text-ink-mute" : s.state === "active" ? "font-semibold" : ""}>
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------ navigation */

/**
 * Breadcrumbs (Phase 13) — always starting at Home, ending at the current
 * page which is announced with `aria-current="page"` instead of being a link
 * to itself.
 */
export function Breadcrumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex flex-wrap items-center gap-1.5 text-xs text-ink-mute">
        <li className="flex items-center gap-1.5">
          <Link href="/" className="inline-flex min-h-6 items-center py-1 hover:text-brand-800 hover:underline underline-offset-2">
            Home
          </Link>
        </li>
        {items.map((it) => (
          <li key={it.label} className="flex items-center gap-1.5">
            <span aria-hidden="true">/</span>
            {it.href ? (
              <Link
                href={it.href}
                className="inline-flex min-h-6 items-center py-1 hover:text-brand-800 hover:underline underline-offset-2"
              >
                {it.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-ink-soft">
                {it.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/**
 * In-page tabs (Phases 10–11).
 *
 * Used to fold the old "Approved Templates" and "Company Policies"
 * destinations into the single Templates / Policies pages: they are views of
 * the same resource, not separate routes, so they are tabs rather than
 * extra nav entries.
 *
 * Implements the ARIA tabs pattern: roving focus with arrow keys, Home/End,
 * and the panel is wired through `aria-controls`.
 */
export function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { key: T; label: string; count?: number }[];
  value: T;
  onChange: (key: T) => void;
  label: string;
}) {
  const idx = Math.max(
    0,
    tabs.findIndex((t) => t.key === value)
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    let next = -1;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onChange(tabs[next].key);
    const el = document.getElementById(`tab-${tabs[next].key}`);
    el?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="flex flex-wrap items-end gap-1 border-b border-line"
    >
      {tabs.map((t) => {
        const active = t.key === value;
        return (
          <button
            key={t.key}
            id={`tab-${t.key}`}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={`tabpanel-${t.key}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold transition ${
              active
                ? "border-brand-700 text-brand-800"
                : "border-transparent text-ink-soft hover:border-line-strong hover:text-ink"
            }`}
          >
            {t.label}
            {typeof t.count === "number" && (
              <span className="ml-1.5 text-xs font-normal text-ink-mute">{t.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  tabKey,
  active,
  children,
}: {
  tabKey: string;
  active: boolean;
  children: React.ReactNode;
}) {
  if (!active) return null;
  return (
    <div
      role="tabpanel"
      id={`tabpanel-${tabKey}`}
      aria-labelledby={`tab-${tabKey}`}
      tabIndex={0}
    >
      {children}
    </div>
  );
}
