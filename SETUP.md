# Setup — new machine / fresh clone

A clone is **not** runnable out of the box: the two patched Israeli-scraper forks
are dependencies and are excluded from the repo, and credentials live in `.env`
(also excluded). This guide restores everything. Most of it is automated by
`scripts/setup.sh`; the rest is one-time interactive logins.

## What the project does

Unattended sync of five sources into one SQLite DB (`data/finance.db`):

| Source | Method | One-time setup |
|---|---|---|
| Chase (US) | your real Chrome via CDP | log in once in the debug Chrome (device gets remembered) |
| Max (IL) | Camoufox (headless) | username + password in `.env` |
| Isracard (IL) | Camoufox (headless) | id + 6 digits + password in `.env` |
| One Zero (IL) | API (mTLS + Android TLS) | SMS once → long-term token |
| Pepper (IL) | API (Camoufox TLS) | SMS once → persisted device keypair (then password-only) |

## Prerequisites

- **Git** and **Node.js ≥ 18** (only to bootstrap; the project pins its own Node 22 locally).
- **Python 3.12**.
- **Google Chrome** (for Chase).
- **~2 GB free disk** (Camoufox browser is downloaded on first Israeli-source run).
- Windows: use **Git Bash** to run `scripts/setup.sh`. The repo was built on Windows 11.
- Optional: **Tailscale** if One Zero / Pepper must reach Israel from abroad.

## 1. Automated setup

```bash
git clone https://github.com/yehudashtein/finance-sync.git
cd finance-sync
bash scripts/setup.sh
```

`scripts/setup.sh` does all of this (idempotent — safe to re-run):

1. Recreates `israel/vendor/onezero-pr` — clones the One Zero fork (unmerged PR #1209,
   commit `5af6496`) and builds it with `tsup`.
2. Recreates `israel/vendor/pepper-fork` — clones the Pepper fork (base `086b7c15`),
   applies `patches/pepper-fork.patch` (the transactions fix **and** device re-auth),
   and builds it with `tsup`.
3. `npm install` in `israel/` (now the `file:vendor/...` deps resolve), then
   `npm rebuild better-sqlite3` for the local Node 22 (Camoufox needs it).
4. Creates the Python venv and installs `requirements.txt`.
5. Copies `.env.example` → `.env` if missing.

## 2. Fill credentials

Edit `.env` (copied from `.env.example`). Fill what you use:

- `CHASE_ACCOUNT_IDS`, `CHASE_USERNAME`, `CHASE_PASSWORD`
- `MAX_USERNAME`, `MAX_PASSWORD`
- `ISRACARD_ID`, `ISRACARD_CARD6`, `ISRACARD_PASSWORD`
- `ONEZERO_EMAIL`, `ONEZERO_PASSWORD`, `ONEZERO_PHONE`
- `PEPPER_PHONE`, `PEPPER_PASSWORD`

Leave the auto-filled ones blank (`ONEZERO_OTP_TOKEN`, `PEPPER_DEVICE_ID/EC/RSA`,
`PEPPER_OTP_TOKEN`) — the setup commands below write them.

## 3. One-time interactive setups

Run each once; after this they sync unattended.

```bash
# One Zero — sends an SMS, asks for the code, saves a long-term token
npm --prefix israel run onezero-setup

# Pepper — sends an SMS, asks for the code, saves device keypair + id
#          (afterwards it re-auths with password only, no SMS)
npm --prefix israel run pepper-setup

# Chase — opens a Chrome you log in to; the device is then remembered
./start-chrome.ps1        # (PowerShell) log in to chase.com in that window, keep it open
.venv/Scripts/python sync_all.py chase --show   # first run only, completes device check
```

## 4. Run

```bash
.venv/Scripts/python sync_all.py            # all sources
.venv/Scripts/python sync_all.py max pepper # selected
.venv/Scripts/python sync_all.py chase --full
```

Output: `data/finance.db`. Logs: `logs/finance.log`.

## How the forks are vendored (manual reference)

If you prefer to do step 1 by hand:

**One Zero** (`israel/vendor/onezero-pr`):
```bash
git clone https://github.com/RebliNk17/israeli-bank-scrapers.git israel/vendor/onezero-pr
cd israel/vendor/onezero-pr && git checkout 5af6496
npm ci --ignore-scripts && npx tsup
```

**Pepper** (`israel/vendor/pepper-fork`):
```bash
git clone https://github.com/danielbenzvi/israeli-bank-scrapers.git israel/vendor/pepper-fork
cd israel/vendor/pepper-fork && git checkout 086b7c15
git apply ../../../patches/pepper-fork.patch
npm ci --ignore-scripts && npx tsup
```

Both are wired as `file:` deps in `israel/package.json` (`isb-onezero`, `isb-pepper`).
The Pepper changes are also upstream as PRs
[#1 (transactions)](https://github.com/danielbenzvi/israeli-bank-scrapers/pull/1) and
[#2 (device re-auth)](https://github.com/danielbenzvi/israeli-bank-scrapers/pull/2);
once merged, `patches/pepper-fork.patch` can be dropped.

## Troubleshooting

- **`npm install` fails on `file:vendor/...`** — run `scripts/setup.sh` first (the forks must exist).
- **`camoufox launch failed: better-sqlite3 binding missing`** — `npm --prefix israel rebuild better-sqlite3`.
- **Pepper/One Zero blocked (Cloudflare 403)** — connect through an Israeli network (Tailscale exit node).
- **Pepper asks for SMS again** — the device token expired/was revoked; re-run `pepper-setup` once.
- **Chase wants verification** — `sync_all.py chase --show` and complete it in the Chrome window.
