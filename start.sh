#!/usr/bin/env bash
# One-command local startup: full stack via Docker.
set -euo pipefail
cd "$(dirname "$0")"
docker compose up --build
