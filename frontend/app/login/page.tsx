"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { safeInternalPath, useAuth } from "@/lib/auth";

export default function Login() {
  const r = useRouter();
  const { status, login } = useAuth();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [next, setNext] = useState("/dashboard");

  // The route guard hands back where the user was headed: /login?next=/portfolio.
  // safeInternalPath() rejects absolute/protocol-relative URLs (open redirect).
  useEffect(() => {
    const p = safeInternalPath(new URLSearchParams(window.location.search).get("next"));
    setNext(p ?? "/dashboard");
  }, []);

  // Already authenticated (existing session, or the login that just succeeded)
  // -> leave the login page instead of showing a stale form.
  useEffect(() => {
    if (status === "authenticated") r.replace(next);
  }, [status, next, r]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return; // ignore duplicate submits while in flight
    setErr("");
    setBusy(true);
    try {
      const res = await login(email, pw);
      if (!res.ok) {
        setErr(res.message);
        setBusy(false);
        return;
      }
      r.replace(next);
    } catch {
      setErr("Cannot reach the server. Check your connection and retry.");
      setBusy(false);
    }
  }

  const resolving = status === "loading" || status === "authenticated";

  return (
    <main className="grid md:grid-cols-2 gap-0 card overflow-hidden" aria-label="Login">
      <div
        className="p-8 md:p-12 text-white flex flex-col justify-between min-h-[320px]"
        style={{ background: "linear-gradient(135deg, #5c0a12 0%, #7f1d1d 60%, #991b1b 100%)" }}
      >
        <p className="font-serif text-2xl font-bold">⚖ Contract Intelligence</p>
        <div>
          <p className="font-serif text-3xl md:text-4xl font-bold leading-tight">
            Know every risk<br />before you sign.
          </p>
          <p className="mt-3 text-red-100 text-sm max-w-sm">
            AI-assisted contract review — classification, risk detection, evidence
            and human oversight, in one workspace.
          </p>
          <ul className="mt-4 text-xs text-red-100 space-y-1" aria-label="Capabilities">
            <li>✓ Clause-level risk findings with evidence</li>
            <li>✓ Policy and template comparison</li>
            <li>✓ Human review ledger and audit trail</li>
          </ul>
        </div>
        <p className="text-xs text-red-200">Decision support for legal review — not legal advice.</p>
      </div>
      <div className="p-8 md:p-12">
        <h1 className="font-serif text-3xl font-bold">Welcome back</h1>
        <p className="text-sm text-ink-mute mt-1">Sign in to your organization workspace.</p>
        {resolving ? (
          <p className="mt-6 text-sm text-ink-mute" role="status" aria-live="polite">
            <span className="inline-block h-4 w-4 mr-2 align-[-2px] animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
            Checking session…
          </p>
        ) : (
        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium">Email</label>
            <input id="email" type="email" required autoComplete="email"
              className="mt-1 w-full border p-2 rounded" placeholder="you@company.com"
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium">Password</label>
            <input id="password" type="password" required autoComplete="current-password"
              className="mt-1 w-full border p-2 rounded" placeholder="••••••••"
              value={pw} onChange={(e) => setPw(e.target.value)} />
          </div>
          {err && <p className="text-red-600 text-sm" role="alert">{err}</p>}
          <button className="btn-primary w-full" disabled={busy} aria-busy={busy}>
            {busy ? "Signing in…" : "Log in"}
          </button>
        </form>
        )}
        <p className="text-sm text-ink-mute mt-4">
          Don&apos;t have an account? <Link className="underline" href="/register">Sign up</Link>
        </p>
      </div>
    </main>
  );
}
