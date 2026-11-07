"""
Israeli sources via israeli-bank-scrapers (Node, in ./israel).

Each run re-fetches the last ISRAEL_LOOKBACK_DAYS and upserts. These
sources often lack a stable transaction id, so the id is a hash of the
transaction's content plus an occurrence counter for exact duplicates
(e.g. two identical coffees on the same day).
"""

import os
import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from core import db
from core.cdp import NeedsLogin
from core.log import LOG_FILE, get_logger

ISRAEL_DIR = Path(__file__).resolve().parent.parent / "israel"
# Node 22 binary. Override with ISRAEL_NODE (e.g. "node" in Docker where Node 22
# is the system runtime); default to the project-local Node 22 per platform.
_default_node = ISRAEL_DIR / "node_modules" / "node" / "bin" / ("node.exe" if os.name == "nt" else "node")
NODE = os.environ.get("ISRAEL_NODE") or str(_default_node)


# Network blips (e.g. Tailscale exit-node DNS flapping) are worth one retry.
TRANSIENT_RE = re.compile(r"ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNRESET|fetch failed|socket hang up")


def run_scraper(name: str, attempts: int = 2, log=print) -> dict:
    for attempt in range(1, attempts + 1):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"{name}.json"
            # Mirror the Node scraper's full output (incl. library debug) into the
            # shared log file so stalls/errors are inspectable after the fact.
            proc = subprocess.run(
                [str(NODE), "scrape.mjs", name, str(out)],
                cwd=ISRAEL_DIR, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600,
                env={**os.environ, "DEBUG": os.environ.get("DEBUG", "")},
            )
            stderr = (proc.stderr or "").strip()
            if stderr:
                with open(LOG_FILE, "a", encoding="utf-8") as fh:
                    for line in stderr.splitlines():
                        fh.write(f"    [node:{name}] {line}\n")
            tail = stderr.splitlines()[-1] if stderr else ""
            if proc.returncode == 0:
                return json.loads(out.read_text(encoding="utf-8"))
            if proc.returncode == 2:
                raise NeedsLogin(tail)
            if attempt < attempts and TRANSIENT_RE.search(stderr):
                log(f"network error ({tail[:80]}), retrying in 30s")
                time.sleep(30)
                continue
            raise RuntimeError(tail or f"scraper exited {proc.returncode}")


def tx_id(account: str, t: dict, seen: dict) -> str:
    inst = t.get("installments") or {}
    if t.get("identifier") not in (None, ""):
        # installment plans reuse one identifier for every monthly payment
        base = f"ident:{account}:{t['identifier']}:{inst.get('number')}:{t.get('processedDate')}"
    else:
        base = "|".join(str(x) for x in (
            account, t.get("date"), t.get("processedDate"), t.get("originalAmount"),
            t.get("originalCurrency"), t.get("chargedAmount"), t.get("description"),
            inst.get("number"), inst.get("total"),
        ))
    seen[base] = seen.get(base, 0) + 1
    return hashlib.sha1(f"{base}#{seen[base]}".encode("utf-8")).hexdigest()[:20]


def ingest(conn, payload: dict) -> tuple[int, int]:
    source = payload["source"]
    acct_type = payload["accountType"]
    fetched_at = db.now()
    new_total = updated_total = 0
    for a in payload["accounts"]:
        acct = a["accountNumber"]
        currency = a.get("currency") or "ILS"
        label = "card" if acct_type == "credit_card" else "account"
        db.upsert_account(conn, source, acct, name=f"{payload['institution']} {label} {acct[-4:]}",
                          type=acct_type, currency=currency, institution=payload["institution"])
        db.record_balance(conn, source, acct, a.get("balance"), currency)
        prev_pending = db.drop_pending(conn, source, acct)
        seen: dict = {}
        for t in a["txns"]:
            pending = 1 if t.get("status") == "pending" else 0
            inst = t.get("installments") or {}
            tx = {
                "id": tx_id(acct, t, seen),
                "date": t.get("date"),
                "post_date": t.get("processedDate"),
                "description": t.get("description"),
                "memo": t.get("memo") or None,
                "amount": t.get("chargedAmount"),
                "currency": t.get("chargedCurrency") or currency,
                "original_amount": t.get("originalAmount"),
                "original_currency": t.get("originalCurrency"),
                "category": t.get("category"),
                "pending": pending,
                "kind": db.classify_kind(acct_type, t.get("description")),
                "installment_no": inst.get("number"),
                "installment_total": inst.get("total"),
                "raw": t,
            }
            state = db.upsert_tx(conn, source, acct, tx, fetched_at)
            if state == "new" and tx["id"] not in prev_pending:
                new_total += 1
            elif state == "updated":
                updated_total += 1
        conn.commit()
    return new_total, updated_total


def sync(conn, name: str, log=print) -> tuple[int, int]:
    payload = run_scraper(name, log=log)
    for a in payload["accounts"]:
        log(f"  {a['accountNumber'][-4:]}: {len(a['txns'])} txns, balance {a.get('balance')}")
    return ingest(conn, payload)
