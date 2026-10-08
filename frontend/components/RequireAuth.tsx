"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { safeInternalPath, useAuth } from "@/lib/auth";

/**
 * Central protected-route guard (Part 3).
 *
 * Wraps every authenticated page. Children are never mounted while the session
 * is unverified, so:
 *   - no protected page can render its shell for a logged-out visitor,
 *   - no page-level `fetch` fires (and no "Failed to load…" flash) before auth
 *     is resolved,
 *   - sensitive content never blinks during the redirect.
 *
 * Unauthenticated -> /login?next=<current internal path> (single replace, no
 * redirect loop). Auth resolution errors get a visible retry instead of an
 * endless spinner.
 */

function SessionChecking() {
  return (
    <div
      className="card p-10 text-center text-sm text-ink-mute"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="inline-block h-4 w-4 mr-2 align-[-2px] animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
      Checking session…
    </div>
  );
}

function SessionProblem({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="card p-10 text-center" role="alert">
      <p className="font-semibold text-slate-800">We couldn&apos;t verify your session</p>
      <p className="mt-1 text-sm text-ink-mute">{message}</p>
      <div className="mt-4 flex justify-center gap-3">
        <button className="btn-primary" onClick={onRetry}>
          Try again
        </button>
        <a className="btn-secondary" href="/login">
          Go to login
        </a>
      </div>
    </div>
  );
}

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status, error, refreshUser } = useAuth();
  const router = useRouter();
  const redirected = useRef(false);

  useEffect(() => {
    if (status !== "unauthenticated" || redirected.current) return;
    redirected.current = true;
    const here =
      typeof window !== "undefined"
        ? window.location.pathname + window.location.search
        : "/dashboard";
    const next = safeInternalPath(here);
    router.replace(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
  }, [status, router]);

  if (status === "authenticated") return <>{children}</>;
  if (status === "error") {
    return <SessionProblem message={error} onRetry={() => void refreshUser()} />;
  }
  // "loading", or "unauthenticated" while the single redirect above lands.
  return <SessionChecking />;
}
