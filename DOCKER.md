# Docker — headless Israeli sources

Runs **Max, Isracard, One Zero, Pepper** (and analysis) in a container. **Chase is
not supported in Docker** — it needs a real, interactively-logged-in Chrome on the
host; keep running Chase natively (`sync_all.py chase`).

The image rebuilds the vendored forks for Linux, installs deps, and bakes in the
Camoufox browser, so it is self-contained (~1.5 GB).

## Build

```bash
docker compose build      # or: docker build -t finance-sync .
```

## One-time setups (interactive, need the SMS code)

```bash
docker compose run --rm setup onezero-setup
docker compose run --rm setup pepper-setup
```

These write the tokens / device credentials back into your host `.env` (it's
mounted). Do them once; afterwards sync is unattended.

## Sync

```bash
docker compose run --rm sync                 # max isracard onezero pepper
docker compose run --rm sync max pepper      # selected
```

Reads `.env`, writes `data/finance.db` and `logs/finance.log` on the host (mounted).

## Scheduling (server / NAS)

Add a cron entry on the host:

```
0 6 * * *  cd /path/to/finance && docker compose run --rm sync >> logs/cron.log 2>&1
```

## Networking caveat (One Zero / Pepper)

One Zero and Pepper only accept connections from Israel. The compose file uses
`network_mode: host` so the container follows the host's routing (e.g. a Tailscale
exit node in Israel).

- **Linux host:** host networking works; if the host routes IL traffic through
  Tailscale, the container does too.
- **Docker Desktop (Windows/Mac):** host networking is limited (Docker runs in a
  VM), so the container may not inherit the host's Tailscale exit node. If One Zero
  or Pepper return a Cloudflare 403, either run those two natively, or run
  Tailscale inside the container, or deploy the image on a Linux host in/routed to
  Israel.

## Camoufox cache

The browser is downloaded at build time into the image. If you see it
re-downloading on every run, mount its cache directory as a volume (check the run
log for the path it uses, typically under `/root/.cache`) and add it to both
services in `docker-compose.yml`.

## What's excluded from the image

`.dockerignore` keeps the build clean: `.env`, `data/`, `logs/`, the prebuilt
`israel/node_modules` and `israel/vendor` (rebuilt inside), `.git`, `CARDS.md`.
