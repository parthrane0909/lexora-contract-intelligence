"use client";
import { useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

/**
 * Public landing page (Phase 8).
 *
 * `/` is now a real destination instead of a redirect: it introduces the
 * product and offers the two primary journeys (contracts, portfolio) plus
 * direct routes into Semantic Search, Templates and Policies.
 *
 * Navigation adapts to the session (Part 3, unchanged): signed-out visitors
 * get "Log in / Get started" from the global header and public calls to
 * action here; signed-in users get "Explore Contracts / View Portfolio".
 *
 * The hero image is the supplied `Golden Angel of Justice in the Heavens(1).png`
 * served verbatim from /public — it is never substituted or generated.
 *
 * Motion is decorative only: `.reveal` blocks fade in through an
 * IntersectionObserver, and the reduced-motion media query in globals.css
 * removes every transition and animation for users who ask for it.
 */

const HERO_IMAGE = "/Golden%20Angel%20of%20Justice%20in%20the%20Heavens(1).png";

const CAPABILITIES: [string, string, string][] = [
  [
    "01",
    "Clause-level classification",
    "A transformer model sorts every clause into its legal category, so a "
      + "120-page agreement becomes a structured, navigable document.",
  ],
  [
    "02",
    "Risk findings with evidence",
    "Deterministic rules produce findings that quote the original clause and "
      + "name the rule that fired — never an unsourced score.",
  ],
  [
    "03",
    "Policy and template comparison",
    "See where an agreement departs from your approved positions and your "
      + "own standard template, side by side.",
  ],
  [
    "04",
    "Semantic clause search",
    "Ask for a concept, not a filename: MiniLM embeddings retrieve the "
      + "closest clauses across your whole portfolio.",
  ],
  [
    "05",
    "Portfolio attention queue",
    "One health score per contract, built from real counts of findings, "
      + "overdue obligations and imminent renewals.",
  ],
  [
    "06",
    "Human review, on the record",
    "Accepted, rejected and escalated decisions are kept in an audit ledger "
      + "— the model advises, your reviewers decide.",
  ],
];

const EXPLORE: { href: string; title: string; body: string }[] = [
  {
    href: "/search",
    title: "Semantic Search",
    body: "Find clauses that mean the same thing, across every contract you hold.",
  },
  {
    href: "/templates",
    title: "Templates",
    body: "Your standard language, its approved variants, and where yours differs.",
  },
  {
    href: "/policies",
    title: "Policies",
    body: "The negotiating positions your organization has agreed to hold.",
  },
];

/** Decorative scroll reveal — inert when the user prefers reduced motion. */
function useReveal() {
  useEffect(() => {
    const nodes = Array.from(document.querySelectorAll<HTMLElement>(".reveal"));
    if (nodes.length === 0) return;
    if (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      nodes.forEach((n) => n.classList.add("is-visible"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            io.unobserve(e.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );
    nodes.forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, []);
}

export default function LandingPage() {
  const { status } = useAuth();
  const authed = status === "authenticated";
  useReveal();

  return (
    <main className="pb-6">
      {/* ------------------------------------------------------------ hero */}
      <section className="relative overflow-hidden rounded-2xl border border-line bg-paper">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(120% 90% at 8% 0%, rgba(233,215,166,.55) 0%, rgba(255,253,248,0) 58%)",
          }}
        />
        <div className="relative grid items-stretch gap-0 lg:grid-cols-[1.05fr_1fr]">
          <div className="p-7 sm:p-10 lg:p-14">
            <p className="eyebrow animate-fade-up">Contract Intelligence</p>
            <h1 className="mt-4 font-serif text-4xl font-bold leading-[1.05] tracking-tight text-ink sm:text-5xl lg:text-[3.4rem] animate-fade-up">
              Know every risk
              <br />
              <span className="text-brand-800">before you sign.</span>
            </h1>
            <p className="lede mt-5 animate-fade-up">
              Upload an agreement and read what it actually says: classified
              clauses, evidenced risk findings, policy deviations and the
              obligations you are about to inherit — with human reviewers
              making the final call.
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3 animate-fade-up">
              {authed ? (
                <>
                  <Link href="/contracts" className="btn btn-primary">
                    Explore Contracts
                  </Link>
                  <Link href="/portfolio" className="btn btn-secondary">
                    View Portfolio
                  </Link>
                </>
              ) : (
                <>
                  <Link href="/register" className="btn btn-primary">
                    Get started
                  </Link>
                  <Link href="/login" className="btn btn-secondary">
                    Log in
                  </Link>
                </>
              )}
            </div>

            <nav
              aria-label="Explore the workspace"
              className="mt-7 flex flex-wrap gap-x-6 gap-y-2 border-t border-line pt-5 text-sm"
            >
              <span className="eyebrow self-center">Also</span>
              {EXPLORE.map((e) => (
                <Link
                  key={e.href}
                  href={e.href}
                  className="inline-flex min-h-6 items-center py-1 text-ink-soft underline-offset-4 transition hover:text-brand-800 hover:underline"
                >
                  {e.title}
                </Link>
              ))}
            </nav>

            <dl className="mt-7 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-5 sm:grid-cols-3">
              {[
                ["Analysis", "Transformer clause classification"],
                ["Retrieval", "MiniLM embeddings + Qdrant"],
                ["Risk", "Deterministic, reviewable rules"],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="eyebrow">{k}</dt>
                  <dd className="mt-1 text-sm text-ink-soft">{v}</dd>
                </div>
              ))}
            </dl>
          </div>

          <figure className="relative min-h-[16rem] border-t border-line bg-cream-100 lg:border-l lg:border-t-0">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={HERO_IMAGE}
              alt="A gilded angel holding the scales of justice among sunlit clouds"
              className="h-full w-full object-cover"
              width={1774}
              height={887}
              loading="eager"
              decoding="async"
            />
            <figcaption className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink/75 to-transparent p-4 text-xs tracking-wide text-cream-100">
              Golden Angel of Justice in the Heavens
            </figcaption>
          </figure>
        </div>
      </section>

      {/* ----------------------------------------------------- capabilities */}
      <section className="mt-14" aria-labelledby="capabilities">
        <div className="reveal max-w-2xl">
          <p className="eyebrow">What it does</p>
          <h2 id="capabilities" className="mt-2 font-serif text-3xl font-bold text-ink">
            Six things you stop guessing about
          </h2>
          <p className="lede mt-3">
            Everything the system tells you is traceable: to a clause, to a
            rule, or to a reviewer. Nothing is inferred without a source.
          </p>
        </div>

        <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(([n, title, body]) => (
            <article key={n} className="card card-hover reveal p-5">
              <span className="font-serif text-2xl font-bold text-gold-500">{n}</span>
              <h3 className="mt-3 font-serif text-lg font-semibold text-ink">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* --------------------------------------------------------- explore */}
      <section className="mt-14 reveal" aria-labelledby="explore">
        <p className="eyebrow">Go straight there</p>
        <h2 id="explore" className="mt-2 font-serif text-3xl font-bold text-ink">
          Three places people start
        </h2>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          {EXPLORE.map((e) => (
            <Link
              key={e.href}
              href={e.href}
              className="card card-hover group flex flex-col justify-between p-6"
            >
              <div>
                <h3 className="font-serif text-xl font-semibold text-ink">{e.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">{e.body}</p>
              </div>
              <span className="mt-6 inline-flex items-center gap-2 text-sm font-semibold text-brand-700">
                Open <span aria-hidden="true">→</span>
              </span>
            </Link>
          ))}
        </div>
      </section>

      {/* -------------------------------------------------------- closing */}
      <section className="mt-14 reveal rounded-2xl bg-brand-800 p-7 text-cream-100 sm:p-10">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-2xl">
            <h2 className="font-serif text-2xl font-bold sm:text-3xl">
              Start with one contract
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-cream-200">
              Upload a PDF or DOCX and the pipeline takes it from there —
              parsing, classification, risk analysis and semantic indexing —
              while you keep the decision.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link
              href={authed ? "/contracts" : "/register"}
              className="btn btn-gold"
            >
              {authed ? "Open Contracts" : "Create an account"}
            </Link>
            <Link
              href={authed ? "/portfolio" : "/login"}
              className="btn border border-cream-300/60 bg-transparent text-cream-100 hover:bg-white/10"
            >
              {authed ? "View Portfolio" : "Log in"}
            </Link>
          </div>
        </div>
      </section>

      <p className="mt-8 text-xs text-ink-mute">
        Decision support for legal review — not legal advice.
      </p>
    </main>
  );
}
