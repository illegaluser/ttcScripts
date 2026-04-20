#!/usr/bin/env bash
# ============================================================================
# Zero-Touch QA All-in-One — 호스트 하이브리드 이미지 빌드 스크립트 (온라인 머신)
#
# 본 스크립트는 호스트 Ollama + 호스트 agent 하이브리드 이미지를 만든다.
# 설계: Mac (Metal GPU passthrough 부재) 과 Windows (headed Chromium 이 Windows
# 네이티브 화면에서만 뜸) 공통 요구 해결. 컨테이너는 Jenkins controller + Dify 만.
#
# 원본 All-in-One 대비 차이:
#   - 컨테이너 내부 Ollama 제거 (바이너리·모델 seed·supervisord 프로그램 없음)
#   - 컨테이너 내부 Jenkins agent 제거 (호스트에서 JNLP 연결)
#   - 사전 `ollama pull` 단계 없음 → 빌드 빠름 (4GB 모델 다운로드 스킵)
#   - 결과 이미지 5-7GB (원본 All-in-One 10-12GB 대비 4-5GB 절감)
#
# 동작:
#   1) Jenkins 플러그인 hpi 다운로드 (jenkins-plugin-manager.jar 로 의존성 포함)
#   2) Dify 플러그인 .difypkg 다운로드 (langgenius/ollama)
#   3) docker buildx build 로 단일 이미지 제작
#   4) docker save | gzip 으로 배포용 tar.gz 산출
#
# 출력: dscore.ttc.playwright-<timestamp>.tar.gz (e2e-pipeline/ 루트에)
#
# 요구:
#   - Docker 26+ (buildx 활성), 디스크 20GB+ 여유
#   - 온라인 (Docker Hub + PyPI + github.com + marketplace.dify.ai + updates.jenkins.io 접근)
#   - 빌드 소요: 10-30 분 (ollama pull 단계 없어 원본보다 크게 단축)
#
# 런타임 전제:
#   - 호스트 (Mac 또는 Windows) 에 Ollama 가 설치·기동되어 있어야 함
#   - 호스트에 JDK 21 + Python 3.11+ 설치 필요 (agent 용)
#   - docker run 에 `--add-host host.docker.internal:host-gateway` 필수
#   - 자세한 가이드: README §4 (Mac), §5 (Windows)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 설정값 ────────────────────────────────────────────────────────────────
IMAGE_TAG="${IMAGE_TAG:-dscore.ttc.playwright:latest}"

# TARGET_PLATFORM: 호스트 아키텍처를 자동 감지하되, env 로 override 가능.
# "빌드 머신 OS/아키텍처 = 런타임 머신 OS/아키텍처" 가 기본 가정 (qemu 에뮬레이션 회피).
#   - Apple Silicon Mac  → linux/arm64  (네이티브)
#   - Intel Mac / Windows (WSL2/Docker Desktop) / Linux x86 → linux/amd64
#   - qemu 크로스 빌드는 playwright chromium 설치가 silent-fail 하는 등 결함이 잦음
#   - 다른 아키의 서버로 배포하려면 TARGET_PLATFORM=linux/<amd64|arm64> 로 명시 override
if [ -z "${TARGET_PLATFORM:-}" ]; then
  case "$(uname -m)" in
    arm64|aarch64) TARGET_PLATFORM="linux/arm64" ;;
    x86_64|amd64)  TARGET_PLATFORM="linux/amd64" ;;
    *)             TARGET_PLATFORM="linux/amd64" ;;  # fallback
  esac
fi

# OLLAMA_MODEL: 이미지에 사전 pull 되지 않음 (이 이미지는 호스트 Ollama 사용).
# 이 값은 docker buildx 가 Dockerfile ARG 로 받아두긴 하지만 실질적 효과는 없음.
# 실제 런타임 모델 지정은 docker run 의 `-e OLLAMA_MODEL=...` 로 Dify provider 에 등록됨.
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"
OUTPUT_TAR="${OUTPUT_TAR:-dscore.ttc.playwright-$(date +%Y%m%d-%H%M%S).tar.gz}"

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
log "빌드 완료 (호스트 하이브리드 이미지 — 내부 Ollama·agent 없음)"
log ""
log "아키텍처:"
log "  Jenkins master + Dify + DB  → 컨테이너 (이 이미지)"
log "  Ollama (LLM 추론)          → 호스트 (Mac: Metal / WSL2: CUDA)"
log "  Jenkins agent + Playwright → 호스트 (headed Chromium)"
log "                              — Mac: macOS 네이티브 창"
log "                              — WSL2: WSLg 경유 Windows 데스크탑 창"
log ""
log "[사전 준비 — 호스트 Mac]"
log "  A. Ollama:  brew install ollama && brew services start ollama && ollama pull ${OLLAMA_MODEL}"
log "  B. JDK 21:  brew install --cask temurin@21   (또는 openjdk@21)"
log "  C. Python:  brew install python@3.12   (3.11+)"
log ""
log "[사전 준비 — Windows 11 하이브리드 (Ollama = Windows 네이티브, agent = WSL2 Ubuntu)]"
log "  0. Windows NVIDIA 드라이버 + WSL2 + Ubuntu 22.04 설치 (PowerShell 관리자: wsl --install -d Ubuntu-22.04)"
log "  A. Ollama (Windows 네이티브 — PowerShell): winget install Ollama.Ollama && ollama pull ${OLLAMA_MODEL}"
log "     (Windows Ollama 가 host.docker.internal 로 Docker Desktop 포워딩되어 컨테이너에서 사용됨)"
log "  B. WSL2 Ubuntu 안 — JDK 21:  sudo apt install -y openjdk-21-jdk-headless"
log "  C. WSL2 Ubuntu 안 — Python:  sudo apt install -y python3.12 python3.12-venv"
log "  D. Docker:  Docker Desktop (WSL2 백엔드) 또는 WSL native Docker Engine"
log ""
log "[이미지 로드 및 컨테이너 기동 — Mac / WSL2 동일]"
log "  docker load -i $OUTPUT_TAR"
log "  docker run -d --name dscore.ttc.playwright \\"
log "    -p 18080:18080 -p 18081:18081 -p 50001:50001 \\"
log "    -v dscore-data:/data \\"
log "    --add-host host.docker.internal:host-gateway \\"
log "    -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \\"
log "    -e OLLAMA_MODEL=${OLLAMA_MODEL} \\"
log "    --restart unless-stopped \\"
log "    $IMAGE_TAG"
log ""
log "[호스트 agent 연결 — 컨테이너 기동 완료 후]"
log "  1) docker logs dscore.ttc.playwright | grep NODE_SECRET        # 64자 hex 값 확인"
log "  2) Mac:  NODE_SECRET=<위 값> ./offline/mac-agent-setup.sh"
log "     WSL2: NODE_SECRET=<위 값> ./offline/wsl-agent-setup.sh"
log "     → JDK/venv/Chromium 설치 + agent 연결. 성공 시 호스트 화면에 headed Chromium"
log ""
log "접속:"
log "  - Jenkins:  http://localhost:18080  (admin / password)"
log "  - Dify:     http://localhost:18081  (admin@example.com / Admin1234!)"
log ""
log "⚠️  호스트 Ollama 미기동 / 호스트 agent 미연결 시 Pipeline Stage 3 가 실패한다."
log "    자세한 가이드: e2e-pipeline/offline/README.md"
log "=========================================================================="
