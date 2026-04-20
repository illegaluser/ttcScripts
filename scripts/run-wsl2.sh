#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 기동 스크립트
# docker compose 래퍼. 데이터 볼륨은 WSL2 HOME 하위에 둔다.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
exec docker compose -f docker-compose.allinone.wsl2.yaml "$@" up -d
