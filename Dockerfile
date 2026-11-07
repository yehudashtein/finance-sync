# Docker image for the HEADLESS Israeli sources (Max, Isracard, One Zero, Pepper)
# and analysis. Chase is intentionally NOT supported here — it needs a real,
# interactively-logged-in Chrome on the host (run Chase from the host instead).
#
# Build:  docker build -t finance-sync .
# Run:    docker run --rm --network host \
#           -v "$PWD/.env:/app/.env" -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
#           finance-sync                       # default: max isracard onezero pepper
#         docker run --rm --network host -v ... finance-sync max pepper
# Interactive one-time SMS setups:
#         docker run --rm -it --network host -v "$PWD/.env:/app/.env" \
#           finance-sync-setup onezero-setup   # (see docker-compose / DOCKER.md)
FROM node:22-bookworm-slim

# Python + git + Camoufox(Firefox) runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip git ca-certificates \
      libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxt6 libxtst6 libxcomposite1 \
      libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 \
      libpango-1.0-0 libcairo2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libgdk-pixbuf-2.0-0 libegl1 libgl1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Rebuild the vendored forks + deps + venv for Linux (scripts/setup.sh is idempotent).
RUN bash scripts/setup.sh

# Pre-download the Camoufox browser so the image is self-contained (~1.3 GB).
# Falls back to a first-run download (into /root/.cache) if the CLI shape differs.
RUN cd israel && (npx --yes camoufox-js fetch || node -e "import('camoufox-js').then(m=>m.fetchCamoufox?.()).catch(()=>process.exit(0))" || true)

# israel.py picks its Node binary from ISRAEL_NODE (the system Node 22 here).
ENV ISRAEL_NODE=node
ENV PYTHONUNBUFFERED=1

# Default: sync the four headless sources (never 'chase' in a container).
ENTRYPOINT ["/app/.venv/bin/python", "sync_all.py"]
CMD ["max", "isracard", "onezero", "pepper"]
