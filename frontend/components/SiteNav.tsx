"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AccountMenu } from "@/components/AccountMenu";

/**
 * The application's single navigation (Phase 13).
 *
 * IA — every destination appears exactly once, nothing is duplicated:
 *
 *   brand mark      → Home (`/`)          — the landing page
 *   Primary (5)     → Contracts · Semantic Search · Templates · Policies ·
 *                     Portfolio
 *   Secondary       → ML Evaluation (a utility, kept out of the primary row)
 *   right side      → account control (Part 3, unchanged)
 *
 * Consolidation (Phases 10–11) removed the old "Approved Templates" and
 * "Company Policies" shortcuts — those are filters inside the single
 * Templates / Policies pages now, not separate destinations.
 *
 * The landing page (`/`) shows a public variant when nobody is signed in:
 * brand + Log in + Get started, so a visitor is never offered links to pages
 * they cannot reach yet.
 */

const PRIMARY: [string, string][] = [
  ["Contracts", "/contracts"],
  ["Semantic Search", "/search"],
  ["Templates", "/templates"],
  ["Policies", "/policies"],
  ["Portfolio", "/portfolio"],
];

const SECONDARY: [string, string][] = [["ML Evaluation", "/ml-evaluation"]];

/** `/dashboard` is a compatibility alias of `/contracts` — same active state. */
function isActive(pathname: string, href: string): boolean {
  if (href === "/contracts") return pathname === "/contracts" || pathname === "/dashboard";
  return pathname === href;
}

export function SiteNav() {
  const { status } = useAuth();
  const pathname = usePathname();
  const authed = status === "authenticated";
  const publicPage = pathname === "/login" || pathname === "/register";

  return (
    <header className="topnav">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link
          href="/"
          className="group flex items-center gap-2 text-brand-800"
          aria-label="Contract Intelligence — home"
        >
          <span
            aria-hidden="true"
            className="grid h-8 w-8 place-items-center rounded-full bg-brand-800 text-sm text-cream-100 transition group-hover:bg-brand-900"
          >
            ⚖
          </span>
          <span className="font-serif text-lg font-bold tracking-tight">
            Contract Intelligence
          </span>
        </Link>

        <nav className="flex flex-wrap gap-4 text-sm" aria-label="Primary">
          {/* Public visitors get the concise header (brand + auth actions);
              workspace destinations appear once the session resolves, so a
              signed-out visitor is never offered links they cannot open. */}
          {status !== "unauthenticated" &&
            PRIMARY.map(([label, href]) => (
              <Link
                key={href}
                href={href}
                className={`inline-flex min-h-8 items-center px-0.5 py-2 ${
                isActive(pathname, href) ? "active" : ""
              }`}
                aria-current={isActive(pathname, href) ? "page" : undefined}
              >
                {label}
              </Link>
            ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <nav className="flex gap-3 text-xs" aria-label="Secondary">
            {authed &&
              SECONDARY.map(([label, href]) => (
                <Link
                  key={href}
                  href={href}
                  className={`inline-flex min-h-8 items-center px-0.5 py-2 ${
                isActive(pathname, href) ? "active" : ""
              }`}
                  aria-current={isActive(pathname, href) ? "page" : undefined}
                >
                  {label}
                </Link>
              ))}
          </nav>

          <AccountMenu />

          {status === "unauthenticated" && !publicPage && (
            /* AccountMenu already renders the "Log in" link for a public
               visitor (Part 3) — only "Get started" is added here so no
               destination ever appears twice in the header. Rendered only
               once the session has resolved, so it never flickers in and
               out for a signed-in user. */
            <div className="flex items-center gap-2">
              <Link href="/register" className="btn btn-primary btn-sm">
                Get started
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
