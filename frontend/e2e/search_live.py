#!/usr/bin/env python3
"""Part 2 browser acceptance: LIVE contract search (search-as-you-type).

Drives the real running frontend in the real browser (nothing is mocked):

    --mode accept   O debounce · P loading · Q results · R empty · S clear ·
                     T stale-request cancellation · the 8 spec queries ·
                     incremental typing (b → ba → bal → bala → balanced)
    --mode fail      Elasticsearch stopped: explicit search error, page not
                     frozen (normal listing still rendered)
    --mode recover   Elasticsearch restarted: Retry actually recovers

Prints PASS/FAIL per assertion and a machine-readable JSON summary.
"""
import argparse
import json
import time
import urllib.request

from cdp_shot import CDP, list_pages

QUERIES = [
    "vend", "balanced", "saas", "nda", "employment", "low risk",
    "adversarial", "balnced",
]

INPUT_JS = """(() => {
  const input = document.querySelector('input[aria-label="Search contracts by filename"]');
  if (!input) return 'missing';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, %s);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return input.value;
})()"""

INSTRUMENT_JS = """(() => {
  if (window.__searchInstrumented) return 'already';
  window.__searchRequests = [];
  window.__searchAborted = 0;
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/api/v1/search/contracts') !== -1) {
      const entry = { url, started: Date.now(), finished: null, status: null, aborted: false };
      window.__searchRequests.push(entry);
      return orig.apply(this, arguments).then(
        (res) => { entry.finished = Date.now(); entry.status = res.status; return res; },
        (err) => {
          entry.finished = Date.now();
          if (err && err.name === 'AbortError') { entry.aborted = true; window.__searchAborted += 1; }
          throw err;
        }
      );
    }
    return orig.apply(this, arguments);
  };
  return 'ok';
})()"""


class Tester:
    def __init__(self, cdp: CDP):
        self.cdp = cdp
        self.results = []

    def check(self, name: str, ok: bool, detail: str = ""):
        self.results.append({"name": name, "pass": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        return ok

    @property
    def failed(self):
        return [r for r in self.results if not r["pass"]]


def ev(cdp, expr):
    out = cdp.evaluate(f"JSON.stringify(({expr}))")
    try:
        return json.loads(out) if out is not None else None
    except Exception:
        return None


def status_text(cdp) -> str:
    return (cdp.evaluate(
        "document.getElementById('contract-search-status')?.innerText || ''") or "").strip()


def input_value(cdp) -> str:
    return (cdp.evaluate(
        "(document.querySelector('input[aria-label=\"Search contracts by filename\"]')||{}).value"
        " || ''") or "")


def rows(cdp) -> list:
    """Filenames rendered in the contract table (whichever table is active).

    The redesigned table (Phase 6) puts the importance star in the first
    column, so the filename anchor is located by its own class rather than
    by position.
    """
    return ev(cdp, """[...document.querySelectorAll('tbody tr a.contract-filename')]
        .map(a => a.innerText.trim())""") or []


def requests(cdp) -> list:
    return ev(cdp, "window.__searchRequests || []") or []


def aborted(cdp) -> int:
    return ev(cdp, "window.__searchAborted || 0") or 0


def set_query(cdp, value: str, char_delay: float = 0.0):
    """Type like a human: one input event per keystroke."""
    if char_delay <= 0:
        cdp.evaluate(INPUT_JS % json.dumps(value))
        return
    for i in range(1, len(value) + 1):
        cdp.evaluate(INPUT_JS % json.dumps(value[:i]))
        time.sleep(char_delay)


def wait_until(cdp, pred, timeout: float = 8.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def settle_search(cdp, timeout: float = 10.0):
    """Wait until the search is neither typing nor in flight."""

    def done():
        t = status_text(cdp)
        return (t.endswith("match") or "match “" in t or "No contracts match" in t
                or "unavailable" in t.lower())

    return wait_until(cdp, done, timeout=timeout)


def throttling(cdp, latency_ms: int):
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False,
        "latency": latency_ms,
        "downloadThroughput": -1,
        "uploadThroughput": -1,
        "connectionType": "wifi",
    })


def reset_counts(cdp):
    cdp.evaluate("window.__searchRequests = []; window.__searchAborted = 0; 'ok'")


def navigate(cdp, token: str, url: str, wait_for: str, timeout: float = 45.0):
    cdp.send("Page.navigate", {"url": "http://localhost:3000/login"})
    wait_until(cdp, lambda: cdp.evaluate("document.readyState") == "complete", 20)
    cdp.evaluate(f"localStorage.setItem('token', {json.dumps(token)})")
    cdp.send("Page.navigate", {"url": url})
    ok = wait_until(cdp, lambda: wait_for in (
        cdp.evaluate("document.body ? document.body.innerText : ''") or ""), timeout)
    time.sleep(0.8)
    return ok


# --------------------------------------------------------------------- modes


def mode_accept(cdp, t: Tester):
    reset_counts(cdp)

    # ---- O: debounce (one request per pause, not one per keystroke) --------
    set_query(cdp, "")
    time.sleep(0.4)
    reset_counts(cdp)
    t0 = time.time()
    set_query(cdp, "balanced", char_delay=0.06)      # 8 keystrokes, 60ms apart
    wait_until(cdp, lambda: bool(rows(cdp)), timeout=6)
    time.sleep(0.9)
    n = len(requests(cdp))
    t.check("O debounce: 8 keystrokes → 1 request", n == 1,
            f"{n} search request(s) in {time.time()-t0:.2f}s")

    # ---- P: visible loading state while a request is in flight -------------
    set_query(cdp, "")
    time.sleep(0.5)
    throttling(cdp, 900)
    reset_counts(cdp)
    set_query(cdp, "employment", char_delay=0.03)
    loading_seen = wait_until(cdp, lambda: "Searching contracts" in status_text(cdp), timeout=6)
    t.check("P loading state visible while searching", loading_seen,
            f"status line: {status_text(cdp)!r}")
    settle_search(cdp, timeout=12)
    throttling(cdp, 0)
    t.check("Q result state: matching contract rendered",
            rows(cdp) == ["04_employment_agreement.pdf"], f"rows={rows(cdp)}")
    t.check("Q result state: status line reports the match",
            "1 contract match" in status_text(cdp), f"status={status_text(cdp)!r}")

    # ---- R: empty state ----------------------------------------------------
    set_query(cdp, "nonexistentzzz", char_delay=0.02)
    settle_search(cdp, timeout=10)
    body = cdp.evaluate("document.body.innerText") or ""
    t.check("R empty state: 'No contracts match' shown",
            "No contracts match" in body and "nonexistentzzz" in body,
            f"status={status_text(cdp)!r}")

    # ---- S: clear button resets to the normal listing ----------------------
    clear_btn = cdp.evaluate(
        "!!document.querySelector('button[aria-label=\"Clear search\"]')")
    cdp.evaluate("document.querySelector('button[aria-label=\"Clear search\"]')?.click()")
    time.sleep(1.0)
    t.check("S clear button exists and resets to the full listing",
            clear_btn and input_value(cdp) == "" and len(rows(cdp)) >= 7
            and status_text(cdp) == "",
            f"clear={clear_btn} input={input_value(cdp)!r} rows={len(rows(cdp))}")

    # ---- keyboard: Escape clears, Enter runs before the debounce -----------
    set_query(cdp, "nda", char_delay=0.03)
    settle_search(cdp)
    cdp.evaluate("""(() => {
      const i = document.querySelector('input[aria-label="Search contracts by filename"]');
      i.focus();
      i.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      return 'ok';
    })()""")
    time.sleep(0.8)
    t.check("Escape clears the search", input_value(cdp) == "" and len(rows(cdp)) >= 7,
            f"input={input_value(cdp)!r} rows={len(rows(cdp))}")

    set_query(cdp, "")
    time.sleep(0.5)
    reset_counts(cdp)
    set_query(cdp, "nda", char_delay=0.0)   # one shot, no debounce wait
    t_enter_ms = int(time.time() * 1000)
    cdp.evaluate("""(() => {
      const i = document.querySelector('input[aria-label="Search contracts by filename"]');
      i.focus();
      i.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      return 'ok';
    })()""")
    wait_until(cdp, lambda: bool(rows(cdp)), timeout=6)
    reqs = requests(cdp)
    enter_delay = (reqs[0]["started"] - t_enter_ms) if reqs else None
    t.check("Enter runs the search immediately (no debounce wait)",
            bool(reqs) and enter_delay is not None and enter_delay < 200
            and rows(cdp) == ["03_nda.pdf"],
            f"requests={len(reqs)} enter→request={enter_delay}ms rows={rows(cdp)}")

    # ---- T: stale request cancellation (a slower, older query loses) -------
    set_query(cdp, "")
    time.sleep(0.5)
    throttling(cdp, 1500)
    reset_counts(cdp)
    set_query(cdp, "vend", char_delay=0.02)          # fires after 280ms…
    time.sleep(0.45)                                  # …still in flight (throttled)
    set_query(cdp, "balanced", char_delay=0.02)       # supersedes it
    settle_search(cdp, timeout=15)
    throttling(cdp, 0)
    t.check("T stale request is aborted",
            aborted(cdp) >= 1, f"aborted={aborted(cdp)} requests={len(requests(cdp))}")
    t.check("T final results belong to the LATEST query (no stale overwrite)",
            rows(cdp) == ["02_balanced_saas_agreement.pdf"], f"rows={rows(cdp)}")
    time.sleep(1.5)
    t.check("T stale response never arrives later",
            rows(cdp) == ["02_balanced_saas_agreement.pdf"], f"rows={rows(cdp)}")

    # ---- the 8 spec queries, typed like a user -----------------------------
    set_query(cdp, "")
    time.sleep(0.6)
    accepted = []
    for q in QUERIES:
        reset_counts(cdp)
        set_query(cdp, q, char_delay=0.03)
        settle_search(cdp, timeout=10)
        got = rows(cdp)
        st = status_text(cdp)
        reqs = requests(cdp)
        ms = 0
        if reqs and reqs[0].get("finished") and reqs[0].get("started"):
            ms = reqs[0]["finished"] - reqs[0]["started"]
        accepted.append({"query": q, "rows": got, "status": st, "requests": len(reqs),
                         "roundtrip_ms": ms})
        print(f"    {q!r:14} → {len(reqs)} req(s), {ms}ms, rows={got}")
        time.sleep(0.3)

    def only(rows_, *names):
        return rows_ == list(names)

    expect = {
        "vend": lambda r: sorted(r) == ["01_clear_high_risk_vendor.pdf",
                                        "01_high_risk_vendor_agreement.pdf"],
        "balanced": lambda r: only(r, "02_balanced_saas_agreement.pdf"),
        "saas": lambda r: only(r, "02_balanced_saas_agreement.pdf"),
        "nda": lambda r: only(r, "03_nda.pdf"),
        "employment": lambda r: only(r, "04_employment_agreement.pdf"),
        "low risk": lambda r: r and r[0] == "05_low_risk_contract.pdf",
        "adversarial": lambda r: only(r, "06_adversarial_indirect_risk_contract.pdf"),
        "balnced": lambda r: only(r, "02_balanced_saas_agreement.pdf"),
    }
    for entry in accepted:
        ok = expect[entry["query"]](entry["rows"])
        t.check(f"20 query {entry['query']!r}", ok, f"rows={entry['rows']}")

    # ---- incremental typing (spec Step 20: 'b' → 'ba' → 'bal' → ...) -------
    set_query(cdp, "")
    time.sleep(0.6)
    steps = []
    for prefix in ["b", "ba", "bal", "bala", "balan", "balanced"]:
        set_query(cdp, prefix)
        settle_search(cdp, timeout=8)
        steps.append({"prefix": prefix, "rows": rows(cdp), "status": status_text(cdp)})
        time.sleep(0.35)
    print("    incremental:", json.dumps(steps))
    t.check("20 incremental typing updates automatically (b → balanced)",
            steps[-1]["rows"] == ["02_balanced_saas_agreement.pdf"]
            and all(s["rows"] for s in steps),
            f"steps={[s['rows'] for s in steps]}")
    t.check("20 every keystroke produced results (single letter works)",
            all(len(s["rows"]) >= 1 for s in steps),
            f"counts={[len(s['rows']) for s in steps]}")

    set_query(cdp, "")
    time.sleep(0.8)
    cdp.screenshot("/tmp/opencode/p2_accept_idle.png")
    set_query(cdp, "risk vendor")
    settle_search(cdp)
    cdp.screenshot("/tmp/opencode/p2_accept_results.png")
    set_query(cdp, "nonexistentzzz")
    settle_search(cdp)
    cdp.screenshot("/tmp/opencode/p2_accept_empty.png")
    set_query(cdp, "")
    return accepted, steps


def mode_fail(cdp, t: Tester):
    reset_counts(cdp)
    set_query(cdp, "")
    time.sleep(0.6)
    set_query(cdp, "balanced", char_delay=0.03)
    settle_search(cdp, timeout=20)
    body = cdp.evaluate("document.body.innerText") or ""
    t.check("22 search failure shows a search-specific error",
            "temporarily unavailable" in body or "unavailable" in status_text(cdp).lower(),
            f"status={status_text(cdp)!r}")
    t.check("22 error offers Retry", bool(ev(cdp,
        "[...document.querySelectorAll('button')].some(b => /try again|retry/i.test(b.innerText))")),
        "")
    listed = rows(cdp)
    t.check("22 normal contract list stays visible (page not frozen)",
            len(listed) >= 7, f"rows={len(listed)}")
    t.check("22 page still interactive (input responds)",
            input_value(cdp) == "balanced", f"input={input_value(cdp)!r}")
    cdp.screenshot("/tmp/opencode/p2_fail_error.png")


def mode_recover(cdp, t: Tester):
    settle_search(cdp, timeout=10)
    clicked = cdp.evaluate("""(() => {
      const b = [...document.querySelectorAll('button')]
        .find(x => /try again|retry/i.test(x.innerText));
      if (!b) return 'none';
      b.click();
      return 'clicked';
    })()""")
    recovered = wait_until(
        cdp,
        lambda: "temporarily unavailable" not in (cdp.evaluate("document.body.innerText") or ""),
        timeout=25)
    t.check("22 Retry recovers after Elasticsearch restarts",
            clicked == "clicked" and recovered, f"clicked={clicked}")
    t.check("22 recovered search returns results again",
            wait_until(cdp, lambda: rows(cdp) == ["02_balanced_saas_agreement.pdf"],
                       timeout=25),
            f"rows={rows(cdp)}")
    cdp.screenshot("/tmp/opencode/p2_recover.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", default="/tmp/opencode/org8_tok.txt")
    ap.add_argument("--url", default="http://localhost:3000/dashboard")
    ap.add_argument("--mode", default="accept", choices=["accept", "fail", "recover"])
    a = ap.parse_args()

    token = open(a.token_file).read().strip()
    pages = list_pages()
    if not pages:
        req = urllib.request.Request("http://localhost:9222/json/new?about:blank",
                                     method="PUT")
        with urllib.request.urlopen(req) as r:
            pages = [json.load(r)]
    cdp = CDP(pages[0]["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")

    # recover must observe the SAME page that failed — reloading would wipe
    # the error state and make the Retry assertion meaningless.
    if a.mode == "recover":
        if not wait_until(cdp, lambda: "Upload Contract" in (
                cdp.evaluate("document.body ? document.body.innerText : ''") or ""), 10):
            print("FAIL: dashboard page (left in error state) is not open")
            raise SystemExit(2)
    elif not navigate(cdp, token, a.url, wait_for="Upload Contract"):
        print("FAIL: dashboard never rendered")
        raise SystemExit(2)
    cdp.evaluate(INSTRUMENT_JS)
    t = Tester(cdp)

    extra = None
    if a.mode == "accept":
        extra = mode_accept(cdp, t)
    elif a.mode == "fail":
        mode_fail(cdp, t)
    else:
        mode_recover(cdp, t)

    summary = {"mode": a.mode, "passed": len(t.results) - len(t.failed),
               "failed": len(t.failed), "results": t.results}
    if extra:
        summary["queries"], summary["incremental"] = extra
    with open(f"/tmp/opencode/p2_search_{a.mode}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps({k: v for k, v in summary.items()
                      if k in ("mode", "passed", "failed")}))
    if t.failed:
        print("FAILURES:", json.dumps(t.failed, indent=2))
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
