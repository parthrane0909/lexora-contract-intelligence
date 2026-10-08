"use client";
import ContractsPage from "@/app/contracts/page";

/**
 * `/dashboard` is the historical name of the Contracts page (Parts 1–5).
 *
 * It renders the very same view as `/contracts` instead of redirecting, so
 * existing bookmarks, the login fallback target and every Part 3 auth
 * assertion (logged-out `/dashboard` → `/login?next=%2Fdashboard`, the NAV
 * loop, the no-open-redirect fallback) keep behaving exactly as they did.
 *
 * Only `/contracts` appears in the navigation — this route exists purely for
 * compatibility and is not a second destination in the product's IA.
 */
export default function DashboardPage() {
  return <ContractsPage />;
}
