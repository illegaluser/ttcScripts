#!/usr/bin/env bash
# ============================================================================
# Zero-Touch QA All-in-One — 번들 빌드 스크립트 (온라인 머신 전용)
#
# 동작:
#   1) Jenkins 플러그인 hpi 다운로드 (jenkins-plugin-manager.jar 로 의존성 포함)
#   2) Dify 플러그인 .difypkg 다운로드 (langgenius/ollama)
#   3) docker buildx build 로 단일 이미지 제작
#   4) docker save | gzip 으로 배포용 tar.gz 산출
#
# 출력: dscore-qa-allinone-<timestamp>.tar.gz (e2e-pipeline/ 루트에)
#
# 요구:
#   - Docker 26+ (buildx 활성), 디스크 40GB+ 여유
#   - 온라인 (Docker Hub + PyPI + github.com + marketplace.dify.ai + updates.jenkins.io 접근)
#   - 빌드 소요: 30-90 분 (네트워크 속도 의존)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 설정값 ────────────────────────────────────────────────────────────────
IMAGE_TAG="${IMAGE_TAG:-dscore-qa:allinone}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"
OUTPUT_TAR="${OUTPUT_TAR:-dscore-qa-allinone-$(date +%Y%m%d-%H%M%S).tar.gz}"

JENKINS_PLUGINS=(
  workflow-aggregator
  file-parameters
  htmlpublisher
  plain-credentials
)
# JENKINS_VERSION 은 빌드 시점에 jenkins/jenkins:lts-jdk21 이미지에서 동적 추출.
# (PoC 2026-04-19: 최신 플러그인들이 2.479.x 이상을 요구하므로 2.462.3 고정은 다운로드 실패를 유발)
JENKINS_VERSION_OVERRIDE="${JENKINS_VERSION:-}"
JENKINS_PLUGIN_MANAGER_URL="https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.13.2/jenkins-plugin-manager-2.13.2.jar"

DIFY_PLUGIN_MARKETPLACE="https://marketplace.dify.ai"
DIFY_PLUGIN_ID="langgenius/ollama"
DIFY_VERSION="1.13.3"

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
log()  { printf '[build-allinone] %s\n' "$*"; }
err()  { printf '[build-allinone] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 사전 검증 ────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
command -v curl   >/dev/null || err "curl 명령을 찾을 수 없습니다."
command -v java   >/dev/null || err "java 명령을 찾을 수 없습니다 (JDK 11+ 필요)."
docker buildx version >/dev/null 2>&1 || err "docker buildx 가 필요합니다 (Docker 26+)."

[ -f "$ROOT_DIR/setup.sh" ]         || err "$ROOT_DIR/setup.sh 가 없습니다. e2e-pipeline 루트에서 실행하세요."
[ -f "$ROOT_DIR/dify-chatflow.yaml" ] || err "dify-chatflow.yaml 이 없습니다."
[ -d "$ROOT_DIR/zero_touch_qa" ]     || err "zero_touch_qa/ 디렉토리가 없습니다."
[ -f "$ROOT_DIR/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline" ] || err "Pipeline 정의 파일 없음."

log "빌드 대상: $IMAGE_TAG (platform=$TARGET_PLATFORM)"
log "출력 파일: $OUTPUT_TAR"

# ── 1. Jenkins 플러그인 hpi 다운로드 (의존성 재귀 해결) ──────────────────
log "[1/4] Jenkins 플러그인 다운로드 (+의존성)"
mkdir -p "$SCRIPT_DIR/jenkins-plugins"
if [ ! -f "$SCRIPT_DIR/jenkins-plugin-manager.jar" ]; then
  log "  jenkins-plugin-manager.jar 다운로드"
  curl -fL -o "$SCRIPT_DIR/jenkins-plugin-manager.jar" "$JENKINS_PLUGIN_MANAGER_URL"
fi

# 플러그인 매니저 설정: 각 플러그인을 :latest 로 요청. 의존성은 재귀 resolve.
PLUGIN_LIST_TXT="$SCRIPT_DIR/.plugins.txt"
: > "$PLUGIN_LIST_TXT"
for p in "${JENKINS_PLUGINS[@]}"; do
  echo "$p:latest" >> "$PLUGIN_LIST_TXT"
done

log "  플러그인 목록: ${JENKINS_PLUGINS[*]}"

# 빌드 머신에서 실제 사용할 Jenkins 버전 추출 (jenkins/jenkins:lts-jdk21 현재 태그)
if [ -n "$JENKINS_VERSION_OVERRIDE" ]; then
  JENKINS_VERSION_DETECTED="$JENKINS_VERSION_OVERRIDE"
else
  log "  jenkins/jenkins:lts-jdk21 버전 동적 추출 중..."
  JENKINS_VERSION_DETECTED=$(docker run --rm --entrypoint java jenkins/jenkins:lts-jdk21 \
    -jar /usr/share/jenkins/jenkins.war --version 2>/dev/null | head -n1 | tr -d '\r')
fi
[ -z "$JENKINS_VERSION_DETECTED" ] && err "Jenkins 버전 추출 실패 — JENKINS_VERSION 환경변수로 명시"
log "  대상 Jenkins 버전: $JENKINS_VERSION_DETECTED"

java -jar "$SCRIPT_DIR/jenkins-plugin-manager.jar" \
  --war "" \
  --plugin-download-directory "$SCRIPT_DIR/jenkins-plugins" \
  --plugin-file "$PLUGIN_LIST_TXT" \
  --jenkins-version "$JENKINS_VERSION_DETECTED" \
  --verbose || err "Jenkins 플러그인 다운로드 실패"

# PoC 2026-04-19: plugin-manager 는 .jpi 로 저장한다 (.hpi 와 동등, Jenkins 가 양쪽 모두 로드)
PLUGIN_FILE_COUNT=$(find "$SCRIPT_DIR/jenkins-plugins" \( -name '*.hpi' -o -name '*.jpi' \) | wc -l | tr -d ' ')
log "  다운로드된 플러그인 파일 개수: $PLUGIN_FILE_COUNT (hpi + jpi)"

# ── 2. Dify 플러그인 .difypkg 다운로드 ────────────────────────────────────
log "[2/4] Dify 플러그인 다운로드 ($DIFY_PLUGIN_ID)"
mkdir -p "$SCRIPT_DIR/dify-plugins"

PLUGIN_BATCH_RESP=$(curl -sS -X POST "$DIFY_PLUGIN_MARKETPLACE/api/v1/plugins/batch" \
  -H "Content-Type: application/json" \
  -H "X-Dify-Version: $DIFY_VERSION" \
  -d "{\"plugin_ids\":[\"$DIFY_PLUGIN_ID\"]}")

PLUGIN_UID=$(echo "$PLUGIN_BATCH_RESP" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    plugins = d.get('data',{}).get('plugins',[])
    print(plugins[0].get('latest_package_identifier','') if plugins else '')
except Exception:
    print('')
")
[ -z "$PLUGIN_UID" ] && err "marketplace 조회 실패. 응답: $PLUGIN_BATCH_RESP"

# unique_identifier 에서 버전 분리: "org/name:ver@sha"
PLUGIN_VERSION=$(echo "$PLUGIN_UID" | sed -n 's#.*:\([^@]*\)@.*#\1#p')
log "  최신 버전: $PLUGIN_VERSION (uid: ${PLUGIN_UID:0:60}...)"

# marketplace 에서 패키지 다운로드
# endpoint: /api/v1/plugins/download?unique_identifier=...
curl -fL -o "$SCRIPT_DIR/dify-plugins/${DIFY_PLUGIN_ID//\//-}-${PLUGIN_VERSION}.difypkg" \
  "$DIFY_PLUGIN_MARKETPLACE/api/v1/plugins/download?unique_identifier=$PLUGIN_UID" \
  || err "Dify 플러그인 다운로드 실패"

log "  저장: $SCRIPT_DIR/dify-plugins/"
ls -lh "$SCRIPT_DIR/dify-plugins/"

# ── 3. Docker 이미지 빌드 ─────────────────────────────────────────────────
log "[3/4] Docker 이미지 빌드 ($TARGET_PLATFORM)"
log "  (이 단계는 30-90분 소요될 수 있습니다)"

docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --file "$SCRIPT_DIR/Dockerfile.allinone" \
  --tag "$IMAGE_TAG" \
  --build-arg "OLLAMA_MODEL=$OLLAMA_MODEL" \
  --load \
  "$ROOT_DIR" \
  || err "docker buildx build 실패"

log "  이미지 크기:"
docker images "$IMAGE_TAG" --format '  {{.Repository}}:{{.Tag}}  {{.Size}}'

# ── 4. docker save + gzip 압축 ────────────────────────────────────────────
log "[4/4] 이미지 save + 압축 → $OUTPUT_TAR"
docker save "$IMAGE_TAG" | gzip -1 > "$ROOT_DIR/$OUTPUT_TAR"
log "  최종 파일: $ROOT_DIR/$OUTPUT_TAR ($(du -h "$ROOT_DIR/$OUTPUT_TAR" | cut -f1))"

log ""
log "=========================================================================="
log "빌드 완료"
log ""
log "폐쇄망 타겟으로 $OUTPUT_TAR 를 이동한 뒤 아래 명령으로 기동하세요."
log "운영 환경에 맞는 블록 하나만 복사해 사용하면 됩니다."
log ""
log "  공통 1단계: 이미지 로드"
log "    docker load -i $OUTPUT_TAR"
log ""
log "──────────────────────────────────────────────────────────────────────────"
log "[A] 일반 Linux 서버 (GPU 없음 / 온프레미스 폐쇄망)"
log "──────────────────────────────────────────────────────────────────────────"
log "  docker run -d --name dscore-qa \\"
log "    -p 18080:18080 -p 18081:18081 -p 50001:50001 \\"
log "    -v dscore-data:/data \\"
log "    --add-host host.docker.internal:host-gateway \\"
log "    --shm-size=2g \\"
log "    --restart unless-stopped \\"
log "    $IMAGE_TAG"
log ""
log "──────────────────────────────────────────────────────────────────────────"
log "[B] Windows 11 WSL2 + NVIDIA GPU"
log "──────────────────────────────────────────────────────────────────────────"
log "  사전: docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi  → 성공 확인"
log ""
log "  docker run -d --name dscore-qa \\"
log "    -p 18080:18080 -p 18081:18081 -p 50001:50001 \\"
log "    -v dscore-data:/data \\"
log "    --add-host host.docker.internal:host-gateway \\"
log "    --gpus all \\"
log "    --shm-size=2g \\"
log "    --restart unless-stopped \\"
log "    $IMAGE_TAG"
log ""
log "  Jenkins Pipeline 실행 시 HEADLESS 파라미터 반드시 체크 (컨테이너 X display 없음)"
log ""
log "──────────────────────────────────────────────────────────────────────────"
log "[C] Linux + NVIDIA GPU 서버 (베어메탈/클라우드)"
log "──────────────────────────────────────────────────────────────────────────"
log "  [B] 와 동일. nvidia-container-toolkit 호스트 설치 여부만 확인."
log ""
log "──────────────────────────────────────────────────────────────────────────"
log "[D] Apple Silicon Mac (Docker Desktop) — Metal 은 컨테이너로 passthrough 되지 않음"
log "──────────────────────────────────────────────────────────────────────────"
log "  호스트에 Ollama 설치 필요. 별도 브랜치 feat/allinone-mac-host-ollama 참조."
log ""
log "초기 기동 후 5-10분 지나면 다음이 가용합니다:"
log "  - Jenkins:  http://<host>:18080  (admin / password)"
log "  - Dify:     http://<host>:18081  (admin@example.com / Admin1234!)"
log ""
log "WSL2/GPU 옵션 상세: README.md 섹션 2.3 (c) 참조."
log "=========================================================================="
