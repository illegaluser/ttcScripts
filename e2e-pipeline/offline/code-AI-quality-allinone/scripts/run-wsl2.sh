#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 기동 스크립트
# docker compose 래퍼. 데이터 볼륨은 WSL2 HOME 하위에 둔다.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ 의 상위가 code-AI-quality-allinone/ 이고 거기에 compose 파일이 있다.
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ALLINONE_DIR"
exec docker compose -f docker-compose.wsl2.yaml "$@" up -d
