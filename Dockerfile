# Docker image for the HEADLESS Israeli sources (Max, Isracard, One Zero, Pepper)
# and analysis. Chase is intentionally NOT supported here — it needs a real,
# interactively-logged-in Chrome on the host (run Chase from the host instead).
#
# Build:  docker build -t finance-sync .
# Run:    docker run --rm \
#           -v "$PWD/.env:/app/.env" -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
#           finance-sync                       # default: max isracard onezero pepper
#         docker run --rm -v ... finance-sync max pepper
# See DOCKER.md for the one-time SMS setups and the networking caveat.
FROM node:22-bookworm-slim

# Build-time tooling only (kept early so later layers aren't busted by it).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only the inputs scripts/setup.sh needs, copied first so editing app source
# later does NOT re-run the heavy fork build + 1.3 GB Camoufox download.
COPY israel/package.json israel/package-lock.json ./israel/
COPY patches/ ./patches/
COPY scripts/ ./scripts/
COPY requirements.txt .env.example ./

# Rebuild the vendored forks + deps + venv for Linux (also downloads Puppeteer's
# Chrome for Max/Isracard). Idempotent.
RUN bash scripts/setup.sh

# Pre-download Camoufox (One Zero / Pepper) so the image is self-contained.
RUN cd israel && (npx --yes camoufox-js fetch || node -e "import('camoufox-js').then(m=>m.fetchCamoufox?.()).catch(()=>process.exit(0))" || true)

# The rest of the source (node_modules / vendor are .dockerignore'd, so the
# freshly built ones are preserved).
COPY . .

# Browser runtime libraries (Chrome for Max/Isracard + Firefox for Camoufox).
# Last so re-tuning this list rebuilds only this small layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libcups2 \
      libdbus-1-3 libdrm2 libexpat1 libfontconfig1 libgbm1 libglib2.0-0 \
      libgtk-3-0 libdbus-glib-1-2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
      libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
      libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxt6 libxtst6 \
      libxkbcommon0 libgdk-pixbuf-2.0-0 libasound2 libegl1 libgl1 \
      libxshmfence1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV ISRAEL_NODE=node
ENV BROWSER_NO_SANDBOX=1
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/.venv/bin/python", "sync_all.py"]
CMD ["max", "isracard", "onezero", "pepper"]
