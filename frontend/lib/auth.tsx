"use client";

/**
 * Central authentication state (Part 3).
 *
 * One authoritative answer to "am I logged in, and as whom?" for the whole app.
 *
 * Token storage (unchanged from the existing architecture): the JWT issued by
 * `POST /api/v1/auth/login` lives in `localStorage["token"]` and is attached to
 * API calls by `authHeaders()` in lib/api.ts. This module never decodes the JWT
 * for identity — `GET /api/v1/auth/me` (backend) is the source of truth, so
 * profile/role/org always come from the server, not from client-held claims.
 *
 * Status machine:
 *   loading          -> session being resolved (token present, /auth/me in flight)
 *   authenticated    -> /auth/me returned 200 (user + org populated)
 *   unauthenticated  -> no token, or the backend said 401 (expired/invalid)
 *   error            -> we could not verify the session (network/server down, or
 *                       403 = valid token but no organization membership).
 *                       `error` is deliberately NOT "logged out": we must not
 *                       throw away a possibly-valid session because a request
 *                       failed, and it prevents redirect loops/spinners.
 *
 * 401 vs 403: only 401 clears the local session. 403 means "authenticated but
 * not permitted" — the user stays logged in (see `onUnauthorized` below).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export const TOKEN_KEY = "token";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  organization_id: string;
  role: string;
}

export interface AuthOrg {
  id: string;
  name: string;
}

export interface AuthResult {
  ok: boolean;
  status?: number;
  message: string;
}

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  org: AuthOrg | null;
  error: string;
  login(email: string, password: string): Promise<AuthResult>;
  adoptToken(token: string): Promise<AuthResult>;
  logout(): void;
  refreshUser(): Promise<AuthResult>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth() must be used inside <AuthProvider>");
  return ctx;
}

/** Never allow a token to outlive a failed verification. */
export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string) {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage unavailable (private mode): session simply won't persist */
  }
}

function clearToken() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Only internal, same-origin paths may be used as a post-login destination:
 * must start with a single "/", must not be "//host" or "/\host" (open
 * redirect), and must not point back at an auth page.
 */
export function safeInternalPath(raw: string | null): string | null {
  if (!raw) return null;
  if (!raw.startsWith("/")) return null;
  if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
  if (raw.startsWith("/login") || raw.startsWith("/register")) return null;
  return raw;
}

/** Fetch with a hard timeout so a dead backend can never hang a spinner. */
async function authFetch(path: string, token: string, timeoutMs = 12000): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(`${API_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(t);
  }
}

/**
 * One process-wide response watcher instead of editing every fetch call in the
 * app: any API response that is 401 (and is not the login/register call itself)
 * means the stored session is dead → clear it and mark unauthenticated, so the
 * route guard sends the user to /login. 403 responses are deliberately ignored
 * here: forbidden ≠ logged out.
 */
let watcherInstalled = false;
function installUnauthorizedWatcher(onUnauthorized: () => void) {
  if (typeof window === "undefined" || watcherInstalled) return;
  watcherInstalled = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const res = await original(input, init);
    try {
      if (res.status === 401) {
        const url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : (input as Request).url;
        const isApi = url.startsWith(API_URL);
        const isCredentialCall = /\/auth\/(login|register)($|\?)/.test(url);
        if (isApi && !isCredentialCall) onUnauthorized();
      }
    } catch {
      /* never let auth bookkeeping break a request */
    }
    return res;
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [org, setOrg] = useState<AuthOrg | null>(null);
  const [error, setError] = useState("");

  const discard = useCallback(() => {
    clearToken();
    setUser(null);
    setOrg(null);
    setError("");
    setStatus("unauthenticated");
  }, []);

  /** Token -> GET /auth/me -> user (+ organization name, best effort). */
  const resolveSession = useCallback(async (): Promise<AuthResult> => {
    const token = readToken();
    if (!token) {
      setUser(null);
      setOrg(null);
      setError("");
      setStatus("unauthenticated");
      return { ok: false, message: "Not signed in." };
    }
    setStatus("loading");
    setError("");
    try {
      const me = await authFetch("/api/v1/auth/me", token);
      if (me.status === 401) {
        // Expired / tampered / stale token.
        discard();
        return { ok: false, status: 401, message: "Your session has expired. Please log in again." };
      }
      if (me.status === 403) {
        // Token is valid but this user has no organization membership.
        const msg =
          "Your account is not linked to an organization, so it cannot access this workspace. Ask an administrator to add you, then try again.";
        setStatus("error");
        setError(msg);
        return { ok: false, status: 403, message: msg };
      }
      if (!me.ok) {
        const msg = `Could not verify your session (HTTP ${me.status}).`;
        setStatus("error");
        setError(msg);
        return { ok: false, status: me.status, message: msg };
      }
      const u = (await me.json()) as AuthUser;
      setUser(u);
      setStatus("authenticated");
      // Organization display name is cosmetic: fetch it in parallel with the
      // page rendering and tolerate failure without failing authentication.
      try {
        const r = await authFetch("/api/v1/organizations/me", token, 8000);
        setOrg(r.ok ? ((await r.json()) as AuthOrg) : null);
      } catch {
        setOrg(null);
      }
      return { ok: true, message: "Signed in." };
    } catch {
      const msg = "Cannot reach the server to verify your session.";
      setStatus("error");
      setError(msg);
      return { ok: false, message: msg };
    }
  }, [discard]);

  const login = useCallback(
    async (email: string, password: string): Promise<AuthResult> => {
      let res: Response;
      try {
        res = await fetch(`${API_URL}/api/v1/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ username: email, password }),
        });
      } catch {
        return { ok: false, message: "Cannot reach the server. Check your connection and retry." };
      }
      if (!res.ok) {
        if (res.status === 400 || res.status === 401) {
          return { ok: false, status: res.status, message: "Invalid email or password. Please try again." };
        }
        return { ok: false, status: res.status, message: `Login failed (HTTP ${res.status}).` };
      }
      let token: string;
      try {
        const j = await res.json();
        token = j.access_token;
      } catch {
        return { ok: false, status: res.status, message: "The server returned an unexpected response." };
      }
      storeToken(token);
      const r = await resolveSession();
      if (!r.ok) {
        return { ok: false, status: r.status, message: r.message };
      }
      return { ok: true, message: "Signed in." };
    },
    [resolveSession]
  );

  /** Used by register: the API returns a token, adopt it as a live session. */
  const adoptToken = useCallback(
    async (token: string): Promise<AuthResult> => {
      if (!token) return { ok: false, message: "No session token was returned." };
      storeToken(token);
      const r = await resolveSession();
      if (!r.ok) return { ok: false, status: r.status, message: r.message };
      return { ok: true, message: "Signed in." };
    },
    [resolveSession]
  );

  const logout = useCallback(() => {
    // Stateless JWT: the server cannot revoke it, but the credential is
    // removed here, so the app stops sending it (authHeaders reads storage).
    clearToken();
    setUser(null);
    setOrg(null);
    setError("");
    setStatus("unauthenticated");
    router.replace("/login");
  }, [router]);

  // Resolve the session once on mount; re-run only on manual retry/login.
  useEffect(() => {
    void resolveSession();
  }, [resolveSession]);

  // Browser Back/Forward cache: Chrome can restore a whole page (with its old
  // React state, which may still say "authenticated") without re-running the
  // mount effect. Re-verify on every bfcache restore so a logged-out visitor
  // can never keep a stale authenticated shell.
  useEffect(() => {
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) void resolveSession();
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, [resolveSession]);

  // Install the 401 watcher once; it flips this provider to unauthenticated
  // when an in-flight protected call comes back 401 (expired session).
  useEffect(() => {
    installUnauthorizedWatcher(() => {
      if (!readToken()) return;
      clearToken();
      setUser(null);
      setOrg(null);
      setError("");
      setStatus("unauthenticated");
    });
  }, []);

  const value: AuthContextValue = {
    status,
    user,
    org,
    error,
    login,
    adoptToken,
    logout,
    refreshUser: resolveSession,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
