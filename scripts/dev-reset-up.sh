#!/usr/bin/env bash
set -euo pipefail

# Keep Qdrant vectors between restarts, but force a fresh Postgres volume.
docker compose down --remove-orphans
docker volume rm -f proactive_its_postgres_data >/dev/null 2>&1 || true
docker compose up --build
