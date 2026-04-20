#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 빌드 전 플러그인 다운로드 (온라인 단계)
#
# Jenkins plugin manager 로 .jpi / .hpi 재귀 수집, Dify marketplace 에서
# .difypkg 다운로드. 결과물을 이 폴더 하위에 두면 Dockerfile 이 COPY 로 반입.
#
# playwright-allinone/build.sh 의 [1/4]-[2/4] 로직과 동일하되 독립 실행 가능
# (빌드 스크립트와 분리해 재실행/캐시 편의성 확보).
#
# 실행: bash scripts/download-plugins.sh
# 결과:
#   <allinone-dir>/jenkins-plugin-manager.jar
#   <allinone-dir>/jenkins-plugins/*.jpi
#   <allinone-dir>/dify-plugins/langgenius-ollama-*.difypkg
#   <allinone-dir>/.plugins.txt
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ALLINONE_DIR"

log()  { printf '[download-plugins] %s\n' "$*"; }
err()  { printf '[download-plugins] ERROR: %s\n' "$*" >&2; exit 1; }

# ── 설정 ────────────────────────────────────────────────────────────────────
JENKINS_PLUGINS=(
  workflow-aggregator
  file-parameters
  htmlpublisher
  plain-credentials
  # code-AI-quality 추가 요구 (Active Choices — DSCORE-TTC AI평가.jenkinsPipeline)
  uno-choice
)
JENKINS_PLUGIN_MANAGER_URL="https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.13.2/jenkins-plugin-manager-2.13.2.jar"
JENKINS_VERSION_OVERRIDE="${JENKINS_VERSION:-}"

DIFY_PLUGIN_MARKETPLACE="https://marketplace.dify.ai"
DIFY_PLUGIN_ID="langgenius/ollama"
DIFY_VERSION="1.13.3"

# ── 사전 검증 ─────────────────────────────────────────────────────────────
command -v docker >/dev/null || err "docker 명령을 찾을 수 없습니다."
command -v curl   >/dev/null || err "curl 명령을 찾을 수 없습니다."
command -v java   >/dev/null || err "java 명령을 찾을 수 없습니다 (JDK 11+ 필요)."

# ── 1. Jenkins 플러그인 ──────────────────────────────────────────────────
log "[1/2] Jenkins 플러그인 다운로드 (의존성 재귀 해결)"
mkdir -p "$ALLINONE_DIR/jenkins-plugins"
if [ ! -f "$ALLINONE_DIR/jenkins-plugin-manager.jar" ]; then
    log "  jenkins-plugin-manager.jar 다운로드"
    curl -fL -o "$ALLINONE_DIR/jenkins-plugin-manager.jar" "$JENKINS_PLUGIN_MANAGER_URL"
fi

PLUGIN_LIST_TXT="$ALLINONE_DIR/.plugins.txt"
: > "$PLUGIN_LIST_TXT"
for p in "${JENKINS_PLUGINS[@]}"; do
    echo "$p:latest" >> "$PLUGIN_LIST_TXT"
done
log "  플러그인 목록: ${JENKINS_PLUGINS[*]}"

if [ -n "$JENKINS_VERSION_OVERRIDE" ]; then
    JENKINS_VERSION_DETECTED="$JENKINS_VERSION_OVERRIDE"
else
    log "  jenkins/jenkins:lts-jdk21 버전 동적 추출 중..."
    JENKINS_VERSION_DETECTED=$(docker run --rm --entrypoint java jenkins/jenkins:lts-jdk21 \
        -jar /usr/share/jenkins/jenkins.war --version 2>/dev/null | head -n1 | tr -d '\r')
fi
[ -z "$JENKINS_VERSION_DETECTED" ] && err "Jenkins 버전 추출 실패 — JENKINS_VERSION env 명시"
log "  대상 Jenkins 버전: $JENKINS_VERSION_DETECTED"

java -jar "$ALLINONE_DIR/jenkins-plugin-manager.jar" \
    --war "" \
    --plugin-download-directory "$ALLINONE_DIR/jenkins-plugins" \
    --plugin-file "$PLUGIN_LIST_TXT" \
    --jenkins-version "$JENKINS_VERSION_DETECTED" \
    --verbose || err "Jenkins 플러그인 다운로드 실패"

PLUGIN_COUNT=$(find "$ALLINONE_DIR/jenkins-plugins" \( -name '*.hpi' -o -name '*.jpi' \) | wc -l | tr -d ' ')
log "  다운로드된 플러그인 개수: $PLUGIN_COUNT"

# ── 2. Dify 플러그인 ─────────────────────────────────────────────────────
log "[2/2] Dify 플러그인 다운로드 ($DIFY_PLUGIN_ID)"
mkdir -p "$ALLINONE_DIR/dify-plugins"

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

PLUGIN_VERSION=$(echo "$PLUGIN_UID" | sed -n 's#.*:\([^@]*\)@.*#\1#p')
log "  최신 버전: $PLUGIN_VERSION (uid: ${PLUGIN_UID:0:60}...)"

curl -fL -o "$ALLINONE_DIR/dify-plugins/${DIFY_PLUGIN_ID//\//-}-${PLUGIN_VERSION}.difypkg" \
    "$DIFY_PLUGIN_MARKETPLACE/api/v1/plugins/download?unique_identifier=$PLUGIN_UID" \
    || err "Dify 플러그인 다운로드 실패"

log "  저장:"
ls -lh "$ALLINONE_DIR/dify-plugins/"

log "플러그인 다운로드 완료. 이제 bash scripts/build-wsl2.sh (또는 build-mac.sh) 로 이미지 빌드."
