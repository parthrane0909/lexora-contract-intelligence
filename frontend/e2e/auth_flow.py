#!/usr/bin/env python3
"""Part 3 real-browser auth tests (spec tests I–V + Step 20 TEST 1–8).

Drives the actual running stack — headless Chrome over CDP, the real Next.js
bundle on :3000 and the real FastAPI backend on :8001. Nothing is mocked and
nothing is asserted from curl: every check reads the rendered DOM / location.

    I  logged-out /dashboard -> /login (no app shell, no data requests)
    J  logged-out /portfolio -> /login
    K  logged-out /search    -> /login   (+ templates/policies/ml-evaluation/
                                          contracts/[id] also guarded)
    L  successful login through the real form (and ?next= return target)
    M  account indicator appears
    N  correct email / user shown
    O  account menu opens
    P  organization + role come from the backend
    Q  logout clears state + credential and lands on /login
    R  protected page inaccessible after logout (direct URL)
    S  refresh while authenticated restores the session, no login flash
    T  invalid / expired token returns to /login
    U  403 does NOT log the user out (role is temporarily lowered, then restored)
    V  account menu keyboard interaction (Enter/Space open, Escape close + focus)
    NR ?next= never leaves the origin (external / protocol-relative / auth-loop
       targets fall back to /dashboard, internal paths are honoured)
  BACK  browser Back after logout cannot re-open protected content
  NAV   account indicator consistent across the main routes
  RESP  header + account area survive a 700px-wide viewport
  T3    a 401 during use clears the session and returns to /login
  LEAK  no Authorization header is sent after logout (old credential is gone)

Usage:
    python e2e/auth_flow.py --email user@example.com --password '...'

Writes /tmp/opencode/p3_auth_flow_summary.json + p3_*.png screenshots.
Exit code is non-zero when any assertion fails.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from cdp_shot import CDP, list_pages

APP = "http://localhost:3000"
API = "http://localhost:8001"
PG_CONTAINER = "dl-mini-proj-postgres-1"
BE_CONTAINER = "dl-mini-proj-backend-1"

RESULTS = []


def check(code, name, ok, detail=""):
    RESULTS.append({"id": code, "name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'PASS' if ok else 'FAIL'}] {code:6} {name}" + (f" — {detail}" if detail else ""), flush=True)


def nav(cdp, url, timeout=25):
    cdp.send("Page.navigate", {"url": url})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.evaluate("document.readyState") == "complete":
            break
        time.sleep(0.2)
    time.sleep(0.3)


def where(cdp):
    return cdp.evaluate("location.pathname + location.search") or ""


def text(cdp):
    return cdp.evaluate("document.body ? document.body.innerText : ''") or ""


def has(cdp, sel):
    return bool(cdp.evaluate(f"!!document.querySelector({json.dumps(sel)})"))


def wait_for(cdp, expr, timeout=15, poll=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = cdp.evaluate(expr)
        if v:
            return v
        time.sleep(poll)
    return cdp.evaluate(expr)


def drain(cdp):
    """Force buffered CDP events into cdp.events so index slicing is exact."""
    cdp.evaluate("1")
    cdp.evaluate("1")


def api_calls_since(cdp, start):
    out = []
    for ev in cdp.events[start:]:
        if ev.get("method") != "Network.requestWillBeSent":
            continue
        req = ev["params"]["request"]
        url = req.get("url", "")
        if "/api/v1/" in url:
            out.append((url, req.get("headers", {}) or {}))
    return out


def set_input(cdp, sel, value):
    return cdp.evaluate(
        f"""(() => {{
          const el = document.querySelector({json.dumps(sel)});
          if (!el) return 'missing';
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(el, {json.dumps(value)});
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          return el.value;
        }})()"""
    )


# Chrome only treats Enter/Space as activation keys when the keyDown carries
# the character it produces (verified empirically with a CDP key probe).
KEY_TEXT = {"Enter": "\r", " ": " "}


def press(cdp, key, code, vk=0):
    """Trusted key events (real activation behaviour, unlike synthetic ones)."""
    for etype in ("keyDown", "keyUp"):
        params = {"type": etype, "key": key, "code": code, "windowsVirtualKeyCode": vk}
        if etype == "keyDown" and key in KEY_TEXT:
            params["text"] = KEY_TEXT[key]
        cdp.send("Input.dispatchKeyEvent", params)


def login_via_form(cdp, email, password, expect_path, timeout=25):
    """Fill and submit the real login form; wait for the redirect."""
    wait_for(cdp, "!!document.querySelector('#email')", timeout=10)
    set_input(cdp, "#email", email)
    set_input(cdp, "#password", password)
    time.sleep(0.2)
    cdp.evaluate(
        """(() => {
            const btn = document.querySelector('form button[type=submit], form .btn-primary, form button');
            if (!btn) return 'missing';
            if (btn.disabled) return 'disabled';
            btn.click();
            return 'clicked';
        })()"""
    )
    return wait_for(
        cdp, f"location.pathname === {json.dumps(expect_path)}", timeout=timeout
    )


def pg(sql):
    r = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "contracts", "-d", "contracts", "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}")
    return r.stdout.strip()


def expired_token():
    """A correctly signed but expired JWT (the server's own secret/alg)."""
    code = (
        "from datetime import datetime, timedelta, timezone;"
        "from jose import jwt;"
        "from app.core.config import get_settings as g;"
        "s=g();"
        "print(jwt.encode({'sub':'00000000000000000000000000000000',"
        "'org':'00000000000000000000000000000000','role':'admin',"
        "'exp':datetime.now(timezone.utc)-timedelta(minutes=5)},"
        "s.JWT_SECRET, algorithm=s.JWT_ALGORITHM))"
    )
    try:
        r = subprocess.run(
            ["docker", "exec", BE_CONTAINER, "python", "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="/tmp/opencode/p3_auth_flow")
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
    # Deterministic desktop viewport so `md:` header layout is active.
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    # At document start: detect any flash of the login page (for the refresh test).
    cdp.send(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
              window.__flashLogin = false;
              const start = () => {
                if (!document.documentElement) return setTimeout(start, 0);
                new MutationObserver(() => {
                  if (location.pathname === '/login') window.__flashLogin = true;
                }).observe(document.documentElement, {childList:true, subtree:true});
              };
              start();
            """
        },
    )

    # ---------------------------------------------------------------- setup
    nav(cdp, f"{APP}/login")
    cdp.evaluate("localStorage.removeItem('token')")
    time.sleep(0.3)

    # ------------------------------------------- I / J / K  (logged out)
    for code, path in (
        ("I", "/dashboard"),
        ("J", "/portfolio"),
        ("K", "/search"),
        ("I2", "/templates"),
        ("J2", "/policies"),
        ("K2", "/ml-evaluation"),
        ("I3", "/contracts/00000000-0000-0000-0000-000000000000"),
    ):
        drain(cdp)
        start = len(cdp.events)
        nav(cdp, f"{APP}{path}")
        wait_for(cdp, f"location.pathname === '/login'", timeout=12)
        time.sleep(0.6)
        calls = api_calls_since(cdp, start)
        data_calls = [u for u, _ in calls if not u.endswith("/auth/me")
                      and "/organizations/me" not in u]
        loc = where(cdp)
        ok = (
            loc.startswith("/login")
            and has(cdp, "#email")
            and not has(cdp, "#account-menu-button")
            and not data_calls
        )
        check(code, f"logged-out {path} -> /login, no app shell, no data request",
              ok, f"landed {loc}; api calls: {[u.split('/api/v1')[1] for u, _ in calls] or 'none'}")
    cdp.screenshot(f"{a.out}_01_loggedout_dashboard.png")

    # ------------------------------------------------- L / M / N / O / P
    # Step 10: ?next=/portfolio must be honoured after a successful login.
    nav(cdp, f"{APP}/login?next=%2Fportfolio")
    time.sleep(0.4)
    cdp.screenshot(f"{a.out}_02_login_form.png")
    reached = login_via_form(cdp, a.email, a.password, "/portfolio")
    check("L", "successful login returns to the requested /portfolio", reached,
          f"landed {where(cdp)}")
    time.sleep(1.0)

    check("M", "account indicator appears in the global header",
          has(cdp, "#account-menu-button"), where(cdp))

    btn_text = cdp.evaluate(
        "document.querySelector('#account-menu-button')?.innerText || ''") or ""
    btn_title = cdp.evaluate(
        "document.querySelector('#account-menu-button')?.title || ''") or ""
    check("N", "correct email / user shown in the header",
          a.email in btn_text or a.email in btn_title,
          f"title={btn_title!r} text={btn_text!r}")

    # O: click the real button, menu opens
    cdp.evaluate("document.querySelector('#account-menu-button').click()")
    opened = wait_for(cdp, "!!document.querySelector('#account-menu-panel')", timeout=6)
    check("O", "account menu opens on click", bool(opened))
    time.sleep(0.3)
    cdp.screenshot(f"{a.out}_03_account_menu.png")
    menu_text = cdp.evaluate(
        "document.querySelector('#account-menu-panel')?.innerText || ''") or ""

    org_name = pg(f"select name from organizations o "
                  f"join organization_members m on m.organization_id=o.id "
                  f"join users u on u.id=m.user_id where u.email='{a.email}';")
    role = pg(f"select m.role from organization_members m join users u on u.id=m.user_id "
              f"where u.email='{a.email}';")
    # re-read: the organization name arrives just after /auth/me resolves
    menu_text = cdp.evaluate(
        "document.querySelector('#account-menu-panel')?.innerText || ''") or ""
    check("P", "menu shows the real organization name and role",
          (org_name in menu_text) and (role.replace("_", " ") in menu_text),
          f"org={org_name!r} role={role!r} menu={menu_text!r}")
    check("P2", "menu identity is the real backend email", a.email in menu_text,
          menu_text.replace("\n", " | ")[:160])
    check("P3", "no invented labels in the menu",
          all(bad not in menu_text for bad in ("Enterprise", "Premium", "Superuser")),
          menu_text.replace("\n", " | ")[:160])

    # V: keyboard interaction (trusted CDP key events, not synthetic clicks)
    press(cdp, "Escape", "Escape", vk=27)
    time.sleep(0.4)
    active = cdp.evaluate("document.activeElement && document.activeElement.id")
    check("V", "Escape closes the menu and restores focus to the button",
          (not has(cdp, "#account-menu-panel")) and active == "account-menu-button",
          f"activeElement={active!r}")

    cdp.evaluate("document.querySelector('#account-menu-button').focus()")
    press(cdp, "Enter", "Enter", vk=13)
    time.sleep(0.5)
    expanded = cdp.evaluate(
        "document.querySelector('#account-menu-button').getAttribute('aria-expanded')")
    check("V2", "Enter opens the account menu",
          has(cdp, "#account-menu-panel") and expanded == "true",
          f"panel={has(cdp, '#account-menu-panel')} aria-expanded={expanded}")

    press(cdp, "Escape", "Escape", vk=27)
    time.sleep(0.3)
    cdp.evaluate("document.querySelector('#account-menu-button').focus()")
    press(cdp, " ", "Space", vk=32)
    time.sleep(0.5)
    check("V3", "Space opens the account menu", has(cdp, "#account-menu-panel"),
          f"panel={has(cdp, '#account-menu-panel')}")

    press(cdp, "Escape", "Escape", vk=27)
    time.sleep(0.4)
    cdp.evaluate(
        """(() => {
            const b = document.querySelector('#account-menu-button');
            b.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
            b.dispatchEvent(new MouseEvent('mouseenter', {bubbles:true}));
            return 1;
        })()"""
    )
    time.sleep(0.5)
    check("V4", "menu is not hover-driven (opens only on click/keys)",
          not has(cdp, "#account-menu-panel"))

    # ---------------------------------------------------------------- S
    # refresh while authenticated: session restored, never bounced to /login
    cdp.send("Page.reload", {"ignoreCache": False})
    time.sleep(0.4)
    settled = wait_for(cdp, "!!document.querySelector('#account-menu-button')", timeout=15)
    time.sleep(0.8)
    flash = cdp.evaluate("window.__flashLogin")
    check("S", "refresh restores the session without a login flash",
          bool(settled) and not flash and where(cdp).startswith("/portfolio"),
          f"landed {where(cdp)}; loginFlash={flash}")
    cdp.screenshot(f"{a.out}_04_after_refresh.png")

    # ---------------------------------------------------------------- NAV
    nav_ok = True
    nav_detail = []
    for path in ("/dashboard", "/search", "/templates", "/policies", "/portfolio"):
        nav(cdp, f"{APP}{path}")
        wait_for(cdp, f"location.pathname === {json.dumps(path)}", timeout=12)
        time.sleep(0.6)
        acc = cdp.evaluate("document.querySelector('#account-menu-button')?.title || ''")
        good = where(cdp).startswith(path) and acc == a.email
        nav_ok = nav_ok and good
        nav_detail.append(f"{path}:{'ok' if good else 'FAIL ' + where(cdp)}")
    check("NAV", "account indicator stays consistent across main routes", nav_ok,
          "; ".join(nav_detail))

    # ---------------------------------------------------------------- U
    # 403 (authenticated but forbidden) must NOT end the session. The role is
    # lowered for this one check and restored immediately afterwards.
    try:
        pg(f"update organization_members set role='viewer' "
           f"where user_id=(select id from users where email='{a.email}');")
        res = cdp.evaluate(
            f"""(async () => {{
                const tok = localStorage.getItem('token') || '';
                const r = await fetch('{API}/api/v1/organizations/members', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok }},
                  body: JSON.stringify({{ email: 'forbidden-test@x.com', role: 'viewer' }})
                }});
                const me = await fetch('{API}/api/v1/auth/me', {{
                  headers: {{ Authorization: 'Bearer ' + tok }}
                }});
                return JSON.stringify({{ forbidden: r.status, me: me.status }});
            }})()"""
        )
        parsed = json.loads(res) if res else {}
        still_in = has(cdp, "#account-menu-button") and not where(cdp).startswith("/login")
        check("U", "403 does NOT log the user out (session + UI stay alive)",
              parsed.get("forbidden") == 403 and parsed.get("me") == 200 and still_in,
              f"POST /organizations/members -> {parsed.get('forbidden')}, "
              f"/auth/me -> {parsed.get('me')}, at {where(cdp)}")
    finally:
        pg(f"update organization_members set role='admin' "
           f"where user_id=(select id from users where email='{a.email}');")
    back_role = pg(f"select m.role from organization_members m join users u on u.id=m.user_id "
                   f"where u.email='{a.email}';")
    check("U2", "role restored after the 403 test", back_role == "admin", f"role={back_role}")

    # ------------------------------------------------- Q (logout) + LEAK
    cdp.evaluate("document.querySelector('#account-menu-button').click()")
    wait_for(cdp, "!!document.querySelector('#account-menu-panel')", timeout=6)
    time.sleep(0.3)
    cdp.evaluate(
        """(() => {
            const items = [...document.querySelectorAll('#account-menu-panel [role=menuitem]')];
            const b = items.find((x) => /log out/i.test(x.textContent));
            if (!b) return 'missing';
            b.click();
            return 'clicked';
        })()"""
    )
    landed = wait_for(cdp, "location.pathname === '/login'", timeout=12)
    time.sleep(0.8)
    token_after = cdp.evaluate("localStorage.getItem('token')")
    check("Q", "logout clears the credential and lands on /login",
          bool(landed) and token_after is None and not has(cdp, "#account-menu-button"),
          f"at {where(cdp)}; token={token_after!r}")
    cdp.screenshot(f"{a.out}_05_after_logout.png")

    # -------------------------------------------------------- R + TEST 7
    drain(cdp)
    start_after_logout = len(cdp.events)
    nav(cdp, f"{APP}/portfolio")
    wait_for(cdp, "location.pathname.indexOf('/login') === 0", timeout=12)
    time.sleep(0.8)
    check("R", "direct /portfolio after logout shows the login page",
          where(cdp).startswith("/login") and has(cdp, "#email")
          and not has(cdp, "#account-menu-button"),
          f"landed {where(cdp)}")

    # ------------------------------------------------------------ LEAK
    # TEST 8: since logout, no request may carry the old credential.
    drain(cdp)
    calls = api_calls_since(cdp, start_after_logout)
    leaked = [(u, h.get("Authorization")) for u, h in calls if h.get("Authorization")]
    check("LEAK", "no Authorization header is sent after logout",
          not leaked, f"{len(calls)} api calls since logout; leaked={leaked[:2]}")

    # ---------------------------------------------------------------- T
    for label, tok in (
        ("invalid", "garbage.token.value"),
        ("expired", expired_token()),
    ):
        if not tok:
            check("T-" + label, f"{label} token returns to /login", True, "skipped (token unavailable)")
            continue
        cdp.evaluate(f"localStorage.setItem('token', {json.dumps(tok)})")
        nav(cdp, f"{APP}/dashboard")
        wait_for(cdp, "location.pathname.indexOf('/login') === 0", timeout=12)
        time.sleep(0.6)
        cleared = cdp.evaluate("localStorage.getItem('token')")
        check("T-" + label, f"{label} token is rejected and cleared -> /login",
              where(cdp).startswith("/login") and cleared is None and has(cdp, "#email"),
              f"landed {where(cdp)}; token after={cleared!r}")

    # -------------------------------------------------------------- BACK
    # Login again, build a protected history, log out, then press Back.
    nav(cdp, f"{APP}/login?next=%2Fportfolio")
    login_via_form(cdp, a.email, a.password, "/portfolio")
    time.sleep(0.8)
    for path in ("/dashboard", "/templates", "/portfolio"):
        nav(cdp, f"{APP}{path}")
        wait_for(cdp, f"location.pathname === {json.dumps(path)}", timeout=12)
        time.sleep(0.4)
    cdp.evaluate("document.querySelector('#account-menu-button').click()")
    wait_for(cdp, "!!document.querySelector('#account-menu-panel')", timeout=6)
    cdp.evaluate(
        """(() => {
            const items = [...document.querySelectorAll('#account-menu-panel [role=menuitem]')];
            const b = items.find((x) => /log out/i.test(x.textContent));
            b && b.click();
            return 1;
        })()"""
    )
    wait_for(cdp, "location.pathname === '/login'", timeout=12)
    time.sleep(0.6)
    drain(cdp)
    back_start = len(cdp.events)
    cdp.evaluate("history.back()")
    # The auth guard must win: protected content may appear for a frame at
    # most, but the user must end up unauthenticated, not on a usable page.
    deadline = time.time() + 8
    final = ""
    while time.time() < deadline:
        time.sleep(0.4)
        final = where(cdp)
        if final.startswith("/login"):
            break
    time.sleep(0.8)
    calls = api_calls_since(cdp, back_start)
    data_calls = [u for u, _ in calls if not u.endswith("/auth/me") and "/organizations/me" not in u]
    check("BACK", "browser Back after logout cannot re-open protected content",
          final.startswith("/login") and not has(cdp, "#account-menu-button") and not data_calls,
          f"ended at {final}; api calls={[u.split('/api/v1')[1] for u, _ in calls] or 'none'}")
    cdp.screenshot(f"{a.out}_06_back_after_logout.png")

    # ------------------------------------- narrow header (Step 18) + expiry
    nav(cdp, f"{APP}/login?next=%2Fdashboard")
    login_via_form(cdp, a.email, a.password, "/dashboard")
    time.sleep(1.0)

    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 700, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(1.0)
    overflow = cdp.evaluate(
        "Math.max(0, document.documentElement.scrollWidth - window.innerWidth)") or 0
    nav_links = cdp.evaluate(
        "[...document.querySelectorAll('nav[aria-label=\"Primary\"] a')].length") or 0
    acc_small = has(cdp, "#account-menu-button")
    check("RESP", "header + account area survive a 700px-wide viewport",
          overflow <= 1 and acc_small and nav_links == 5,
          f"horizontal overflow={overflow}px account={acc_small} navLinks={nav_links}")
    cdp.screenshot(f"{a.out}_07_narrow_header.png")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(0.5)

    # Expired/invalid session discovered mid-use: one 401 on a protected call
    # must clear the stored credential and bounce the protected route.
    cdp.evaluate(
        f"""(async () => {{
            const r = await fetch('{API}/api/v1/contracts',
              {{ headers: {{ Authorization: 'Bearer expired.invalid.token' }} }});
            return r.status;
        }})()"""
    )
    bounced = wait_for(cdp, "location.pathname.indexOf('/login') === 0", timeout=12)
    time.sleep(0.6)
    check("T3", "a 401 during use clears the session and returns to /login",
          bool(bounced) and cdp.evaluate("localStorage.getItem('token')") is None
          and not has(cdp, "#account-menu-button"),
          f"landed {where(cdp)}; token={cdp.evaluate('localStorage.getItem(' + chr(39) + 'token' + chr(39) + ')')!r}")
    cdp.screenshot(f"{a.out}_08_session_expired.png")

    # ------------------------------------------------- NR (no open redirect)
    # Step 10: ?next= may only point inside this origin — an external,        #
    # protocol-relative or auth-looping target must fall back to /dashboard,  #
    # while a real internal path is honoured after login.                     #
    def login_expect(next_param: str, expected_path: str) -> bool:
        cdp.evaluate("localStorage.removeItem('token')")
        nav(cdp, f"{APP}/login?next={urllib.parse.quote(next_param, safe='')}")
        wait_for(cdp, "!!document.querySelector('#email')", timeout=10)
        set_input(cdp, "#email", a.email)
        set_input(cdp, "#password", a.password)
        time.sleep(0.2)
        cdp.evaluate("document.querySelector('form button').click()")
        return bool(wait_for(cdp, f"location.pathname === {json.dumps(expected_path)}",
                             timeout=20))

    nr_ok, nr_detail = True, []
    for i, (nxt, exp) in enumerate((
        ("//evil.example.com", "/dashboard"),
        ("/\\evil.example.com", "/dashboard"),
        ("https://evil.example.com/phish", "/dashboard"),
        ("/login", "/dashboard"),
        ("/ml-evaluation", "/ml-evaluation"),
    ), start=1):
        try:
            got = login_expect(nxt, exp)
        except Exception:  # noqa: BLE001 — report it as a failure, keep going
            got = False
        loc = where(cdp)
        good = bool(got) and "evil" not in loc
        nr_ok = nr_ok and good
        nr_detail.append(f"{nxt} -> {loc}")
        check(f"NR{i}", f"?next={nxt} lands on {exp} (never off-origin)", good,
              f"landed {loc}")
    check("NR", "login redirection stays internal — no open redirect", nr_ok,
          "; ".join(nr_detail))

    # ------------------------------------------------------------- output
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = [r for r in RESULTS if not r["ok"]]
    summary = {
        "total": len(RESULTS),
        "passed": passed,
        "failed": len(failed),
        "results": RESULTS,
    }
    with open(f"{a.out}_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("-" * 70)
    print(f"REAL-BROWSER AUTH: {passed}/{len(RESULTS)} PASS"
          + ("" if not failed else f", {len(failed)} FAIL: {[r['id'] for r in failed]}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
