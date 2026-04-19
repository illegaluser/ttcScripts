#!/usr/bin/env bash
# ============================================================================
# perf-allinone — 폐쇄망 배포 번들 제작 스크립트 (온라인 빌드 머신 전용)
#
# 동작:
#   1) JMeter 컨테이너 이미지 빌드 (build-allinone.sh 호출)
#   2) Ollama 인스톨러 다운로드 (Mac DMG, Windows standalone ZIP, NSSM)
#   3) Ollama 모델 archive 제작 (호스트에 잠시 ollama 설치 → pull → archive → 정리)
#   4) install-*.sh / *.ps1 / README*.md 와 함께 단일 번들 디렉토리/tar.gz 산출
#
# 산출:
#   perf-allinone-bundle-<TS>/                    ← USB로 폐쇄망 반입할 디렉토리
#   perf-allinone-bundle-<TS>.tar.gz              ← 동일 내용 압축 (선택)
#
# 요구:
#   - Docker 26+ (buildx 활성), 디스크 30GB+
#   - 온라인 (Docker Hub, ollama.com, github.com, jmeter-plugins.org 등)
#   - macOS 또는 Linux 빌드 호스트 권장 (Mac DMG 가져오려면 macOS 환경이 더 자연스러움)
#
# 환경변수 (모두 선택):
#   TARGET_PLATFORMS     기본 'linux/amd64,linux/arm64' (양쪽 모두)
#   OLLAMA_MODELS        기본 'gemma4:e2b'  (콤마 구분 다중 가능)
#   SKIP_IMAGE_BUILD     '1' 이면 컨테이너 이미지 빌드 건너뜀 (이미 만들어둔 tar.gz 재사용)
#   SKIP_OLLAMA_DMG      '1' 이면 Ollama Mac DMG 다운로드 건너뜀 (Windows 전용 배포)
#   SKIP_OLLAMA_WIN      '1' 이면 Ollama Windows ZIP 다운로드 건너뜀 (Mac 전용 배포)
#   SKIP_MODEL_PULL      '1' 이면 모델 archive 제작 건너뜀 (이미 ollama-models-*.tgz 있는 경우)
#   OUTPUT_DIR           기본 ../  (e2e-pipeline/ 루트)
#   PACK_TGZ             '1' 이면 마지막에 번들 디렉토리를 단일 tar.gz 로 추가 압축
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 설정값 ────────────────────────────────────────────────────────────────
TARGET_PLATFORMS="${TARGET_PLATFORMS:-linux/amd64,linux/arm64}"
OLLAMA_MODELS_LIST="${OLLAMA_MODELS:-gemma4:e2b}"
SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-0}"
SKIP_OLLAMA_DMG="${SKIP_OLLAMA_DMG:-0}"
SKIP_OLLAMA_WIN="${SKIP_OLLAMA_WIN:-0}"
SKIP_MODEL_PULL="${SKIP_MODEL_PULL:-0}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR}"
PACK_TGZ="${PACK_TGZ:-0}"

TS="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="perf-allinone-bundle-${TS}"
BUNDLE_DIR="${OUTPUT_DIR}/${BUNDLE_NAME}"
ASSETS_DIR="${BUNDLE_DIR}/assets"

# ── 외부 자산 URL (검증된 공식 출처) ─────────────────────────────────────
OLLAMA_DMG_URL="https://ollama.com/download/Ollama.dmg"
OLLAMA_WIN_ZIP_URL="https://ollama.com/download/ollama-windows-amd64.zip"
NSSM_URL="https://nssm.cc/release/nssm-2.24.zip"

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
log()   { printf '\033[1;36m[prepare-bundle]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[prepare-bundle] ✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[prepare-bundle] !\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[1;31m[prepare-bundle] ✗\033[0m %s\n' "$*" >&2; exit 1; }

sha256_file() {
  local f="$1"
  if command -v sha256sum >/dev/null; then
    sha256sum "$f" | awk '{print $1}'
  elif command -v shasum >/dev/null; then
    shasum -a 256 "$f" | awk '{print $1}'
  else
    err "sha256sum / shasum 둘 다 없음."
  fi
}

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
command -v curl   >/dev/null || err "curl 명령을 찾을 수 없습니다."
docker buildx version >/dev/null 2>&1 || err "docker buildx 가 필요합니다."

[ -f "$SCRIPT_DIR/build-allinone.sh" ] || err "build-allinone.sh 누락"
[ -f "$SCRIPT_DIR/install-mac.sh" ]    || err "install-mac.sh 누락"
[ -f "$SCRIPT_DIR/install-windows.ps1" ] || err "install-windows.ps1 누락"
[ -f "$SCRIPT_DIR/README.md" ]         || err "README.md 누락"

mkdir -p "$ASSETS_DIR"

log "================================================================"
log "  perf-allinone 폐쇄망 배포 번들 제작"
log "  플랫폼: $TARGET_PLATFORMS"
log "  Ollama 모델: $OLLAMA_MODELS_LIST"
log "  번들 위치: $BUNDLE_DIR"
log "================================================================"

# ── 1. JMeter 컨테이너 이미지 빌드 ───────────────────────────────────────
if [ "$SKIP_IMAGE_BUILD" = "1" ]; then
  warn "[1/5] 컨테이너 이미지 빌드 건너뜀 (SKIP_IMAGE_BUILD=1)"
  warn "       기존 e2e-pipeline/dscore-qa-perf-allinone-*.tar.gz 를 assets/ 로 복사합니다."
  for f in "$ROOT_DIR"/dscore-qa-perf-allinone-*.tar.gz; do
    [ -e "$f" ] || err "재사용할 이미지 tar.gz 가 없습니다. SKIP_IMAGE_BUILD 해제 또는 사전 빌드."
    cp -f "$f" "$ASSETS_DIR/"
    [ -e "${f}.sha256" ] && cp -f "${f}.sha256" "$ASSETS_DIR/" || true
  done
else
  log "[1/5] 컨테이너 이미지 빌드 ($TARGET_PLATFORMS)"
  TARGET_PLATFORMS="$TARGET_PLATFORMS" \
    OUTPUT_DIR="$ASSETS_DIR" \
    bash "$SCRIPT_DIR/build-allinone.sh" \
    || err "컨테이너 이미지 빌드 실패"
fi

# ── 2. Ollama Mac DMG 다운로드 ────────────────────────────────────────────
if [ "$SKIP_OLLAMA_DMG" = "1" ]; then
  warn "[2/5] Ollama Mac DMG 건너뜀 (SKIP_OLLAMA_DMG=1, Windows 전용 배포)"
else
  log "[2/5] Ollama Mac DMG 다운로드"
  curl -fL --progress-bar -o "$ASSETS_DIR/Ollama.dmg" "$OLLAMA_DMG_URL" \
    || err "Ollama.dmg 다운로드 실패"
  ok "  Ollama.dmg ($(du -h "$ASSETS_DIR/Ollama.dmg" | cut -f1))"
fi

# ── 3. Ollama Windows ZIP + NSSM 다운로드 ────────────────────────────────
if [ "$SKIP_OLLAMA_WIN" = "1" ]; then
  warn "[3/5] Ollama Windows ZIP 건너뜀 (SKIP_OLLAMA_WIN=1, Mac 전용 배포)"
else
  log "[3/5] Ollama Windows standalone ZIP + NSSM 다운로드"
  curl -fL --progress-bar -o "$ASSETS_DIR/ollama-windows-amd64.zip" "$OLLAMA_WIN_ZIP_URL" \
    || err "Ollama Windows ZIP 다운로드 실패"
  ok "  ollama-windows-amd64.zip ($(du -h "$ASSETS_DIR/ollama-windows-amd64.zip" | cut -f1))"

  curl -fL --progress-bar -o "$ASSETS_DIR/nssm-2.24.zip" "$NSSM_URL" \
    || err "NSSM 다운로드 실패"
  ok "  nssm-2.24.zip ($(du -h "$ASSETS_DIR/nssm-2.24.zip" | cut -f1))"
fi

# ── 4. Ollama 모델 archive 제작 ──────────────────────────────────────────
if [ "$SKIP_MODEL_PULL" = "1" ]; then
  warn "[4/5] 모델 archive 건너뜀 (SKIP_MODEL_PULL=1)"
  warn "       OLLAMA_MODELS_DIR 환경변수로 외부 .tgz 를 직접 assets/ 에 두세요."
else
  log "[4/5] Ollama 모델 archive 제작 (호스트에 docker 임시 컨테이너로 pull)"
  log "       모델 목록: $OLLAMA_MODELS_LIST"

  # 임시 ollama 컨테이너로 모델 pull (호스트에 ollama 설치 불필요)
  TMP_VOL="prepare-bundle-ollama-$TS"
  TMP_NAME="prepare-bundle-ollama-$TS"

  log "  ollama 컨테이너 기동..."
  docker run -d --name "$TMP_NAME" \
    -v "$TMP_VOL:/root/.ollama" \
    -p 0:11434 \
    ollama/ollama:latest \
    >/dev/null

  # 11434 준비 대기
  for i in $(seq 1 30); do
    if docker exec "$TMP_NAME" curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  # 각 모델 pull
  IFS=',' read -ra MODELS <<< "$OLLAMA_MODELS_LIST"
  for m in "${MODELS[@]}"; do
    m="$(echo "$m" | xargs)"
    [ -z "$m" ] && continue
    log "  pull: $m"
    docker exec "$TMP_NAME" ollama pull "$m" || err "$m pull 실패"
  done

  log "  모델 디렉토리 archive..."
  # 컨테이너의 /root/.ollama (volume) → tar.gz 로 추출
  ARCHIVE_NAME="ollama-models-$(echo "$OLLAMA_MODELS_LIST" | tr ',:' '__')-${TS}.tgz"
  docker run --rm -v "$TMP_VOL:/data" -v "$ASSETS_DIR:/out" alpine \
    tar czf "/out/$ARCHIVE_NAME" -C /data .
  ok "  $ARCHIVE_NAME ($(du -h "$ASSETS_DIR/$ARCHIVE_NAME" | cut -f1))"

  # 정리
  log "  임시 ollama 컨테이너/볼륨 정리"
  docker stop "$TMP_NAME" >/dev/null 2>&1 || true
  docker rm   "$TMP_NAME" >/dev/null 2>&1 || true
  docker volume rm "$TMP_VOL" >/dev/null 2>&1 || true
fi

# ── 5. install 스크립트 / README / SHA256 합성 ───────────────────────────
log "[5/5] 설치 스크립트 / README / 무결성 파일 합성"

cp -f "$SCRIPT_DIR/install-mac.sh"      "$BUNDLE_DIR/"
cp -f "$SCRIPT_DIR/install-windows.ps1" "$BUNDLE_DIR/"
cp -f "$SCRIPT_DIR/README.md"           "$BUNDLE_DIR/"
cp -f "$SCRIPT_DIR/README-mac.md"       "$BUNDLE_DIR/"
cp -f "$SCRIPT_DIR/README-windows.md"   "$BUNDLE_DIR/"

chmod +x "$BUNDLE_DIR/install-mac.sh"

# 전체 자산 SHA256 합성 (assets/SHA256SUMS)
log "  SHA256 합성"
( cd "$ASSETS_DIR" && find . -type f ! -name 'SHA256SUMS' \
    -exec bash -c 'echo "$(sha256sum "$1" | cut -d" " -f1)  $(basename "$1")"' _ {} \; \
    | sort > SHA256SUMS )
ok "  $ASSETS_DIR/SHA256SUMS"

# 번들 정보 파일
cat > "$BUNDLE_DIR/BUNDLE_INFO.txt" <<EOF
perf-allinone Bundle
====================
Bundle Name : $BUNDLE_NAME
Built At    : $(date -u '+%Y-%m-%dT%H:%M:%SZ')
Built On    : $(uname -s) $(uname -m)
Platforms   : $TARGET_PLATFORMS
Ollama Models : $OLLAMA_MODELS_LIST

Contents:
$(cd "$BUNDLE_DIR" && find . -maxdepth 2 -type f | sort | sed 's|^\./|  |')

Total Size  : $(du -sh "$BUNDLE_DIR" | cut -f1)

Usage (offline target):
  Mac     : bash install-mac.sh
  Windows : powershell -ExecutionPolicy Bypass -File install-windows.ps1
EOF

ok "  BUNDLE_INFO.txt"

# ── 6. (선택) 단일 tar.gz 로 압축 ────────────────────────────────────────
if [ "$PACK_TGZ" = "1" ]; then
  log "[+] 번들 디렉토리 → 단일 tar.gz"
  ( cd "$OUTPUT_DIR" && tar czf "${BUNDLE_NAME}.tar.gz" "$BUNDLE_NAME" )
  ok "  $OUTPUT_DIR/${BUNDLE_NAME}.tar.gz ($(du -h "$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz" | cut -f1))"
  echo "$(sha256_file "$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz")  ${BUNDLE_NAME}.tar.gz" \
    > "$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz.sha256"
fi

log ""
log "=========================================================================="
log "번들 제작 완료"
log "=========================================================================="
log ""
log "산출:"
log "  📁 $BUNDLE_DIR/"
[ "$PACK_TGZ" = "1" ] && log "  📦 $OUTPUT_DIR/${BUNDLE_NAME}.tar.gz"
log ""
log "폐쇄망 배포 절차:"
log "  1) 위 디렉토리(또는 .tar.gz)를 USB 등으로 폐쇄망 호스트로 이동"
log "  2) 해당 호스트에서:"
log "     Mac:     bash install-mac.sh"
log "     Windows: 우클릭 install-windows.ps1 → 'PowerShell로 실행' (관리자)"
log "  3) 끝. 자동으로 Ollama 설치 + 모델 복원 + 컨테이너 기동 + 브라우저 열림."
log "=========================================================================="
