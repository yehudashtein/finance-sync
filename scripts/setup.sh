#!/usr/bin/env bash
# Restore a fresh clone to a runnable state: rebuild the two vendored Israeli
# scraper forks, install deps, create the Python venv and .env.
# Idempotent — safe to re-run. Run from anywhere: `bash scripts/setup.sh`.
# Windows: run in Git Bash. Needs: git, node >= 18, python 3.12.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ONEZERO_REPO="https://github.com/RebliNk17/israeli-bank-scrapers.git"
ONEZERO_REF="5af6496"
PEPPER_REPO="https://github.com/danielbenzvi/israeli-bank-scrapers.git"
PEPPER_REF="086b7c15"

build_fork() {  # repo ref dest [patch]
  local repo="$1" ref="$2" dest="$3" patch="${4:-}"
  if [ -f "$dest/lib/index.mjs" ]; then echo "✓ $dest already built"; return; fi
  echo "→ building $dest"
  rm -rf "$dest"; git clone --quiet "$repo" "$dest"
  ( cd "$dest"
    git checkout --quiet "$ref"
    [ -n "$patch" ] && git apply "$patch"
    npm ci --ignore-scripts --no-audit --no-fund
    npx tsup )
}

echo "== 1/4 One Zero fork"
build_fork "$ONEZERO_REPO" "$ONEZERO_REF" "israel/vendor/onezero-pr"
echo "== 2/4 Pepper fork (+ transactions fix & device re-auth patch)"
build_fork "$PEPPER_REPO" "$PEPPER_REF" "israel/vendor/pepper-fork" "$ROOT/patches/pepper-fork.patch"

echo "== 3/4 israel deps + better-sqlite3 (N-API, Camoufox needs it)"
( cd israel && npm install --no-audit --no-fund && npm rebuild better-sqlite3 )

echo "== 4/4 Python venv + deps"
python -m venv .venv 2>/dev/null || python3 -m venv .venv
PIP=".venv/Scripts/pip.exe"; [ -f "$PIP" ] || PIP=".venv/bin/pip"
"$PIP" install -q --upgrade pip
"$PIP" install -q -r requirements.txt

[ -f .env ] || { cp .env.example .env; echo "→ created .env from template"; }

cat <<'DONE'

Setup complete. Next:
  1. Fill credentials in .env
  2. One-time logins (see SETUP.md):
       npm --prefix israel run onezero-setup
       npm --prefix israel run pepper-setup
       ./start-chrome.ps1   &&   .venv/Scripts/python sync_all.py chase --show
  3. Run:  .venv/Scripts/python sync_all.py
DONE
