#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 (Windows) 빌드 스크립트
#
# 실행 위치: WSL2 Ubuntu 셸. 빌드 컨텍스트는 레포 루트.
# 결과 이미지: ttc-allinone:wsl2-<tag>
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:wsl2-${TAG}}"
PLATFORM="linux/amd64"

cd "$REPO_ROOT"

# WSL2 빌드는 /mnt/c 가 아닌 WSL2 네이티브 FS 에서 수행해야 빠르다. 경고만 출력.
if [[ "$(pwd)" == /mnt/* ]]; then
    echo "[build-wsl2] WARN: WSL2 마운트 경로 ($(pwd)) 에서 빌드하면 I/O 가 느립니다." >&2
    echo "[build-wsl2]       ~/ttcScripts 등 WSL2 네이티브 경로로 clone 하는 것을 권장합니다." >&2
fi

echo "[build-wsl2] image: $IMAGE"
echo "[build-wsl2] platform: $PLATFORM"
echo "[build-wsl2] context: $REPO_ROOT"

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

echo "[build-wsl2] 빌드 완료: $IMAGE"
echo "[build-wsl2] 기동: bash scripts/run-wsl2.sh"
