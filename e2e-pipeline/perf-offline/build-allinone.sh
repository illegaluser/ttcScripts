#!/usr/bin/env bash
# ============================================================================
# JMeter Performance All-in-One — 번들 빌드 스크립트 (온라인 머신 전용)
#
# 동작:
#   1) 사전 검증 (docker / buildx / 디스크)
#   2) docker buildx build 로 단일 이미지 제작 (1개 또는 2개 아키텍처)
#   3) docker save | gzip 으로 배포용 tar.gz 산출 (아키텍처별 1개 파일)
#
# 산출:
#   dscore-qa-perf-allinone-<arch>-<timestamp>.tar.gz   (예: -amd64-, -arm64-)
#   같은 디렉토리에 .sha256 파일도 함께 떨어짐.
#
# Ollama / LLM 모델은 이 이미지에 들어 있지 않다.
#   → 호스트(Mac/Windows)에 별도 설치한 Ollama 를 사용 (호스트 GPU 가속 활용).
#   → 설치 절차는 README-mac.md / README-windows.md 참조.
#
# 환경변수 (모두 선택):
#   IMAGE_TAG          기본 dscore-qa-perf:allinone (실제 태그는 -<arch> 자동 부착)
#   TARGET_PLATFORMS   기본 'linux/amd64'  →  콤마 구분으로 다중 지정:
#                                              'linux/amd64,linux/arm64'
#   JMETER_VERSION     기본 5.6.3
#   JMETER_PM_VERSION  기본 1.12 (jmeter-plugins-manager)
#   NOVNC_VERSION      기본 1.6.0
#   OUTPUT_DIR         기본 ../  (e2e-pipeline/ 루트)
#
# 요구:
#   - Docker 26+ (buildx 활성), 디스크 15GB+ 여유 (다중 아키 시 30GB+)
#   - 온라인 (Docker Hub + apt + repo1.maven.org + jmeter-plugins.org + github.com)
#   - 빌드 소요: 5-15분/아키텍처 (네트워크 속도 의존)
#
# 빌드 예시:
#   # AMD64 단독 (Intel/AMD Linux/Windows 호스트용)
#   ./build-allinone.sh
#
#   # ARM64 단독 (Apple Silicon Mac 호스트용)
#   TARGET_PLATFORMS=linux/arm64 ./build-allinone.sh
#
#   # 두 아키텍처 모두 (Mac+Windows 양쪽 배포)
#   TARGET_PLATFORMS=linux/amd64,linux/arm64 ./build-allinone.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 설정값 ────────────────────────────────────────────────────────────────
IMAGE_TAG_BASE="${IMAGE_TAG:-dscore-qa-perf:allinone}"
TARGET_PLATFORMS="${TARGET_PLATFORMS:-${TARGET_PLATFORM:-linux/amd64}}"
JMETER_VERSION="${JMETER_VERSION:-5.6.3}"
JMETER_PM_VERSION="${JMETER_PM_VERSION:-1.12}"
NOVNC_VERSION="${NOVNC_VERSION:-1.6.0}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR}"
TS="$(date +%Y%m%d-%H%M%S)"

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
log()  { printf '[build-perf-allinone] %s\n' "$*"; }
err()  { printf '[build-perf-allinone] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
docker buildx version >/dev/null 2>&1 || err "docker buildx 가 필요합니다 (Docker 26+)."

[ -f "$SCRIPT_DIR/Dockerfile.allinone" ]    || err "Dockerfile.allinone 누락"
[ -f "$SCRIPT_DIR/jmeter-plugin-list.txt" ] || err "jmeter-plugin-list.txt 누락"
[ -f "$SCRIPT_DIR/supervisord.conf" ]       || err "supervisord.conf 누락"
[ -f "$SCRIPT_DIR/nginx-perf.conf" ]        || err "nginx-perf.conf 누락"
[ -f "$SCRIPT_DIR/entrypoint-allinone.sh" ] || err "entrypoint-allinone.sh 누락"
[ -d "$SCRIPT_DIR/scenarios-seed" ]         || err "scenarios-seed/ 디렉토리 누락"

mkdir -p "$OUTPUT_DIR"

log "================================================================"
log "  JMeter Performance All-in-One 빌드"
log "  플랫폼: $TARGET_PLATFORMS"
log "  JMeter $JMETER_VERSION  /  Plugins Manager $JMETER_PM_VERSION"
log "  noVNC $NOVNC_VERSION"
log "  Ollama 는 호스트 설치 사용 (이미지에 미포함)"
log "  산출 디렉토리: $OUTPUT_DIR"
log "================================================================"

# ── 빌드 함수 (아키텍처 1개) ─────────────────────────────────────────────
build_one() {
  local platform="$1"
  local arch_suffix
  case "$platform" in
    linux/amd64) arch_suffix="amd64" ;;
    linux/arm64) arch_suffix="arm64" ;;
    *) arch_suffix="$(echo "$platform" | tr '/' '-')" ;;
  esac

  local image_tag="${IMAGE_TAG_BASE}-${arch_suffix}"
  local out_tar="dscore-qa-perf-allinone-${arch_suffix}-${TS}.tar.gz"
  local out_path="${OUTPUT_DIR}/${out_tar}"

  log ""
  log "▶ [$platform] 빌드 시작 — tag: $image_tag"
  log "  (이 단계는 5-15분 소요될 수 있습니다)"

  docker buildx build \
    --platform "$platform" \
    --file "$SCRIPT_DIR/Dockerfile.allinone" \
    --tag "$image_tag" \
    --build-arg "JMETER_VERSION=$JMETER_VERSION" \
    --build-arg "JMETER_PM_VERSION=$JMETER_PM_VERSION" \
    --build-arg "NOVNC_VERSION=$NOVNC_VERSION" \
    --load \
    "$ROOT_DIR" \
    || err "[$platform] docker buildx build 실패"

  log "  이미지 크기:"
  docker images "$image_tag" --format '    {{.Repository}}:{{.Tag}}  {{.Size}}'

  log "  save + gzip → $out_path"
  docker save "$image_tag" | gzip -1 > "$out_path"

  local sz; sz="$(du -h "$out_path" | cut -f1)"
  local sum; sum="$(sha256sum "$out_path" | cut -d' ' -f1)"
  echo "$sum  $out_tar" > "${out_path}.sha256"

  log "  완료: $out_tar ($sz)"
  log "  sha256: $sum"
}

# ── 다중 아키텍처 빌드 ────────────────────────────────────────────────────
IFS=',' read -ra PLATFORM_ARR <<< "$TARGET_PLATFORMS"
for p in "${PLATFORM_ARR[@]}"; do
  p="$(echo "$p" | xargs)"   # trim
  [ -z "$p" ] && continue
  build_one "$p"
done

log ""
log "=========================================================================="
log "전체 빌드 완료"
log "=========================================================================="
log ""
log "산출 파일:"
ls -lh "$OUTPUT_DIR"/dscore-qa-perf-allinone-*-${TS}.tar.gz 2>/dev/null | awk '{print "  " $0}'
log ""
log "배포 방법:"
log "  - Mac (Apple Silicon)         → arm64 tar.gz  + README-mac.md"
log "  - Windows / Intel Mac / Linux → amd64 tar.gz  + README-windows.md"
log ""
log "주의: 호스트(Mac/Windows)에 Ollama 사전 설치 + 모델(gemma4:e2b 등) 반입 필요."
log "      자세한 절차는 README-mac.md / README-windows.md 의 1절 참조."
log "=========================================================================="
