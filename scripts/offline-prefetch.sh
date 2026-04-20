#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 오프라인 이미지 export 헬퍼
#
# 폐쇄망 빌드 전략: 온라인 머신에서 이미지를 빌드한 뒤 docker save 로 tarball
# 을 만들고, 오프라인 머신에서 docker load 로 복원한다. 이 스크립트는 그 과정을
# 자동화한다 (개별 pip wheel / npm tarball 캐싱 불필요).
#
# Usage:
#   bash scripts/offline-prefetch.sh --arch amd64  (WSL2/Linux)
#   bash scripts/offline-prefetch.sh --arch arm64  (macOS Apple Silicon)
#
# 산출물: offline-assets/<arch>/ttc-allinone-<arch>-<tag>.tar.gz
#
# 오프라인 머신 복원:
#   docker load -i offline-assets/<arch>/ttc-allinone-*.tar.gz
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

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
OUT_DIR="offline-assets/${ARCH}"
OUT_FILE="${OUT_DIR}/ttc-allinone-${ARCH}-${TAG}.tar.gz"

mkdir -p "$OUT_DIR"

echo "[prefetch] arch=$ARCH tag=$TAG platform=$PLATFORM image=$IMAGE"

# 1. buildx 빌드 (멀티스테이지라 의존 이미지도 자동 pull)
docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f Dockerfile.allinone \
    -t "$IMAGE" \
    --load \
    .

# 2. docker save → gzip
echo "[prefetch] saving to $OUT_FILE"
docker save "$IMAGE" | gzip > "$OUT_FILE"

# 3. 메타데이터 작성
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
