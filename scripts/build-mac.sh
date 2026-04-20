#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS (Apple Silicon / Intel) 빌드 스크립트
#
# 실행 위치: macOS Terminal. 빌드 컨텍스트는 레포 루트.
# 결과 이미지: ttc-allinone:mac-<tag>
# arm64 가 기본이며, --amd64 플래그 로 Rosetta 경유 amd64 빌드 가능.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:mac-${TAG}}"
PLATFORM="linux/arm64"

for arg in "$@"; do
    case "$arg" in
        --amd64) PLATFORM="linux/amd64"; shift ;;
    esac
done

cd "$REPO_ROOT"

echo "[build-mac] image: $IMAGE"
echo "[build-mac] platform: $PLATFORM"
echo "[build-mac] context: $REPO_ROOT"

docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f Dockerfile.allinone \
    -t "$IMAGE" \
    --load \
    "$@" \
    .

echo "[build-mac] 빌드 완료: $IMAGE"
echo "[build-mac] 기동: bash scripts/run-mac.sh"
