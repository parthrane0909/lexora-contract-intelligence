#!/usr/bin/env python3
"""Phase 16 acceptance: exercise ONLY the real web workflow.

Opens the contract page in the running browser, records the rendered state,
clicks the actual "Run risk analysis" button (no manual API calls at all),
watches the progress UI stage by stage, then asserts the completed state,
the clause-by-clause results, the summary/risk separation, and persistence
after a real page reload.

Prints PASS/FAIL per assertion and a machine-readable JSON summary.
"""
import argparse
import json
import re
import time
import urllib.request

from cdp_shot import CDP, list_pages

FORBIDDEN_IN_SUMMARY = [
    "Risk overview",
    "Top attention items",
    "Analysis coverage",
    "clauses classified",
    "clauses risk-evaluated",
    "clauses require review",
    "findings recorded",
    "Overall:",
    "needs attention",
    "Needs Attention",
    "High Risk",
    "Medium Risk",
    "LOW RISK",
]

REQUIRED_IN_RISK = ["Analysis coverage", "Risk overview", "Clause-by-clause assessment"]


def eval_json(cdp, expr, default=None):
    out = cdp.evaluate(f"JSON.stringify(({expr}))")
    try:
        return json.loads(out) if out else default
    except Exception:
        return default


def body_text(cdp):
    return cdp.evaluate("document.body ? document.body.innerText : ''") or ""


def section(cdp, sel):
    return cdp.evaluate(
        f"(document.querySelector({json.dumps(sel)}) || {{innerText:''}}).innerText"
    ) or ""


def status_strip(cdp):
    """The Processing / Classification / Risk analysis / Semantic search row."""
    return cdp.evaluate(
        """(() => {
             const d = [...document.querySelectorAll('div[aria-live]')]
               .find(x => x.innerText.includes('Classification') && x.innerText.includes('Risk analysis'));
             return d ? d.innerText : '';
           })()"""
    ) or ""


def post_requests(cdp):
    reqs = {}
    for ev in cdp.events:
        if ev.get("method") == "Network.requestWillBeSent":
            p = ev["params"]
            if p["request"].get("method") == "POST" and "/api/v1/" in p["request"].get("url", ""):
                reqs[p["requestId"]] = (p["request"]["url"], None)
        elif ev.get("method") == "Network.responseReceived":
            p = ev["params"]
            if p["requestId"] in reqs:
                url, _ = reqs[p["requestId"]]
                reqs[p["requestId"]] = (url, p["response"]["status"])
    return [(u, s) for u, s in reqs.values()]


# Clause cards only (the section also holds the top-attention list).
CARD_JS = """[...document.querySelectorAll('#risk-assessment ol')]
  .filter(o => o.getAttribute('aria-label') === 'Clause-by-clause assessment list')
  .flatMap(o => [...o.children].map(li => li.innerText))"""
STATE_PAT = re.compile(r"(HIGH RISK|MEDIUM RISK|LOW RISK|NO DETECTED RISK|NEEDS REVIEW)")


def card_texts(cdp):
    """innerText of every clause-by-clause card currently rendered."""
    out = cdp.evaluate(CARD_JS)
    return out if isinstance(out, list) else []


def processing_status(token, page_url):
    """Read processing_status for the contract the page is showing."""
    cid = page_url.rstrip("/").split("/")[-1]
    req = urllib.request.Request(
        f"http://localhost:8001/api/v1/contracts/{cid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("processing_status", "?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-clauses", type=int, required=True)
    ap.add_argument("--label", default="acceptance")
    ap.add_argument(
        "--reprocess",
        action="store_true",
        help="click Re-process first (resets the fixture and exercises the "
             "fixed re-process handler), wait for processing, then continue",
    )
    a = ap.parse_args()

    pages = list_pages()
    if not pages:
        req = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
        pages = [json.load(urllib.request.urlopen(req))]
    cdp = CDP(pages[0]["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")
    cdp.events.clear()

    results = {}

    # --- land on origin, inject token -----------------------------------
    cdp.send("Page.navigate", {"url": "http://localhost:3000/login"})
    for _ in range(80):
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.25)
    cdp.evaluate(f"localStorage.setItem('token', {json.dumps(a.token)})")

    # --- open the contract page -----------------------------------------
    cdp.send("Page.navigate", {"url": a.url})
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(0.4)
        if "Contract Summary" in body_text(cdp):
            break
    time.sleep(1.5)

    results["reprocess"] = None
    if a.reprocess:
        # Setup only: reset the fixture with the app's own Re-process button
        # (this also exercises the previously bare `await fetch(...)` handler).
        results["reprocess"] = {}
        results["reprocess"]["clicked"] = cdp.evaluate(
            """(() => {
                 const b = [...document.querySelectorAll('button')]
                   .find(x => /Re-process document/.test(x.textContent));
                 if (!b) return 'not-found';
                 if (b.disabled) return 'disabled';
                 b.click();
                 return 'clicked';
               })()"""
        )
        time.sleep(2.0)
        results["reprocess"]["message"] = cdp.evaluate(
            """[...document.querySelectorAll('span[role=status], span[role=alert]')]
                 .map(e => e.innerText.trim())
                 .filter(t => /[Rr]e-process/.test(t)).join(' | ')"""
        ) or ""
        rp_deadline = time.time() + 180
        status = "?"
        while time.time() < rp_deadline:
            try:
                status = processing_status(a.token, a.url)
            except Exception:
                status = "?"
            if status in ("processed", "failed"):
                break
            time.sleep(2.0)
        results["reprocess"]["status"] = status
        # Reload so the page reflects the freshly processed document.
        cdp.send("Page.navigate", {"url": a.url})
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(0.4)
            if "Contract Summary" in body_text(cdp):
                break
        time.sleep(2.0)

    before_header = body_text(cdp)
    before_strip = status_strip(cdp)
    before_summary = section(cdp, "#summary")
    results["before"] = {
        "processing_complete": before_strip.count("✓ Complete") >= 1,
        "classification_not_started": (
            ("Not started" in before_strip or "Stale" in before_strip)
            and f"0/{a.expect_clauses} clauses classified" in before_strip
        ),
        "risk_unanalyzed": ("not run yet" in before_strip) or ("not analyzed" in before_strip)
        or ("Partial" in before_strip) or ("Stale" in before_strip),
        "run_button": eval_json(
            cdp,
            "document.querySelector('[data-testid=run-risk-analysis]')?.innerText || ''",
            "",
        ),
    }
    results["before"]["classified_zero"] = f"0/{a.expect_clauses} clauses classified" in before_strip
    results["before_strip"] = before_strip

    open(f"{a.out}_before.txt", "w", encoding="utf-8").write(before_header)

    # --- CLICK the real button (the only action taken) -------------------
    clicked = cdp.evaluate(
        """(() => { const b = document.querySelector('[data-testid=run-risk-analysis]');
                    if (!b) return 'not-found'; if (b.disabled) return 'disabled';
                    b.click(); return 'clicked'; })()"""
    )
    results["click"] = clicked

    # --- watch the progress UI ------------------------------------------
    seen_stages, seen_labels, progress_present = set(), set(), False
    poll_deadline = time.time() + 90
    finished = ""
    while time.time() < poll_deadline:
        prog = cdp.evaluate(
            "document.querySelector('[data-testid=risk-progress]')?.innerText || ''"
        ) or ""
        if prog.strip():
            progress_present = True
            for line in [l.strip() for l in prog.split("\n") if l.strip()]:
                seen_stages.add(line)
        lbl = cdp.evaluate(
            "document.querySelector('[data-testid=run-risk-analysis]')?.innerText || ''"
        ) or ""
        if lbl.strip():
            seen_labels.add(lbl.strip())
        txt = body_text(cdp)
        if "Risk analysis complete" in txt:
            finished = "complete"
            break
        if "Risk analysis failed" in txt or "did not complete" in txt:
            finished = "failed"
            break
        time.sleep(0.15)

    results["progress"] = {
        "progress_box_seen": progress_present,
        "stages": sorted(seen_stages),
        "button_labels": sorted(seen_labels),
        "outcome": finished or "timeout",
    }

    time.sleep(1.2)
    after_header = body_text(cdp)
    after_strip = status_strip(cdp)
    after_summary = section(cdp, "#summary")
    after_risk = section(cdp, "#risk-assessment")
    open(f"{a.out}_after.txt", "w", encoding="utf-8").write(after_header)
    open(f"{a.out}_strip.txt", "w", encoding="utf-8").write(after_strip)
    open(f"{a.out}_summary.txt", "w", encoding="utf-8").write(after_summary)
    open(f"{a.out}_risk.txt", "w", encoding="utf-8").write(after_risk)

    after_cards = card_texts(cdp)
    n_cards = len(after_cards)
    states = [
        (STATE_PAT.search(c).group(1) if STATE_PAT.search(c) else "")
        for c in after_cards
    ]

    results["after"] = {
        "classification_complete": f"{a.expect_clauses}/{a.expect_clauses} clauses classified" in after_strip
        and after_strip.count("✓ Complete") >= 3,
        "risk_complete": "not run yet" not in after_strip
        and ("findings" in after_strip or "no detected risk" in after_strip),
        "strip": after_strip,
        "success_message": [l for l in after_header.split("\n") if "Risk analysis complete" in l],
        "clause_cards": n_cards,
        "clause_states": states,
        "risk_has_coverage": all(s in after_risk for s in REQUIRED_IN_RISK),
        "risk_counts_present": all(
            s in after_risk.lower()
            for s in ["high", "medium", "low", "no risk", "review", "findings", "attention"]
        ),
        "summary_neutral_info": f"contains {a.expect_clauses} clauses" in after_summary,
        "summary_forbidden_hits": [s for s in FORBIDDEN_IN_SUMMARY if s in after_summary],
        "summary_paragraphs": [
            p for p in after_summary.split("\n")
            if p.strip() and not p.startswith("Key Contract") and not p.startswith("Contract Summary")
        ][:8],
    }

    # POSTs issued by the page during the click — captured before the reload
    results["posts_from_page"] = [
        {"status": s, "url": u.replace("http://localhost:8001", "")}
        for u, s in post_requests(cdp)
    ]

    # --- real reload: results must persist -------------------------------
    cdp.events.clear()
    cdp.send("Page.navigate", {"url": a.url})
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(0.4)
        if "Contract Summary" in body_text(cdp):
            break
    time.sleep(2.0)
    persisted_header = body_text(cdp)
    persisted_strip = status_strip(cdp)
    persisted_risk = section(cdp, "#risk-assessment")
    open(f"{a.out}_persisted.txt", "w", encoding="utf-8").write(persisted_header)
    results["persisted"] = {
        "classification_complete": f"{a.expect_clauses}/{a.expect_clauses} clauses classified" in persisted_strip
        and persisted_strip.count("✓ Complete") >= 3,
        "risk_complete": "not run yet" not in persisted_strip
        and ("findings" in persisted_strip or "no detected risk" in persisted_strip),
        "strip": persisted_strip,
        "clause_cards": len(card_texts(cdp)),
        "coverage_present": "Analysis coverage" in persisted_risk,
    }

    # --- verdict ----------------------------------------------------------
    checks = [
        ("clicked real button", clicked == "clicked"),
        ("initial state not analyzed", all(results["before"].values()) or results["before"]["classification_not_started"]),
        ("progress UI shown", results["progress"]["progress_box_seen"]),
        ("progress stages recorded", len(results["progress"]["stages"]) >= 2),
        ("run finished", results["progress"]["outcome"] == "complete"),
        ("classification complete in UI", results["after"]["classification_complete"]),
        ("risk analysis complete in UI", results["after"]["risk_complete"]),
        ("success message rendered", bool(results["after"]["success_message"])),
        ("all clauses rendered", results["after"]["clause_cards"] == a.expect_clauses),
        ("clause states rendered", len(results["after"]["clause_states"]) == a.expect_clauses),
        ("risk section owns coverage", results["after"]["risk_has_coverage"]),
        ("risk section owns counts", results["after"]["risk_counts_present"]),
        ("summary has no risk content", not results["after"]["summary_forbidden_hits"]),
        ("summary has neutral contract info", results["after"]["summary_neutral_info"]),
        ("page called /classify", any("/classify" in p["url"] for p in results["posts_from_page"])),
        ("page called /risk-analysis", any("/risk-analysis" in p["url"] for p in results["posts_from_page"])),
        ("persisted after reload (classification)", results["persisted"]["classification_complete"]),
        ("persisted after reload (risk)", results["persisted"]["risk_complete"]),
        ("persisted after reload (clauses)", results["persisted"]["clause_cards"] == a.expect_clauses),
    ]
    if results.get("reprocess"):
        rp = results["reprocess"]
        checks = checks + [
            ("re-process button clicked", rp["clicked"] == "clicked"),
            ("re-process reported no error", "failed" not in rp["message"].lower()),
            ("re-process finished processed", rp["status"] == "processed"),
        ]
    results["checks"] = [{"name": n, "ok": ok} for n, ok in checks]
    results["verdict"] = "PASS" if all(ok for _, ok in checks) else "FAIL"

    with open(f"{a.out}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"=== {a.label}: {results['verdict']} ===")
    for n, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {n}")
    print("\nPOSTs issued by the page:")
    for p in results["posts_from_page"]:
        print(f"  {p['status']} {p['url']}")
    print("\nsummary paragraphs:")
    for p in results["after"]["summary_paragraphs"]:
        print("   -", p[:200])
    print("\njson ->", f"{a.out}.json")


if __name__ == "__main__":
    main()
