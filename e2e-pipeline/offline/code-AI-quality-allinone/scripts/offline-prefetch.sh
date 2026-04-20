#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 이미지 export 헬퍼
#
# 폐쇄망 빌드 전략: 온라인 머신에서 이미지를 빌드한 뒤 docker save 로 tarball
# 을 만들고, 오프라인 머신에서 docker load 로 복원한다.
#
# 빌드 컨텍스트: 이 폴더 자체 (자체 완결).
#
# Usage:
#   bash scripts/offline-prefetch.sh --arch amd64  (WSL2/Linux)
#   bash scripts/offline-prefetch.sh --arch arm64  (macOS Apple Silicon)
#
# 선행:
#   bash scripts/download-plugins.sh   # 플러그인 바이너리 준비 (온라인)
#
# 산출물: offline-assets/<arch>/ttc-allinone-<arch>-<tag>.tar.gz
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ALLINONE_DIR"

ARCH="amd64"
TAG="${TAG:-dev}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch) ARCH="$2"; shift 2 ;;
        --tag)  TAG="$2";  shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$ARCH" in
    amd64) PLATFORM="linux/amd64" ;;
    arm64) PLATFORM="linux/arm64" ;;
    *) echo "unsupported arch: $ARCH (amd64|arm64)" >&2; exit 2 ;;
esac

IMAGE="ttc-allinone:${ARCH}-${TAG}"
OUT_DIR="$ALLINONE_DIR/offline-assets/${ARCH}"
OUT_FILE="${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.tar.gz"

mkdir -p "$OUT_DIR"

echo "[prefetch] arch=$ARCH tag=$TAG platform=$PLATFORM image=$IMAGE"
echo "[prefetch] context=$ALLINONE_DIR"

if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[prefetch] 플러그인이 비어 있습니다. 먼저 bash scripts/download-plugins.sh 실행" >&2
    exit 1
fi

docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    --load \
    "$ALLINONE_DIR"

echo "[prefetch] saving to $OUT_FILE"
docker save "$IMAGE" | gzip > "$OUT_FILE"

SIZE=$(du -h "$OUT_FILE" | cut -f1)
SHA=$(sha256sum "$OUT_FILE" | cut -d' ' -f1)
cat > "${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.meta" <<META
image: $IMAGE
arch: $ARCH
platform: $PLATFORM
tag: $TAG
tarball: $(basename "$OUT_FILE")
size: $SIZE
sha256: $SHA
built_at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
META

echo "[prefetch] 완료: $OUT_FILE ($SIZE, sha256=$SHA)"
echo "[prefetch] 오프라인 머신 복원:"
echo "    docker load -i $OUT_FILE"
