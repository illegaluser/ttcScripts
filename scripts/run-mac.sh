#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS 기동 스크립트
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
exec docker compose -f docker-compose.allinone.mac.yaml "$@" up -d
