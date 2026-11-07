"""
Shared SQLite store for every financial source.

Conventions
- amount: signed in the account's own currency. Money out is negative.
- date / post_date: ISO 'YYYY-MM-DD'.
- kind: 'normal', or 'card_payment' for a bank-side lump charge that pays
  a credit card whose individual purchases are already imported. Analysis
  should exclude card_payment rows from spending to avoid double counting.
- raw: the source's original JSON, so fields can be re-derived later.
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "finance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    source      TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    name        TEXT,
    type        TEXT,            -- checking / credit_card / ...
    currency    TEXT,
    institution TEXT,
    updated_at  TEXT,
    PRIMARY KEY (source, account_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    source            TEXT NOT NULL,
    account_id        TEXT NOT NULL,
    id                TEXT NOT NULL,
    date              TEXT,
    post_date         TEXT,
    description       TEXT,
    memo              TEXT,
    amount            REAL,
    currency          TEXT,
    original_amount   REAL,
    original_currency TEXT,
    category          TEXT,
    pending           INTEGER NOT NULL DEFAULT 0,
    kind              TEXT NOT NULL DEFAULT 'normal',
    installment_no    INTEGER,
    installment_total INTEGER,
    raw               TEXT,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (source, account_id, id)
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date DESC);
CREATE INDEX IF NOT EXISTS idx_tx_acct ON transactions(source, account_id, date DESC);

CREATE TABLE IF NOT EXISTS balances (
    source      TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    balance     REAL,
    currency    TEXT,
    PRIMARY KEY (source, account_id, as_of)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,            -- ok / error / skipped
    new_count   INTEGER,
    updated_count INTEGER,
    message     TEXT
);

-- Spending view: real transactions only, card payoffs excluded.
CREATE VIEW IF NOT EXISTS v_spending AS
SELECT * FROM transactions WHERE kind = 'normal' AND amount < 0;
"""

# Bank-side descriptions that are a lump payment to an Israeli card issuer
# or a US card. Matched only on bank (non-card) accounts.
CARD_PAYMENT_RE = re.compile(
    # Israeli issuers (Hebrew bank descriptions). No bare "cal": it matches "LOCAL".
    r"מקס|max it|לאומי קארד|ישראכרט|isracard|כאל|visa cal|cal-online|ויזה|אמריקן|"
    # US card payoffs from checking
    r"\bamex epayment|american express|payment to chase card|chase credit crd|firstcard|"
    r"capital one.*pmt|discover.*e-payment|citi.*autopay|applecard|synchrony",
    re.IGNORECASE,
)


def reclassify(conn) -> int:
    """Re-apply classify_kind to every stored row (after changing the rules)."""
    types = {(s, a): t for s, a, t in conn.execute("SELECT source, account_id, type FROM accounts")}
    changed = 0
    for src, acct, tid, desc, kind in conn.execute(
            "SELECT source, account_id, id, description, kind FROM transactions").fetchall():
        k = classify_kind(types.get((src, acct)), desc)
        if k != kind:
            conn.execute("UPDATE transactions SET kind=? WHERE source=? AND account_id=? AND id=?",
                         (k, src, acct, tid))
            changed += 1
    conn.commit()
    return changed


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def iso_date(d) -> str | None:
    """Normalize 'YYYYMMDD' or ISO strings to 'YYYY-MM-DD'."""
    if not d:
        return None
    s = str(d)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def classify_kind(account_type: str | None, description: str | None) -> str:
    if account_type != "credit_card" and description and CARD_PAYMENT_RE.search(description):
        return "card_payment"
    return "normal"


def upsert_account(conn, source, account_id, *, name=None, type=None,
                   currency=None, institution=None) -> None:
    conn.execute(
        """INSERT INTO accounts(source, account_id, name, type, currency, institution, updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(source, account_id) DO UPDATE SET
             name=COALESCE(excluded.name, name),
             type=COALESCE(excluded.type, type),
             currency=COALESCE(excluded.currency, currency),
             institution=COALESCE(excluded.institution, institution),
             updated_at=excluded.updated_at""",
        (source, account_id, name, type, currency, institution, now()),
    )


def record_balance(conn, source, account_id, balance, currency, as_of=None) -> None:
    if balance is None:
        return
    conn.execute(
        """INSERT OR REPLACE INTO balances(source, account_id, as_of, balance, currency)
           VALUES (?,?,?,?,?)""",
        (source, account_id, as_of or datetime.now().date().isoformat(), balance, currency),
    )


TX_FIELDS = (
    "date", "post_date", "description", "memo", "amount", "currency",
    "original_amount", "original_currency", "category", "pending", "kind",
    "installment_no", "installment_total", "raw",
)
COMPARE_FIELDS = ("date", "description", "amount", "pending")


def upsert_tx(conn, source: str, account_id: str, tx: dict, fetched_at: str) -> str:
    """Insert or update one normalized transaction. Returns new/updated/unchanged."""
    row = conn.execute(
        f"SELECT {', '.join(COMPARE_FIELDS)} FROM transactions WHERE source=? AND account_id=? AND id=?",
        (source, account_id, tx["id"]),
    ).fetchone()
    if row is None:
        state = "new"
    elif tuple(row) != tuple(tx.get(f) for f in COMPARE_FIELDS):
        state = "updated"
    else:
        state = "unchanged"

    values = [tx.get(f) for f in TX_FIELDS]
    raw_idx = TX_FIELDS.index("raw")
    if not isinstance(values[raw_idx], (str, type(None))):
        values[raw_idx] = json.dumps(values[raw_idx], default=str, ensure_ascii=False)

    cols = ", ".join(("source", "account_id", "id") + TX_FIELDS + ("fetched_at",))
    marks = ", ".join("?" * (len(TX_FIELDS) + 4))
    updates = ", ".join(f"{f}=excluded.{f}" for f in TX_FIELDS + ("fetched_at",))
    conn.execute(
        f"""INSERT INTO transactions({cols}) VALUES ({marks})
            ON CONFLICT(source, account_id, id) DO UPDATE SET {updates}""",
        [source, account_id, tx["id"], *values, fetched_at],
    )
    return state


def drop_pending(conn, source: str, account_id: str) -> set[str]:
    """Remove pending rows before a sync so pending->posted resolves cleanly."""
    ids = {r[0] for r in conn.execute(
        "SELECT id FROM transactions WHERE source=? AND account_id=? AND pending=1",
        (source, account_id))}
    conn.execute("DELETE FROM transactions WHERE source=? AND account_id=? AND pending=1",
                 (source, account_id))
    return ids


def start_run(conn, source: str) -> int:
    cur = conn.execute("INSERT INTO sync_runs(source, started_at) VALUES (?,?)", (source, now()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, new=0, updated=0, message=None) -> None:
    conn.execute(
        """UPDATE sync_runs SET finished_at=?, status=?, new_count=?, updated_count=?, message=?
           WHERE id=?""",
        (now(), status, new, updated, message, run_id),
    )
    conn.commit()
