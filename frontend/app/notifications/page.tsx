"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useEffect, useState } from "react";
import {
  Breadcrumbs,
  EmptyState,
  ErrorState,
  Spinner,
} from "@/components/ui";
import Link from "next/link";
import { API_URL, authHeaders } from "@/lib/api";

type N = {
  id: string; contract_id?: string | null; obligation_id?: string | null;
  lifecycle_event_id?: string | null; notification_type: string; title: string;
  body?: string | null; event_date?: string | null; window_days?: number | null;
  status: string; is_read: boolean; created_at: string;
};

export default function NotificationsPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <NotificationsView />
    </RequireAuth>
  );
}

function NotificationsView() {
  // `null` = still loading, so the page shows a real loading state instead of
  // a misleading "No notifications."; `err` is retryable.
  const [items, setItems] = useState<N[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<"all" | "unread" | "overdue">("all");
  const [msg, setMsg] = useState("");

  async function load() {
    setLoading(true);
    setErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/notifications`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setItems((await res.json()) as N[]);
    } catch {
      setErr("Could not load notifications.");
      setItems((i) => i ?? []);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function markRead(id: string) {
    await fetch(`${API_URL}/api/v1/notifications/${id}/read`, {
      method: "POST", headers: authHeaders(),
    });
    load();
  }

  async function readAll() {
    await fetch(`${API_URL}/api/v1/notifications/read-all`, {
      method: "POST", headers: authHeaders(),
    });
    load();
  }

  async function scan() {
    setMsg("Scanning…");
    const res = await fetch(`${API_URL}/api/v1/notifications/scan`, {
      method: "POST", headers: authHeaders(),
    });
    if (res.ok) {
      const s = await res.json();
      setMsg(`Scan: ${s.notifications_created} created, ${s.deliveries_sent} sent, ${s.deliveries_failed} failed.`);
    } else setMsg(`Scan failed: ${await res.text()}`);
    load();
  }

  const rows = items ?? [];
  const shown = rows.filter((n) => {
    if (filter === "unread") return !n.is_read;
    if (filter === "overdue") return n.notification_type === "OVERDUE";
    return true;
  });
  const unread = rows.filter((n) => !n.is_read).length;

  return (
    <main className="space-y-4">
      <Breadcrumbs items={[{ label: "Notifications" }]} />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-serif text-3xl font-bold tracking-tight text-ink">
          Notifications ({unread} unread)
        </h1>
        <button className="btn btn-secondary btn-sm" onClick={scan}>Run scan</button>
        <button className="btn btn-sm" onClick={readAll}>Mark all read</button>
        {(["all", "unread", "overdue"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={`chip ${filter === f ? "border-brand-600 bg-brand-50 text-brand-800" : ""}`}
          >
            {f}
          </button>
        ))}
      </div>
      {msg && <p className="text-sm text-ink-soft" role="status">{msg}</p>}
      {loading ? (
        <Spinner label="Loading notifications…" />
      ) : err ? (
        <ErrorState message={err} onRetry={load} />
      ) : shown.length === 0 ? (
        <EmptyState
          title="Nothing to show"
          hint={filter === "all" ? "No notifications for this organization." : "No notifications match this filter."}
        />
      ) : (
      shown.map((n) => (
        <div key={n.id} className={`border rounded p-3 text-sm ${n.is_read ? "opacity-70" : ""}`}>
          <p>
            <b>{n.title}</b>
            <span className="badge ml-2">{n.notification_type}</span>
            <span className="ml-2 text-xs text-ink-mute">{n.status}</span>
          </p>
          {n.body && <p className="text-ink-soft mt-1">{n.body}</p>}
          <p className="text-xs text-ink-mute mt-1">
            {n.event_date && <>Event date: {n.event_date} · </>}
            {n.window_days != null && <>Window: {n.window_days}d · </>}
            {new Date(n.created_at).toLocaleString()}
          </p>
          <div className="flex gap-3 mt-1 text-xs">
            {n.contract_id && (
              <Link className="underline" href={`/contracts/${n.contract_id}`}>Open contract lifecycle →</Link>
            )}
            {!n.is_read && <button className="underline" onClick={() => markRead(n.id)}>Mark read</button>}
          </div>
        </div>
      )))}
    </main>
  );
}
