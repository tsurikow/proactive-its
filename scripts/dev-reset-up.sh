#!/usr/bin/env bash
set -euo pipefail

# Keep Qdrant vectors between restarts; Postgres is already ephemeral (tmpfs).
docker compose down --remove-orphans
docker compose up --build
