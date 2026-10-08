#!/usr/bin/env python3
"""Drive headless Chrome over the DevTools protocol.

Loads a page with an authenticated token (injected into localStorage on the
app origin), waits for the client-side data fetches, then records:
  * the rendered body text            (<out>.txt)
  * screenshots of the top / summary /
    risk-assessment sections          (<out>_*.png)
  * every API response status seen
    on the wire (incl. risk-assessment)

Nothing is faked: the text and images come from the real running bundle.
"""
import argparse
import base64
import json
import time
import urllib.request

from websockets.sync.client import connect


class CDP:
    def __init__(self, url: str):
        self.ws = connect(url, max_size=None)
        self.i = 0
        self.events = []

    def send(self, method: str, params: dict | None = None, timeout: float = 30):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            except TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise RuntimeError(f"timeout waiting for {method}")

    def evaluate(self, expr: str, timeout: float = 30):
        r = self.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout,
        )
        return (r.get("result") or {}).get("value")

    def api_statuses(self):
        out = []
        for ev in self.events:
            if ev.get("method") != "Network.responseReceived":
                continue
            resp = ev["params"]["response"]
            url = resp.get("url", "")
            if "/api/v1/" in url:
                out.append((resp.get("status"), url))
        return out

    def screenshot(self, path: str):
        shot = self.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))


def list_pages():
    with urllib.request.urlopen("http://localhost:9222/json/list") as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True, help="output prefix")
    ap.add_argument("--wait-for", default="Risk assessment")
    ap.add_argument("--timeout", type=float, default=30)
    a = ap.parse_args()

    pages = list_pages()
    if not pages:
        req = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req) as r:
            pages = [json.load(r)]
    cdp = CDP(pages[0]["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")

    # 1. land on the app origin so localStorage belongs to it
    cdp.send("Page.navigate", {"url": "http://localhost:3000/login"})
    for _ in range(60):
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.25)
    cdp.evaluate(f"localStorage.setItem('token', {json.dumps(a.token)})")

    # 2. open the contract page and wait for the client-side fetches
    cdp.send("Page.navigate", {"url": a.url})
    deadline = time.time() + a.timeout
    text = ""
    while time.time() < deadline:
        time.sleep(0.5)
        text = cdp.evaluate("document.body ? document.body.innerText : ''") or ""
        if a.wait_for in text:
            break
    time.sleep(1.0)  # let stragglers (summary / lifecycle) settle
    text = cdp.evaluate("document.body ? document.body.innerText : ''") or ""
    with open(f"{a.out}.txt", "w", encoding="utf-8") as f:
        f.write(text)

    statuses = cdp.api_statuses()
    with open(f"{a.out}.network.txt", "w", encoding="utf-8") as f:
        for status, url in statuses:
            f.write(f"{int(status)} {url}\n")

    # 3. screenshots: top of page, summary, risk assessment
    cdp.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.4)
    cdp.screenshot(f"{a.out}_top.png")
    for sel, name in (("#summary", "summary"), ("#risk-assessment", "risk")):
        cdp.evaluate(
            f"document.querySelector({json.dumps(sel)})?.scrollIntoView({{block:'start'}})"
        )
        time.sleep(0.5)
        cdp.screenshot(f"{a.out}_{name}.png")

    risk_calls = [s for s in statuses if "risk-assessment" in s[1]]
    print(f"rendered text: {len(text)} chars -> {a.out}.txt")
    print(f"API responses seen: {len(statuses)}  (risk-assessment: {risk_calls})")
    print("first lines:", " | ".join(text.split("\n")[:6]))


if __name__ == "__main__":
    main()
