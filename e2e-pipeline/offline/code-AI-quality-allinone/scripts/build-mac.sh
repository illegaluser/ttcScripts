#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — macOS (Apple Silicon / Intel) 빌드 스크립트
#
# 빌드 컨텍스트: 이 폴더 자체 (자체 완결).
# Dockerfile 위치: 이 폴더의 Dockerfile
# 결과 이미지: ttc-allinone:mac-<tag>
# arm64 가 기본이며, --amd64 플래그 로 Rosetta 경유 amd64 빌드 가능.
#
# 빌드 전 선행: bash scripts/download-plugins.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:mac-${TAG}}"
PLATFORM="linux/arm64"

for arg in "$@"; do
    case "$arg" in
        --amd64) PLATFORM="linux/amd64"; shift ;;
    esac
done

cd "$ALLINONE_DIR"

# 자체 완결 검증
[ -f "$ALLINONE_DIR/Dockerfile" ]       || { echo "Dockerfile 없음" >&2; exit 1; }
[ -f "$ALLINONE_DIR/requirements.txt" ] || { echo "requirements.txt 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/pipeline-scripts" ] || { echo "pipeline-scripts/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/eval_runner" ]      || { echo "eval_runner/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkinsfiles" ]     || { echo "jenkinsfiles/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkins-init" ]     || { echo "jenkins-init/ 없음" >&2; exit 1; }

if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[build-mac] jenkins-plugins/ 가 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi
if [ ! -d "$ALLINONE_DIR/dify-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/dify-plugins" 2>/dev/null)" ]; then
    echo "[build-mac] dify-plugins/ 가 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi

echo "[build-mac] image:      $IMAGE"
echo "[build-mac] platform:   $PLATFORM"
echo "[build-mac] context:    $ALLINONE_DIR"
echo "[build-mac] Dockerfile: $ALLINONE_DIR/Dockerfile"

docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    --load \
    "$@" \
    "$ALLINONE_DIR"

echo "[build-mac] 빌드 완료: $IMAGE"
echo "[build-mac] 기동: bash scripts/run-mac.sh"
