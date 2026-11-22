#!/usr/bin/env bash
# Remote deploy script for OpenGym. Runs on the VPS, called by GitHub Actions.
set -euo pipefail

# Move to the project root (parent of scripts/), wherever the script is called from.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

compose() { docker compose -f docker-compose.vps.yml "$@"; }

# Runtime secrets live in .env on the VPS. Compose reads it automatically.
if [ ! -f .env ] || ! grep -q '^TUNNEL_TOKEN=' .env; then
  echo "ERROR: $(pwd)/.env must define TUNNEL_TOKEN on VPS!" >&2
  exit 1
fi

# Pull pre-built images. Services with a build section are skipped.
compose pull --ignore-buildable

# Build custom services, recreate changed containers, fail if one does not start.
compose up -d --build --remove-orphans --wait --wait-timeout 120

# Free disk space: remove dangling images.
docker image prune -f >/dev/null 2>&1 || true
echo "OpenGym deployed."
