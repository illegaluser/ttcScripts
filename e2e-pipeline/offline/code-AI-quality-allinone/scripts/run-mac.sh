#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS 기동 스크립트
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ALLINONE_DIR"
exec docker compose -f docker-compose.mac.yaml "$@" up -d
