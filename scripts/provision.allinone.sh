#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 최초 프로비저닝
#
# 최초 기동 시 1회만 수행하는 자동 설정. 멱등성을 지키기 위해 각 단계가 이미
# 설정되었는지 조회 후 건너뛴다.
#
# 현재 자동화 범위 (Phase 1 — MVP):
#   - Jenkins 4개 Job 등록 (DSCORE-TTC 코드 사전학습, 정적분석, 결과분석/이슈등록,
#     AI평가)
#
# 수동으로 수행할 것 (별도 README 섹션에 명시):
#   - Dify 첫 관리자 계정 생성 (http://localhost:28081/install)
#   - Dify Chatflow import + API key 발급
#   - SonarQube 관리자 비밀번호 변경 (초기 admin/admin)
#   - SonarQube 토큰 발급 후 Jenkins Credentials 등록
#   - GitLab PAT → Jenkins Credentials 등록
#   - Ollama 모델 호스트 pull
#
# 추후 Phase 2: Dify provision (기존 provision-apps.sh 재사용) + SonarQube
# API 로 token/프로젝트 생성 자동화.
# ============================================================================
set -euo pipefail

LOG_PREFIX="[provision.allinone]"
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }
err()  { printf '%s ERROR: %s\n' "$LOG_PREFIX" "$*" >&2; }

JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:28080}"
JENKINS_USER="${JENKINS_USER:-admin}"
JENKINS_PASSWORD="${JENKINS_PASSWORD:-password}"
JENKINSFILE_DIR="${OFFLINE_JENKINSFILE_DIR:-/opt/jenkinsfiles}"

# ─ CSRF crumb 획득
get_crumb() {
    curl -sS -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null | \
        python3 -c "import json,sys; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null || true
}

# ─ Job 존재 여부
job_exists() {
    local name="$1"
    curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/job/$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$name")/api/json"
}

# ─ Inline pipeline job 등록
create_pipeline_job() {
    local name="$1"
    local jenkinsfile_path="$2"

    if [ ! -f "$jenkinsfile_path" ]; then
        warn "Jenkinsfile 없음: $jenkinsfile_path — $name 등록 건너뜀"
        return 0
    fi

    if job_exists "$name"; then
        log "  Job 이미 존재: $name — 건너뜀"
        return 0
    fi

    local encoded
    encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$name")

    # Jenkins config.xml — Pipeline inline script 형태
    local tmp_xml
    tmp_xml=$(mktemp)
    python3 <<PY > "$tmp_xml"
import html, sys
with open(r"$jenkinsfile_path", encoding="utf-8") as f:
    script = f.read()
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>TTC All-in-One: $name</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>""".format(html.escape(script)))
PY

    local crumb
    crumb=$(get_crumb)
    local rc=0
    if [ -n "$crumb" ]; then
        curl -sS -f -o /dev/null -X POST \
            -u "$JENKINS_USER:$JENKINS_PASSWORD" \
            -H "$crumb" \
            -H "Content-Type: application/xml" \
            --data-binary "@$tmp_xml" \
            "$JENKINS_URL/createItem?name=$encoded" \
            && log "  Job 등록: $name" || rc=$?
    else
        warn "  crumb 획득 실패 — $name 등록 스킵"
        rc=1
    fi
    rm -f "$tmp_xml"
    return $rc
}

log "Jenkins 응답 확인..."
_w=0
until curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" "$JENKINS_URL/api/json"; do
    sleep 3; _w=$((_w + 3))
    if [ $_w -ge 180 ]; then
        err "Jenkins 3분 내 응답 없음 — 프로비저닝 중단"
        exit 1
    fi
done
log "Jenkins 응답 OK (${_w}s)"

log "4개 Pipeline Job 등록 시도..."
create_pipeline_job "DSCORE-TTC-코드-사전학습"                          "$JENKINSFILE_DIR/DSCORE-TTC 코드 사전학습.jenkinsPipeline" || true
create_pipeline_job "DSCORE-TTC-코드-정적분석"                          "$JENKINSFILE_DIR/DSCORE-TTC 코드 정적분석.jenkinsPipeline" || true
create_pipeline_job "DSCORE-TTC-정적분석-결과분석-이슈등록"             "$JENKINSFILE_DIR/DSCORE-TTC 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline" || true
create_pipeline_job "DSCORE-TTC-AI평가"                                 "$JENKINSFILE_DIR/DSCORE-TTC AI평가.jenkinsPipeline" || true

log "Job 등록 시도 완료. 다음 수동 단계 안내:"
log "  1) http://localhost:28081/install 에서 Dify 초기 관리자 생성 + Chatflow import"
log "  2) http://localhost:29000 (admin/admin) SonarQube 비밀번호 변경 + 토큰 발급"
log "  3) Jenkins → Manage Jenkins → Credentials 에 Dify/Sonar/GitLab 자격증명 등록"
log "  4) 호스트에서 'ollama serve' + 'ollama pull gemma4:e4b' 실행"
