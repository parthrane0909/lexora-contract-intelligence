"use client";

// Single source of truth for the frontend API URL.
// NEXT_PUBLIC_* is inlined by Next.js at BUILD time, so the value must be
// present during `next build` (via compose build environment or .env).
// There is intentionally NO fallback to a hardcoded URL: a missing/invalid
// configuration must fail loudly at build time, never silently point the
// browser at an unrelated service.
function resolveApiUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not set. The frontend cannot determine the API " +
      "endpoint. Set it at build time (e.g. NEXT_PUBLIC_API_URL=http://localhost:8001)."
    );
  }
  // Guard against the known-bad default that previously pointed at an
  // unrelated service on this machine.
  if (url.includes("localhost:8000")) {
    throw new Error(
      `NEXT_PUBLIC_API_URL resolves to ${url}, which is not the legal backend. ` +
      "Set it to the host-mapped legal API port (e.g. http://localhost:8001)."
    );
  }
  return url;
}

// Evaluated once at module load; a misconfiguration fails the render
// immediately with a clear message instead of making broken requests.
export const API_URL: string = resolveApiUrl();

export function authHeaders(): HeadersInit {
  if (typeof window === "undefined") return {};
  const t = localStorage.getItem("token");
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** User-friendly message for an HTTP status, without leaking internals. */
export function messageForStatus(status: number): string {
  switch (status) {
    case 401: return "Please log in again.";
    case 403: return "You don't have permission to access this resource.";
    case 404: return "Requested resource was not found.";
    case 422: return "The request was invalid.";
    default:
      if (status >= 500) return "Something went wrong on the server.";
      return `Request failed (HTTP ${status}).`;
  }
}

export async function api(path: string, init?: RequestInit) {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, "Unable to reach the backend.");
  }
  if (!res.ok) {
    let detail = "";
    try { detail = await res.text(); } catch { /* ignore */ }
    throw new ApiError(res.status, messageForStatus(res.status));
  }
  try {
    return await res.json();
  } catch {
    throw new ApiError(res.status, "Received an unexpected response from the server.");
  }
}
