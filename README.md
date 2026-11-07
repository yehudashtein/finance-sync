# finance

All accounts -> one SQLite DB (`data/finance.db`) for analysis.

> **New machine / fresh clone?** See **[SETUP.md](SETUP.md)** — a clone needs the
> vendored forks rebuilt first: run `bash scripts/setup.sh`, then fill `.env`.
>
> **Docker** (headless Israeli sources, Chase excluded): see **[DOCKER.md](DOCKER.md)**.

| Source  | How                                   | Unattended? |
|---------|---------------------------------------|-------------|
| chase   | your logged-in debug Chrome (CDP)     | no - log in first |
| max     | israeli-bank-scrapers (headless)      | yes |
| onezero | israeli-bank-scrapers (API + token)   | yes, after one-time SMS setup |
| pepper  | @sergienko4 fork (vendor/pepper-fork), API-direct app protocol | yes, after one-time SMS setup |

## Setup
1. Fill `.env` (copy of `.env.example`; git-ignored, plain text).
2. One Zero, once, in your own terminal: `npm --prefix israel run onezero-setup`
3. Chase: `./start-chrome.ps1`, log in to chase.com in that window.

## Run
    .venv\Scripts\python sync_all.py            # everything
    .venv\Scripts\python sync_all.py max        # one source
    .venv\Scripts\python sync_all.py chase --full

`SHOW_BROWSER=1` in .env shows the Max browser for debugging.

## Data model
- `transactions`: amount signed (out = negative) in `currency`; `kind='card_payment'`
  marks bank-side card payoffs so spending isn't double counted. Use `v_spending`.
- `balances`: one row per account per sync day (history over time).
- `sync_runs`: log of every run and why a source was skipped.
- `raw`: original source JSON on every transaction.
