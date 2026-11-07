"""
Chase US checking (DDA) transactions, ported from projects/chase-scraper/sync.py.

Debit-card purchases post to checking, so they're covered here.
Incremental: stops after a page with nothing new/updated (pages are
newest-first). full=True walks the whole history.
"""

import os

from playwright.async_api import async_playwright

from core import cdp, db

SOURCE = "chase"
HOST = "secure.chase.com"
ENDPOINT_PATH = (
    "/svc/rr/accounts/secure/gateway/deposit-account/transactions/"
    "inquiry-maintenance/etu-dda-transactions/v3/transactions"
)
CURSOR_KEYS = (
    "nextPageOffsetIdentifier",
    "scrollKeyPageOffsetRecordIdentifier",
    "nextRecordIdentifier",
    "scroll-key-page-offset-record-identifier",
)


def normalize(tx: dict) -> dict | None:
    tid = tx.get("transactionIdentifier")
    if not tid:
        return None
    amount = float(tx.get("transactionAmount") or 0)
    if (tx.get("creditDebitCode") or "").upper() == "D" and amount > 0:
        amount = -amount
    desc = tx.get("transactionDescription")
    return {
        "id": str(tid),
        "date": db.iso_date(tx.get("transactionDate")),
        "post_date": db.iso_date(tx.get("transactionPostDate")),
        "description": desc,
        "memo": tx.get("etuStdTransactionTypeName"),
        "amount": amount,
        "currency": "USD",
        "category": tx.get("etuStdExpenseCategoryName"),
        "pending": 1 if tx.get("pendingTransactionIndicator") else 0,
        "kind": db.classify_kind("checking", desc),
        "raw": tx,
    }


DASHBOARD_URL = f"https://{HOST}/web/auth/dashboard"
USER_SEL = "#userId-input-field-input"
PASS_SEL = "#password-input-field-input"
REMEMBER_SEL = "#rememberMe"
SIGNIN_SEL = "#signin-button"
DASHBOARD_SIGNALS = (
    "/svc/rl/accounts/l4/v1/app/data/list",
    "/svc/rr/accounts/secure/v2/account/detail/dda/list",
    ENDPOINT_PATH,
)


async def _login_frame(page):
    for fr in page.frames:
        try:
            if await fr.locator(USER_SEL).is_visible():
                return fr
        except Exception:
            pass
    return None


CHALLENGE_KEYS = ("recognizeuser", "identity", "verify", "otp", "securityquestion",
                  "authenticat", "confirm")
LOGIN_ERROR_RE = r"/(doesn.t match|can.t find|incorrect|try again|locked)/i"


async def _wait_for_state(page, timeout_s: int, submitted: bool = False,
                          dashboard_only: bool = False) -> str:
    """
    Poll until 'dashboard' (transactions API fired), 'challenge' (device/identity
    check), or 'login'. Before submit, a visible form means 'login' at once. After
    submit the form lingers under a spinner, so 'login' is only returned when Chase
    shows an error message, or the form is still there when time runs out.
    """
    # Logged-in signal: the dashboard app's own data call (it no longer loads
    # transactions on its own), or the transactions call itself.
    seen = {"api": False}
    page.on("request", lambda r: seen.__setitem__(
        "api", seen["api"] or any(k in r.url for k in DASHBOARD_SIGNALS)))
    for _ in range(timeout_s * 2):
        if seen["api"]:
            return "dashboard"
        if dashboard_only:  # user is completing verification: wait for the dashboard only
            await page.wait_for_timeout(500)
            continue
        urls = [page.url.lower()] + [f.url.lower() for f in page.frames]
        if any(k in u for u in urls for k in CHALLENGE_KEYS):
            return "challenge"
        fr = await _login_frame(page)
        if fr:
            if not submitted:
                return "login"
            try:
                if await fr.locator(f"text={LOGIN_ERROR_RE}").count():
                    return "login"
            except Exception:
                pass
        await page.wait_for_timeout(500)
    return "login" if await _login_frame(page) else "challenge"


async def _tick_remember_me(fr, log) -> None:
    """Chase's checkbox is a read-only input inside a custom control: click its label."""
    box = fr.locator(REMEMBER_SEL)
    for target in (fr.locator(f"label[for='{REMEMBER_SEL[1:]}']"), fr.get_by_text("Remember me", exact=False)):
        try:
            if await box.is_checked():
                return
            if await target.count():
                await target.first.click(timeout=3000)
                await fr.wait_for_timeout(300)
        except Exception:
            pass
    if not await box.is_checked():
        log("  (couldn't tick 'Remember me'; continuing)")


async def ensure_session(p, show: bool, log=print):
    """Return a logged-in dashboard page, logging in with .env credentials if needed."""
    browser, launched = await cdp.open_chrome(p, show=show)
    page = await cdp.first_page(browser)
    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
    state = await _wait_for_state(page, 45)

    if state == "login":
        user, pw = os.getenv("CHASE_USERNAME"), os.getenv("CHASE_PASSWORD")
        if not (user and pw):
            raise cdp.NeedsLogin("Set CHASE_USERNAME and CHASE_PASSWORD in .env")
        log("  logging in")
        fr = await _login_frame(page)
        await fr.locator(USER_SEL).fill("")
        await fr.locator(USER_SEL).press_sequentially(user, delay=60)
        await fr.locator(PASS_SEL).press_sequentially(pw, delay=60)
        await _tick_remember_me(fr, log)
        await fr.locator(SIGNIN_SEL).click()
        state = await _wait_for_state(page, 90, submitted=True)
        if state == "login":
            await page.screenshot(path=str(db.DATA_DIR / "chase-failure.png"))
            raise cdp.NeedsLogin("Chase rejected the login. Check CHASE_USERNAME/PASSWORD "
                                 "(screenshot: data/chase-failure.png)")

    if state == "challenge":
        if not show:
            await page.screenshot(path=str(db.DATA_DIR / "chase-failure.png"))
            raise cdp.NeedsLogin("Chase wants identity verification. Run once: "
                                 "python sync_all.py chase --show  (then it's remembered)")
        log("  Chase wants verification: complete it in the Chrome window (5 min)...")
        state = await _wait_for_state(page, 300, dashboard_only=True)
        if state != "dashboard":
            raise cdp.NeedsLogin("Verification not completed in time.")
    return browser, page, launched


async def sync(conn, full: bool = False, show: bool = False, log=print) -> tuple[int, int]:
    accounts = [a.strip() for a in os.getenv("CHASE_ACCOUNT_IDS", "").split(",") if a.strip()]
    if not accounts:
        raise RuntimeError("CHASE_ACCOUNT_IDS is empty in .env")

    fetched_at = db.now()
    async with async_playwright() as p:
        browser, page, launched = await ensure_session(p, show, log)
        try:
            return await _sync_accounts(conn, page, accounts, full, fetched_at, log)
        finally:
            await cdp.close_chrome(browser, launched)


# Chase's transactions API now insists on this header (HTTP 400 errorCode 950 without it).
CHANNEL_HEADER = "network-channel-group-code"
CHANNEL_CODE = os.getenv("CHASE_CHANNEL_CODE", "DIGITAL")


async def capture_api_headers(page) -> dict:
    """Reload and copy the security headers (x-jpmc-*) from the account-detail call."""
    async with page.expect_request(lambda r: any(k in r.url for k in DASHBOARD_SIGNALS[1:]) or
                                   "/svc/rr/" in r.url, timeout=30000) as info:
        await page.reload()
    raw = await (await info.value).all_headers()
    headers = {k: v for k, v in raw.items()
               if k.lower() not in cdp.FORBIDDEN_HEADERS and k.lower() != "content-type"}
    if CHANNEL_CODE:
        headers[CHANNEL_HEADER] = CHANNEL_CODE
    return headers


async def _sync_accounts(conn, page, accounts, full, fetched_at, log) -> tuple[int, int]:
    new_total = updated_total = 0
    headers = await capture_api_headers(page)

    for account in accounts:
        db.upsert_account(conn, SOURCE, account, name=f"Chase checking {account[-4:]}",
                          type="checking", currency="USD", institution="Chase")
        prev_pending = db.drop_pending(conn, SOURCE, account)
        params = {
            "digital-account-identifier": account,
            "channel-entry-point-identifier": "WEB",
            "requested-record-count": 32,
            "generic-enrichment-nudge-indicator": "true",
        }
        cursor = None
        for i in range(200):
            if cursor:
                params["scroll-key-page-offset-record-identifier"] = cursor
            res = await cdp.fetch_json(page, cdp.build_url(f"https://{HOST}{ENDPOINT_PATH}", params), headers)
            if res.get("status") != 200:
                raise RuntimeError(f"HTTP {res.get('status')}: {str(res.get('body'))[:200]}")
            body = res["body"]
            new_p = upd_p = 0
            for raw in (body.get("transactions") if isinstance(body, dict) else None) or []:
                tx = normalize(raw)
                if not tx:
                    continue
                state = db.upsert_tx(conn, SOURCE, account, tx, fetched_at)
                if state == "new" and tx["id"] not in prev_pending:
                    new_p += 1
                elif state == "updated":
                    upd_p += 1
            conn.commit()
            new_total += new_p
            updated_total += upd_p
            log(f"  {account[-4:]} page {i + 1}: {new_p} new, {upd_p} updated")
            cursor = cdp.find_key(body, CURSOR_KEYS)
            if not cursor or (not full and i > 0 and new_p == 0 and upd_p == 0):
                break
    return new_total, updated_total
