"""
Sync every configured source into data/finance.db.

    python sync_all.py                 # all sources
    python sync_all.py max onezero     # just these
    python sync_all.py chase --full    # full Chase history rescan
    python sync_all.py chase --show    # visible browser, for Chase's one-time device check

Sources that need you (Chase: debug Chrome logged in; One Zero: one-time
SMS setup) are reported as NEEDS LOGIN rather than failing the run.
"""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from core import db
from core.cdp import NeedsLogin
from core.log import get_logger
from sources import chase, israel

load_dotenv(db.ROOT / ".env")
log = get_logger()

SOURCES = ["chase", "max", "onezero", "pepper", "isracard"]


def run_source(conn, name: str, full: bool, show: bool) -> tuple[int, int]:
    slog = get_logger(name).info
    if name == "chase":
        return asyncio.run(chase.sync(conn, full=full, show=show, log=slog))
    return israel.sync(conn, name, log=slog)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*", help=f"any of: {', '.join(SOURCES)}")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--show", action="store_true", help="show the Chase browser (for one-time verification)")
    args = ap.parse_args()
    names = args.sources or SOURCES
    bad = [n for n in names if n not in SOURCES]
    if bad:
        ap.error(f"unknown source(s): {bad}. Choose from {SOURCES}")

    conn = db.connect()
    summary = []
    for name in names:
        log.info("== %s", name)
        run_id = db.start_run(conn, name)
        try:
            new, upd = run_source(conn, name, args.full, args.show)
            db.finish_run(conn, run_id, "ok", new, upd)
            summary.append((name, f"ok: {new} new, {upd} updated"))
        except NeedsLogin as e:
            db.finish_run(conn, run_id, "skipped", message=str(e))
            summary.append((name, f"NEEDS LOGIN: {e}"))
        except Exception as e:  # keep going with the other sources
            db.finish_run(conn, run_id, "error", message=str(e))
            summary.append((name, f"ERROR: {e}"))

    log.info("Summary:")
    for name, status in summary:
        log.info("  %-8s %s", name, status)
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    log.info("  %d transactions in %s", total, db.DB_PATH)
    return 0 if all(s.startswith("ok") for _, s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
