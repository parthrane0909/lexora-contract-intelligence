"use client";
import { RequireAuth } from "@/components/RequireAuth";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { API_URL, ApiError, authHeaders, messageForStatus } from "@/lib/api";
import { useToast } from "@/components/Toast";
import {
  Breadcrumbs,
  Button,
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  TableSkeleton,
  TabBar,
  TabPanel,
  statusToneFor,
} from "@/components/ui";

type Tpl = {
  id: string;
  name: string;
  contract_type?: string | null;
  version: number;
  is_active: boolean;
  status: string;
  filename: string;
  page_count: number;
  clause_count: number;
  processing_error?: string | null;
  created_by?: string | null;
};

type Tab = "all" | "approved";

export default function TemplatesPage() {
  // Central auth guard (Part 3): renders only after the session is verified.
  return (
    <RequireAuth>
      <TemplatesView />
    </RequireAuth>
  );
}

/**
 * The single Templates destination (Phase 10).
 *
 * "Approved Templates" used to be a separate nav entry pointing at the same
 * list with `?view=approved`. It is now a tab inside this page: one route,
 * one heading, one table — the approved view is a filter over the same data
 * (active AND ready), so nothing about the underlying API changed.
 */
function TemplatesView() {
  const toast = useToast();
  const [templates, setTemplates] = useState<Tpl[] | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [tab, setTab] = useState<Tab>("all");
  const [name, setName] = useState("");
  const [ctype, setCtype] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadErr, setUploadErr] = useState("");

  // Deep link from the old /templates?view=approved entry keeps working.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("view") === "approved") {
      setTab("approved");
    }
  }, []);

  const load = useCallback(async () => {
    setLoadErr("");
    try {
      const res = await fetch(`${API_URL}/api/v1/templates`, { headers: authHeaders() });
      if (!res.ok) throw new ApiError(res.status, messageForStatus(res.status));
      setTemplates((await res.json()) as Tpl[]);
    } catch (e) {
      setTemplates((t) => t ?? []);
      setLoadErr(e instanceof ApiError ? e.message : "Could not reach the server.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    setUploadErr("");
    if (!file) return setUploadErr("Choose a PDF or DOCX file first.");
    if (!name.trim()) return setUploadErr("Enter a template name first.");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name.trim());
    if (ctype.trim()) fd.append("contract_type", ctype.trim());
    setUploading(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/templates/upload`, {
        method: "POST",
        headers: authHeaders(),
        body: fd,
      });
      if (!res.ok) {
        const detail = (await res.text()).trim().slice(0, 300);
        throw new ApiError(res.status, detail || messageForStatus(res.status));
      }
      const created = (await res.json()) as Tpl;
      toast.success(`“${created.name}” uploaded as v${created.version}`);
      setName("");
      setCtype("");
      setFile(null);
      await load();
    } catch (err) {
      const m = err instanceof ApiError ? err.message : "Upload failed.";
      setUploadErr(m);
      toast.error(m);
    } finally {
      setUploading(false);
      // reset the file input so the same file can be chosen again
      const input = document.getElementById("template-file") as HTMLInputElement | null;
      if (input) input.value = "";
    }
  }

  const approved =
    (templates ?? []).filter((t) => t.is_active && t.status === "ready") ?? [];

  const shown = tab === "approved" ? approved : templates ?? [];

  return (
    <main className="space-y-6">
      <Breadcrumbs items={[{ label: "Templates" }]} />

      <PageHeader
        eyebrow="Clause library"
        title="Templates"
        description={
          <>
            Every uploaded standard and its version history. A template that is
            both <strong>ready</strong> and <strong>active</strong> is approved
            for organizational use — switch tabs to review just those.
          </>
        }
        actions={
          <a href="#upload" className="btn btn-secondary">
            Upload template
          </a>
        }
      />

      <div className="space-y-4">
        <TabBar<Tab>
          label="Template views"
          value={tab}
          onChange={(k) => setTab(k)}
          tabs={[
            { key: "all", label: "All templates", count: templates?.length },
            { key: "approved", label: "Approved", count: approved.length },
          ]}
        />

        {/* ------------------------------------------------------- upload */}
        {tab === "all" && (
          <form
            id="upload"
            onSubmit={upload}
            className="card flex flex-wrap items-end gap-3 p-4"
          >
            <label className="text-sm">
              <span className="field-label">Name</span>
              <input
                className="field mt-1 w-64"
                placeholder="Standard NDA"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="text-sm">
              <span className="field-label">Contract type</span>
              <input
                className="field mt-1 w-56"
                placeholder="NDA"
                value={ctype}
                onChange={(e) => setCtype(e.target.value)}
              />
            </label>
            <label className="text-sm">
              <span className="field-label">Document</span>
              <input
                id="template-file"
                type="file"
                accept=".pdf,.docx"
                className="mt-1 block w-56 text-sm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <Button type="submit" variant="primary" loading={uploading}>
              {uploading ? "Uploading…" : "Upload PDF/DOCX"}
            </Button>
            {uploadErr && (
              <p className="w-full text-sm text-brand-700" role="alert">
                {uploadErr}
              </p>
            )}
          </form>
        )}

        {/* --------------------------------------------------- list state */}
        <TabPanel tabKey={tab} active>
          {loadErr ? (
            <ErrorState message={loadErr} onRetry={load} />
          ) : templates === null ? (
            <TableSkeleton rows={5} cols={6} />
          ) : shown.length === 0 ? (
            <EmptyState
              title={tab === "approved" ? "No approved templates yet" : "No templates yet"}
              hint={
                tab === "approved"
                  ? "A template appears here once it has finished processing and an admin activates it."
                  : "Upload a PDF or DOCX to start building your clause library."
              }
            />
          ) : (
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="table">
                  <caption className="sr-only">
                    Templates, with type, version, processing status and clause counts
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Type</th>
                      <th scope="col">Version</th>
                      <th scope="col">Status</th>
                      <th scope="col">Clauses</th>
                      <th scope="col">Uploaded by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((t) => (
                      <tr key={t.id}>
                        <td className="max-w-[18rem]">
                          <Link
                            href={`/templates/${t.id}`}
                            className="font-semibold text-ink underline-offset-2 hover:text-brand-800 hover:underline"
                          >
                            {t.name}
                          </Link>
                          <div className="mt-1 text-xs text-ink-mute">{t.filename}</div>
                        </td>
                        <td>{t.contract_type ?? "—"}</td>
                        <td>v{t.version}</td>
                        <td>
                          <StatusBadge tone={statusToneFor(t.status)} title="Processing status">
                            {t.status}
                          </StatusBadge>
                          {t.is_active && (
                            <span className="badge ml-1.5 border-emerald-200 bg-emerald-50 text-emerald-800">
                              Active
                            </span>
                          )}
                          {t.processing_error && (
                            <p className="mt-1 max-w-[16rem] text-xs text-brand-700">
                              {t.processing_error}
                            </p>
                          )}
                        </td>
                        <td>{t.clause_count}</td>
                        <td className="text-xs text-ink-soft">{t.created_by ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </TabPanel>
      </div>

      <p className="text-xs text-ink-mute">
        Templates supply clause text for comparison — they do not change any
        risk rule.{" "}
        <Link href="/policies" className="underline underline-offset-2">
          Policies
        </Link>{" "}
        hold your negotiating positions.
      </p>
    </main>
  );
}
