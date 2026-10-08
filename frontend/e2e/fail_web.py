#!/usr/bin/env python3
"""Phase 17: deliberate backend failure, injected in the REAL page.

Overrides window.fetch inside the running browser so that the button's
POST /risk-analysis returns a 500 with a useful JSON `detail`, then clicks
the actual "Run risk analysis" button and asserts:

  * the UI says "Risk analysis failed"
  * the backend's own message is shown
  * no stack trace / HTML leaks through
  * it never claims a false success or a quiet "Partial"
  * the button becomes "Retry Risk Analysis"

Then the mock is removed and Retry is clicked for real: the run must then
succeed end-to-end.
"""
import argparse
import json
import time

from cdp_shot import CDP, list_pages

MOCK_DETAIL = "Risk engine unavailable: rule set failed to load (simulated outage)"
FAIL_MSG = "Risk analysis failed"


def body_text(cdp):
    return cdp.evaluate("document.body ? document.body.innerText : ''") or ""


def alerts(cdp):
    out = cdp.evaluate(
        "[...document.querySelectorAll('[role=alert]')].map(e => e.innerText.trim())"
    )
    if isinstance(out, list):
        return out
    try:
        return json.loads(out or "[]")
    except Exception:
        return []


def button_label(cdp):
    return cdp.evaluate(
        "document.querySelector('[data-testid=run-risk-analysis]')?.innerText || ''"
    ) or ""


def click(cdp):
    return cdp.evaluate(
        """(() => { const b = document.querySelector('[data-testid=run-risk-analysis]');
                    if (!b) return 'not-found'; if (b.disabled) return 'disabled';
                    b.click(); return 'clicked'; })()"""
    )


def wait_for(cdp, pred, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred(cdp):
            return True
        time.sleep(0.2)
    return False


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
    cdp.send("Network.enable")
    cdp.events.clear()

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
        if "Contract Summary" in body_text(cdp):
            break
    time.sleep(1.5)

    before = body_text(cdp)
    results = {"before_complete": f"{a.expect_clauses}/{a.expect_clauses} clauses classified" in before}

    # --- install the failure mock (only the risk endpoint) ---------------
    installed = cdp.evaluate(
        f"""(() => {{
              if (window.__origFetch) return 'already';
              window.__origFetch = window.fetch;
              window.__mockFail = true;
              window.fetch = function (url, opts) {{
                const u = String(url);
                const isPost = opts && (opts.method || 'GET') === 'POST';
                if (window.__mockFail && isPost && u.includes('/risk-analysis')) {{
                  return Promise.resolve(new Response(
                    JSON.stringify({{detail: {json.dumps(MOCK_DETAIL)}}}),
                    {{status: 500, headers: {{'Content-Type': 'application/json'}}}}));
                }}
                return window.__origFetch(url, opts);
              }};
              return 'installed';
            }})()"""
    )
    results["mock"] = installed

    # --- click with the backend "down" -----------------------------------
    results["click"] = click(cdp)
    got_fail = wait_for(
        cdp,
        lambda c: any(FAIL_MSG in x for x in alerts(c)),
        timeout=40,
    )
    time.sleep(0.8)
    fail_alerts = [x for x in alerts(cdp) if FAIL_MSG in x or "did not complete" in x]
    fail_text = "\n".join(fail_alerts)
    header = body_text(cdp)
    open(f"{a.out}_failed.txt", "w", encoding="utf-8").write(header)

    results["failed_state"] = {
        "alert_shown": got_fail,
        "alerts": fail_alerts,
        "backend_detail_present": MOCK_DETAIL in fail_text,
        "retry_label": "Retry Risk Analysis" in button_label(cdp),
        "no_false_success": "Risk analysis complete" not in header,
        "no_stack_trace": not any(
            bad in fail_text
            for bad in ["Traceback", "File \"", "node_modules", "SQLAlchemy", "at Object.", "Error: INJECTED"]
        ),
        "no_silent_partial": "Partial" not in fail_text and "◐" not in fail_text,
        "progress_hidden": cdp.evaluate(
            "document.querySelector('[data-testid=risk-progress]') === null"
        ),
        "button_enabled": cdp.evaluate(
            "!document.querySelector('[data-testid=run-risk-analysis]')?.disabled"
        ),
    }

    # --- remove the mock and use the Retry button ------------------------
    cdp.evaluate("window.__mockFail = false; 'off'")
    cdp.events.clear()
    results["retry_click"] = click(cdp)
    got_ok = wait_for(
        cdp, lambda c: "Risk analysis complete" in body_text(c), timeout=60
    )
    time.sleep(1.0)
    after = body_text(cdp)
    strip = cdp.evaluate(
        """(() => { const d = [...document.querySelectorAll('div[aria-live]')]
                      .find(x => x.innerText.includes('Classification') && x.innerText.includes('Risk analysis'));
                    return d ? d.innerText : ''; })()"""
    ) or ""
    open(f"{a.out}_retried.txt", "w", encoding="utf-8").write(after)
    results["retry_state"] = {
        "succeeded": got_ok,
        "classification_complete": f"{a.expect_clauses}/{a.expect_clauses} clauses classified" in strip,
        "risk_complete": "not run yet" not in strip
        and ("findings" in strip or "no detected risk" in strip),
        "strip": strip,
        "posts": sorted(
            {
                u.replace("http://localhost:8001", "")
                for u, s in [
                    (
                        ev["params"]["request"]["url"],
                        ev["params"]["request"].get("method"),
                    )
                    for ev in cdp.events
                    if ev.get("method") == "Network.requestWillBeSent"
                    and ev["params"]["request"].get("method") == "POST"
                    and "/api/v1/" in ev["params"]["request"].get("url", "")
                ]
            }
        ),
    }

    checks = [
        ("mock installed", results["mock"] == "installed"),
        ("clicked real button", results["click"] == "clicked"),
        ("failure alert shown", results["failed_state"]["alert_shown"]),
        ("backend message shown", results["failed_state"]["backend_detail_present"]),
        ("button reads Retry Risk Analysis", results["failed_state"]["retry_label"]),
        ("no false success claimed", results["failed_state"]["no_false_success"]),
        ("no stack trace leaked", results["failed_state"]["no_stack_trace"]),
        ("failure not reported as Partial", results["failed_state"]["no_silent_partial"]),
        ("progress stopped", results["failed_state"]["progress_hidden"]),
        ("button re-enabled for retry", results["failed_state"]["button_enabled"]),
        ("retry clicked", results["retry_click"] == "clicked"),
        ("retry succeeded", results["retry_state"]["succeeded"]),
        ("retry classification complete", results["retry_state"]["classification_complete"]),
        ("retry risk complete", results["retry_state"]["risk_complete"]),
    ]
    results["checks"] = [{"name": n, "ok": ok} for n, ok in checks]
    results["verdict"] = "PASS" if all(ok for _, ok in checks) else "FAIL"

    with open(f"{a.out}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"=== failure-handling: {results['verdict']} ===")
    for n, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {n}")
    print("\nfailure alert text:")
    for line in fail_text.split("\n"):
        print("   |", line)
    print("\nretry strip:", strip.replace("\n", " | "))
    print("json ->", f"{a.out}.json")


if __name__ == "__main__":
    main()
