"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

/**
 * Global account control (Part 3): "am I logged in / as whom?" at a glance.
 *
 * - loading          -> quiet skeleton (never a bare header)
 * - error            -> nothing (identity unknown; protected pages show retry)
 * - unauthenticated  -> a "Log in" link (hidden on /login and /register)
 * - authenticated    -> initials avatar + identity, opening an account menu
 *
 * Real backend fields only: email, display_name, organization name, role.
 * Nothing (no plan, no fake title) is invented. Keyboard: native <button>
 * (Enter/Space), Escape closes and restores focus, outside click closes,
 * focus lands on Log out when the menu opens. Not hover-driven.
 */

function initialsOf(name: string | null, email: string): string {
  const src = (name && name.trim()) || email;
  const parts = src.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function formatRole(role: string): string {
  // Only re-spaces the real value (contract_manager -> contract manager).
  return role.replace(/_/g, " ");
}

function Skeleton() {
  return (
    <div
      className="flex items-center gap-2 animate-pulse"
      role="status"
      aria-label="Checking session"
      title="Checking session…"
    >
      <span className="h-8 w-8 rounded-full bg-slate-200" aria-hidden="true" />
      <span className="hidden sm:block h-3 w-24 rounded bg-slate-200" aria-hidden="true" />
    </div>
  );
}

export function AccountMenu() {
  const { status, user, org, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const onAuthPage = pathname === "/login" || pathname === "/register";

  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    // Move focus into the menu so keyboard users land where they can act.
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (status === "loading") return <Skeleton />;
  if (status === "error") return null;

  if (status !== "authenticated" || !user) {
    if (onAuthPage) return null;
    return (
      <Link href="/login" className="text-sm font-medium text-brand-800 shrink-0">
        Log in
      </Link>
    );
  }

  const identity = user.display_name?.trim() || user.email;
  const hasName = !!user.display_name?.trim();
  const orgName = org?.name;

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button
        ref={btnRef}
        type="button"
        id="account-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="account-menu-panel"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className="flex items-center gap-2 rounded-full border border-slate-200 py-1 pl-1 pr-2 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#991b1b]"
        title={user.email}
      >
        <span
          aria-hidden="true"
          className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-800 text-xs font-semibold text-white"
        >
          {initialsOf(user.display_name, user.email)}
        </span>
        <span className="hidden md:block max-w-[11rem] truncate text-left text-xs leading-tight">
          <span className="block font-semibold text-slate-800 truncate">{identity}</span>
          {/* Only add a second line when there is a real display name —
              repeating the email under itself is noise. */}
          {hasName && (
            <span className="block text-ink-mute truncate">{user.email}</span>
          )}
        </span>
        <span aria-hidden="true" className="text-[10px] text-ink-mute">
          ▾
        </span>
        <span className="sr-only">Account menu</span>
      </button>

      {open && (
        <div
          ref={menuRef}
          id="account-menu-panel"
          role="menu"
          aria-labelledby="account-menu-button"
          className="absolute right-0 z-50 mt-2 w-64 rounded-lg border border-slate-200 bg-white p-3 shadow-lg"
        >
          <p className="text-[11px] uppercase tracking-wide text-ink-mute">Signed in as</p>
          <p className="truncate text-sm font-semibold text-slate-800" title={user.email}>
            {user.email}
          </p>
          {user.display_name?.trim() && (
            <p className="truncate text-xs text-ink-mute">{user.display_name}</p>
          )}

          <dl className="mt-3 space-y-2 border-t border-slate-100 pt-3 text-xs">
            <div>
              <dt className="text-ink-mute">Organization</dt>
              <dd className="text-slate-700" title={orgName ?? undefined}>
                {orgName ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-mute">Role</dt>
              <dd className="text-slate-700">{formatRole(user.role)}</dd>
            </div>
          </dl>

          <div className="mt-3 border-t border-slate-100 pt-2">
            <button
              type="button"
              role="menuitem"
              onClick={logout}
              className="w-full rounded px-2 py-1.5 text-left text-sm font-medium text-red-700 hover:bg-red-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#991b1b]"
            >
              Log out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
