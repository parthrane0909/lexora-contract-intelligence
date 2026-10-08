"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";
import { safeInternalPath, useAuth } from "@/lib/auth";

export default function Register() {
  const r = useRouter();
  const { status, adoptToken } = useAuth();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [org, setOrg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [next, setNext] = useState("/dashboard");

  useEffect(() => {
    const p = safeInternalPath(new URLSearchParams(window.location.search).get("next"));
    setNext(p ?? "/dashboard");
  }, []);

  // Already signed in -> don't show the sign-up form over a live session.
  useEffect(() => {
    if (status === "authenticated") r.replace(next);
  }, [status, next, r]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return; // ignore duplicate submits while in flight
    setErr("");
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: pw, org_name: org }),
      });
      if (!res.ok) {
        setErr(await res.text());
        setBusy(false);
        return;
      }
      const j = await res.json();
      // Same central session path as login: token -> storage -> GET /auth/me.
      const out = await adoptToken(j.access_token);
      if (!out.ok) {
        setErr(out.message);
        setBusy(false);
        return;
      }
      r.replace(next);
    } catch {
      setErr("Cannot reach the server. Check your connection and retry.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <h1 className="text-2xl font-bold">Register + create organization</h1>
      <input className="w-full border p-2 rounded" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input className="w-full border p-2 rounded" placeholder="password (8+ chars)" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
      <input className="w-full border p-2 rounded" placeholder="organization" value={org} onChange={(e) => setOrg(e.target.value)} />
      {err && <p className="text-red-600 text-sm">{err}</p>}
      <button className="px-4 py-2 bg-slate-900 text-white rounded disabled:opacity-60" disabled={busy} aria-busy={busy}>
        {busy ? "Creating…" : "Register"}
      </button>
    </form>
  );
}
