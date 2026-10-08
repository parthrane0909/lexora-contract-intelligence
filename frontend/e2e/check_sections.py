#!/usr/bin/env python3
"""Section ownership + Phase-8 verification, read-only (no clicks).

Asserts on the REAL rendered DOM:
  * DOM order: header -> CONTRACT SUMMARY -> RISK ASSESSMENT -> document
  * Contract Summary: neutral contract description only — no risk counts,
    no coverage, no attention list, no overall state, no badges, no
    recommendations. It DOES state what the contract is about.
  * Risk assessment: owns coverage, overall state, counts, findings,
    top attention items, clause-by-clause list and the state filters.
  * Each clause card: title/ref, category, risk state, priority, why,
    rule, original clause, evidence, requires-attention.
"""
import argparse
import json
import re
import time

from cdp_shot import CDP, list_pages

# Strings that must NEVER appear inside #summary.
FORBIDDEN = [
    "Risk overview", "Top attention items", "Analysis coverage",
    "clauses classified", "clauses risk-evaluated", "clauses require review",
    "findings recorded", "Overall:", "Needs Attention", "needs attention",
    "requires attention", "HIGH RISK", "MEDIUM RISK", "LOW RISK",
    "NO DETECTED RISK", "No detected risk", "View in document",
    "Attention:", "Why:", "Rule:",
    # Part 4: descriptive summary must carry no risk content at all.
    "findings", "priority", "High Risk", "Medium Risk", "Low Risk",
    "risk rule", "risk_summary", "needs_review", "needs_attention",
]

# Strings the risk section must own.
RISK_REQUIRED = [
    "Risk assessment", "Analysis coverage", "Risk overview",
    "Top attention items", "Clause-by-clause assessment",
    "clauses classified", "clauses risk-evaluated",
]

CARD_FIELDS = [
    ("category", "Category:"),
    ("risk state", "RISK"),
    ("why", "Why:"),
    ("rule", "Rule:"),
    ("original clause", "Original clause"),
    ("evidence", "Evidence"),
    ("requires-attention", "Attention:"),
    ("action", "Action:"),
]

# A clause is identified by §N — Title, or "Clause N" when it carries no
# section number (the document header clause).
TITLE_REF_RE = re.compile(r"(§\s*\d+|Clause\s+\d+)")
STATE_RE = re.compile(
    r"(HIGH RISK|MEDIUM RISK|LOW RISK|NO DETECTED RISK|NEEDS REVIEW)"
)
FINDING_STATES = ("HIGH RISK", "MEDIUM RISK", "LOW RISK")


def txt(cdp, expr):
    return cdp.evaluate(expr) or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-clauses", type=int, required=True)
    a = ap.parse_args()

    pages = list_pages()
    cdp = CDP(pages[0]["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")

    cdp.send("Page.navigate", {"url": "http://localhost:3000/login"})
    for _ in range(80):
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.25)
    cdp.evaluate(f"localStorage.setItem('token', {json.dumps(a.token)})")
    cdp.send("Page.navigate", {"url": a.url})
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(0.4)
        if "Contract Summary" in txt(cdp, "document.body.innerText"):
            break
    time.sleep(2.5)

    summary = txt(cdp, "document.querySelector('#summary')?.innerText || ''")
    risk = txt(cdp, "document.querySelector('#risk-assessment')?.innerText || ''")
    order = txt(
        cdp,
        """(() => {
             const s = document.querySelector('#summary');
             const r = document.querySelector('#risk-assessment');
             const d = document.querySelector('main')?.innerText || '';
             if (!s || !r) return 'missing-section';
             return (d.indexOf(s.innerText.slice(0, 40)) < d.indexOf(r.innerText.slice(0, 40)))
               ? 'summary-first' : 'risk-first';
           })()""",
    )
    filters = json.loads(
        txt(
            cdp,
            """JSON.stringify((() => {
                 const g = document.querySelector('[aria-label="Filter clauses by risk state"]');
                 return g ? [...g.querySelectorAll('button')].map(b => b.textContent.trim()) : [];
               })())""") or "[]"
    )
    CARD_SEL = '#risk-assessment ol[aria-label="Clause-by-clause assessment list"]'
    cards = int(
        txt(cdp, f"String(document.querySelectorAll({json.dumps(CARD_SEL)} + ' > li').length)") or "0"
    )
    attention_items = int(
        txt(
            cdp,
            "String(document.querySelectorAll('#risk-assessment ol[aria-label=\"Top attention items list\"] > li').length)",
        )
        or "0"
    )
    cards_all = json.loads(
        txt(
            cdp,
            f"""JSON.stringify([...document.querySelectorAll({json.dumps(CARD_SEL)} + ' > li')]
                     .map(li => li.innerText))""") or "[]"
    )
    card_preview = cards_all[0] if cards_all else ""
    open(f"{a.out}_summary.txt", "w", encoding="utf-8").write(summary)
    open(f"{a.out}_risk.txt", "w", encoding="utf-8").write(risk)
    open(f"{a.out}_card.txt", "w", encoding="utf-8").write(card_preview)
    open(f"{a.out}_cards.txt", "w", encoding="utf-8").write(
        "".join(f"\n\n===== CARD {i} =====\n{c}" for i, c in enumerate(cards_all, 1))
    )

    forbidden_hits = [t for t in FORBIDDEN if t in summary]
    risk_missing = [t for t in RISK_REQUIRED if t not in risk]
    # Phase 8: every clause card shows its reference and risk state; a card
    # with a finding additionally shows category, why, rule, original clause,
    # evidence and the attention decision.
    card_missing = []
    for i, c in enumerate(cards_all, 1):
        st = STATE_RE.search(c or "")
        if not st:
            card_missing.append(f"card {i}: no risk state")
            continue
        if not TITLE_REF_RE.search(c):
            card_missing.append(f"card {i}: no clause title/ref")
        if st.group(1) in FINDING_STATES:
            for name, needle in CARD_FIELDS:
                if needle not in c:
                    card_missing.append(f"card {i}: no {name}")
    # Part 4: the summary reads like a document briefing — overview first,
    # then typed agreement details — and never the old clause-count opener.
    neutral_ok = (
        "Contract Summary" in summary
        and "What does this agreement say?" in summary
        and "Overview" in summary
        and "Agreement type" in summary
        and "Main agreement areas" in summary
        and not re.search(r"\d+\s+clauses", summary, re.I)
    )
    n_findings = int(
        txt(
            cdp,
            r"""(() => { const m = (document.body.innerText || '').match(/(\d+)\s+FINDINGS/);
                        return m ? m[1] : '0'; })()""",
        )
        or "0"
    )

    results = {
        "dom_order": order,
        "summary_forbidden_hits": forbidden_hits,
        "summary_neutral_ok": neutral_ok,
        "risk_missing": risk_missing,
        "filters": filters,
        "risk_clause_cards": cards,
        "cards_inspected": len(cards_all),
        "top_attention_items": attention_items,
        "findings_count": n_findings,
        "card_missing_fields": card_missing,
        "summary_text": summary,
    }
    checks = [
        ("summary renders before risk assessment", order == "summary-first"),
        ("summary explains what contract is about", neutral_ok),
        ("summary free of risk content", not forbidden_hits),
        ("risk section owns coverage/counts/attention", not risk_missing),
        ("risk section has state filters", len(filters) >= 5),
        ("risk section lists every clause", cards == a.expect_clauses),
        (
            "top attention items owned by risk section",
            attention_items >= 1 if int(n_findings) > 0 else True,
        ),
        ("clause card has all Phase-8 fields", not card_missing),
    ]
    results["checks"] = [{"name": n, "ok": ok} for n, ok in checks]
    results["verdict"] = "PASS" if all(ok for _, ok in checks) else "FAIL"

    with open(f"{a.out}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"=== section ownership: {results['verdict']} ===")
    for n, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {n}")
    if forbidden_hits:
        print("  forbidden in summary:", forbidden_hits)
    if risk_missing:
        print("  missing from risk:", risk_missing)
    if card_missing:
        print("  missing card fields:", card_missing)
    print("  filters:", filters)
    print("  json ->", f"{a.out}.json")


if __name__ == "__main__":
    main()
