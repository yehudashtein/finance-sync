"""
Helpers for scraping through your own logged-in Chrome (started by
start-chrome.ps1 with --remote-debugging-port=9222).

Pattern: let the site's own app fire a real API request, capture its
headers (CSRF/auth added by the site's interceptors), then replay them
with fetch() inside the page so cookies apply.
"""

import asyncio
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "chrome-debug-profile"  # shared with start-chrome.ps1, keeps "remember me"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CDP_URL = "http://localhost:9222"
FORBIDDEN_HEADERS = {
    "host", "content-length", "connection",
    ":authority", ":method", ":path", ":scheme", ":status",
}


class NeedsLogin(Exception):
    """The debug Chrome isn't running, or no logged-in tab for the site."""


def chrome_is_up(port: int = 9222) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


async def open_chrome(p, show: bool = False):
    """
    Connect to the debug Chrome, launching it if needed. Launched Chrome is a
    real (non-headless) Chrome placed off-screen: Chase rejects headless Chrome.
    Returns (browser, launched_by_us).
    """
    launched = None
    if not chrome_is_up():
        chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
        if not chrome:
            raise RuntimeError("Google Chrome not found")
        args = [chrome, "--remote-debugging-port=9222", f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run", "--no-default-browser-check", "--window-size=1280,900"]
        args.append("--window-position=100,100" if show else "--window-position=-32000,-32000")
        launched = subprocess.Popen(args + ["about:blank"])
        for _ in range(60):
            if chrome_is_up():
                break
            await asyncio.sleep(0.5)
    browser = await p.chromium.connect_over_cdp(CDP_URL)
    return browser, launched


async def first_page(browser):
    ctx = browser.contexts[0]
    return ctx.pages[0] if ctx.pages else await ctx.new_page()


async def close_chrome(browser, launched) -> None:
    """Close Chrome only if we started it; leave a manually opened one alone."""
    if launched is None:
        return
    # Ask Chrome to quit cleanly so it flushes cookies (incl. Chase's trusted-device
    # cookie) to the profile. browser.close() on a CDP connection only disconnects,
    # and a hard kill can drop recently set cookies.
    try:
        session = await browser.new_browser_cdp_session()
        await session.send("Browser.close")
    except Exception:
        pass
    try:
        await browser.close()
    except Exception:
        pass
    try:
        launched.wait(timeout=30)
    except subprocess.TimeoutExpired:
        launched.kill()


async def attach(p, host_fragment: str):
    """Return (browser, page) for the first tab whose URL contains host_fragment."""
    if not chrome_is_up():
        raise NeedsLogin("Debug Chrome isn't running. Run start-chrome.ps1 and log in.")
    browser = await p.chromium.connect_over_cdp(CDP_URL)
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if host_fragment in pg.url:
                return browser, pg
    raise NeedsLogin(f"No logged-in tab for {host_fragment} in the debug Chrome.")


async def capture_headers(page, url_fragment: str, timeout_ms: int = 30000) -> dict:
    """Reload the page and snapshot headers of the first GET matching url_fragment."""
    async with page.expect_request(
        lambda r: url_fragment in r.url and r.method == "GET", timeout=timeout_ms,
    ) as info:
        await page.reload()
    req = await info.value
    raw = await req.all_headers()
    return {k: v for k, v in raw.items() if k.lower() not in FORBIDDEN_HEADERS}


async def fetch_json(page, url: str, headers: dict) -> dict:
    js = """async ({url, headers}) => {
        const r = await fetch(url, { credentials: 'include', headers });
        const text = await r.text();
        let body; try { body = JSON.parse(text); } catch { body = text; }
        return { status: r.status, body };
    }"""
    return await page.evaluate(js, {"url": url, "headers": headers})


def build_url(base: str, params: dict) -> str:
    return base + "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())


def find_key(body, keys) -> str | None:
    """Depth-first search for the first truthy value under any of `keys`."""
    if not isinstance(body, dict):
        return None
    for k in keys:
        if body.get(k):
            return body[k]
    for v in body.values():
        if isinstance(v, dict):
            hit = find_key(v, keys)
            if hit:
                return hit
    return None
