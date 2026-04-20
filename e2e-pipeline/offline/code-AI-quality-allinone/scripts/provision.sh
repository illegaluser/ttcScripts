#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 최초 프로비저닝 (완전 자동화)
#
# 최초 기동 시 1회만 수행. 각 단계는 멱등성 보장 — 중간 실패 후 재실행 OK.
# 자동화 범위:
#   A. Jenkins 4개 Job 등록 (사전학습/정적분석/결과분석+이슈등록/AI평가)
#   B. Dify 관리자 setup + 로그인
#   C. Dify Ollama provider 등록 (호스트 Ollama 가리킴)
#   D. Dify Knowledge Dataset 생성 (code-context-kb)
#   E. Dify Sonar Analyzer Workflow import
#   F. Dify API Key 발급 (Dataset, Workflow)
#   G. GitLab root PAT 발급 via REST API
#   H. Jenkins Credentials 자동 주입
#       - dify-dataset-id
#       - dify-knowledge-key
#       - dify-workflow-key
#       - gitlab-pat
#   I. Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입
#
# 필수 환경변수 (docker-compose 에서 주입):
#   JENKINS_URL             http://127.0.0.1:${JENKINS_PORT:-28080}
#   DIFY_URL                http://127.0.0.1:${DIFY_GATEWAY_PORT:-28081}
#   SONAR_URL               http://127.0.0.1:9000
#   GITLAB_URL_INTERNAL     http://gitlab:80 (docker 내부 DNS)
#   GITLAB_ROOT_PASSWORD    초기 GitLab root 비밀번호
#   OLLAMA_BASE_URL         http://host.docker.internal:11434
# ============================================================================
set -uo pipefail

LOG_PREFIX="[provision.allinone]"
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }
err()  { printf '%s ERROR: %s\n' "$LOG_PREFIX" "$*" >&2; }

# ─ 설정 ────────────────────────────────────────────────────────────────────
JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:28080}"
JENKINS_USER="${JENKINS_USER:-admin}"
JENKINS_PASSWORD="${JENKINS_PASSWORD:-password}"
JENKINSFILE_DIR="${OFFLINE_JENKINSFILE_DIR:-/opt/jenkinsfiles}"

DIFY_URL="${DIFY_URL:-http://127.0.0.1:28081}"
DIFY_ADMIN_EMAIL="${DIFY_ADMIN_EMAIL:-admin@ttc.local}"
DIFY_ADMIN_NAME="${DIFY_ADMIN_NAME:-admin}"
DIFY_ADMIN_PASSWORD="${DIFY_ADMIN_PASSWORD:-TtcAdmin!2026}"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"

GITLAB_URL="${GITLAB_URL_INTERNAL:-http://gitlab:80}"
GITLAB_ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD:-ChangeMe!Pass}"

DIFY_ASSETS_DIR="${DIFY_ASSETS_DIR:-/opt/dify-assets}"
STATE_DIR="${STATE_DIR:-/data/.provision}"
mkdir -p "$STATE_DIR"

# ─ 공통: URL-safe name → Jenkins Job name ───────────────────────────────────
urlencode() {
    python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"
}

# ─ Jenkins crumb ────────────────────────────────────────────────────────────
jenkins_crumb() {
    curl -sS -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null || true
}

# ─ Jenkins Job 존재 여부 ────────────────────────────────────────────────────
jenkins_job_exists() {
    curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/job/$(urlencode "$1")/api/json"
}

# ─ Jenkins Inline Pipeline Job 등록 ────────────────────────────────────────
jenkins_create_pipeline_job() {
    local name="$1" jenkinsfile="$2"
    [ ! -f "$jenkinsfile" ] && { warn "Jenkinsfile 없음: $jenkinsfile — $name 건너뜀"; return 0; }
    jenkins_job_exists "$name" && { log "  Job 이미 존재: $name"; return 0; }

    local encoded tmp_xml rc=0
    encoded=$(urlencode "$name")
    tmp_xml=$(mktemp)
    python3 <<PY > "$tmp_xml"
import html
with open(r"$jenkinsfile", encoding="utf-8") as f:
    script = f.read()
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>TTC All-in-One auto-provisioned: $name</description>
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

    local crumb; crumb=$(jenkins_crumb)
    [ -z "$crumb" ] && { warn "  crumb 획득 실패 — $name 등록 스킵"; rm -f "$tmp_xml"; return 1; }

    curl -sS -f -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        -H "$crumb" \
        -H "Content-Type: application/xml" \
        --data-binary "@$tmp_xml" \
        "$JENKINS_URL/createItem?name=$encoded" \
        && log "  Job 등록: $name" || rc=$?
    rm -f "$tmp_xml"
    return $rc
}

# ─ Jenkins Credentials: Secret text 생성/갱신 ──────────────────────────────
jenkins_upsert_string_credential() {
    local id="$1" secret="$2" description="${3:-auto-provisioned}"
    local crumb; crumb=$(jenkins_crumb)
    [ -z "$crumb" ] && { warn "crumb 없음 — credential $id 스킵"; return 1; }

    local payload
    payload=$(python3 <<PY
import json, urllib.parse
obj = {
    "": "0",
    "credentials": {
        "scope": "GLOBAL",
        "id": "$id",
        "secret": """$secret""",
        "description": "$description",
        "\$class": "org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl"
    }
}
print("json=" + urllib.parse.quote(json.dumps(obj)))
PY
)

    # 기존 삭제 후 재생성 (멱등)
    local encoded; encoded=$(urlencode "$id")
    curl -sS -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" -H "$crumb" \
        "$JENKINS_URL/credentials/store/system/domain/_/credential/$encoded/doDelete" 2>/dev/null || true

    curl -sS -f -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        -H "$crumb" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data "$payload" \
        "$JENKINS_URL/credentials/store/system/domain/_/createCredentials" \
        && log "  Credential 주입: $id" \
        || { warn "  Credential 주입 실패: $id"; return 1; }
}

# ─ Dify: 관리자 setup (최초 1회만) ──────────────────────────────────────────
dify_setup_admin() {
    local setup_url="$DIFY_URL/console/api/setup"
    local state; state=$(curl -sS "$setup_url" 2>/dev/null || echo '{}')
    if echo "$state" | grep -q '"step":"finished"'; then
        log "Dify setup 이미 완료됨 — 건너뜀"
        return 0
    fi

    log "Dify 관리자 초기 setup: $DIFY_ADMIN_EMAIL"
    curl -sS -f -X POST "$setup_url" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json,os; print(json.dumps({'email':'$DIFY_ADMIN_EMAIL','name':'$DIFY_ADMIN_NAME','password':'$DIFY_ADMIN_PASSWORD'}))")" \
        >/dev/null \
        && log "Dify setup 완료" \
        || { err "Dify setup 실패"; return 1; }
}

# ─ Dify: 로그인 → access_token ─────────────────────────────────────────────
dify_login() {
    curl -sS -X POST "$DIFY_URL/console/api/login" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json; print(json.dumps({'email':'$DIFY_ADMIN_EMAIL','password':'$DIFY_ADMIN_PASSWORD','language':'ko-KR','remember_me':True}))")" \
        2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('access_token',''))" 2>/dev/null \
        || echo ""
}

# ─ Dify: Ollama provider 등록 ──────────────────────────────────────────────
dify_register_ollama_provider() {
    local token="$1"
    log "Dify Ollama provider 등록 ($OLLAMA_BASE_URL, 모델=$OLLAMA_MODEL)"
    local payload
    payload=$(python3 <<PY
import json
print(json.dumps({
    "credentials": {
        "base_url": "$OLLAMA_BASE_URL",
        "mode": "chat",
        "model_name": "$OLLAMA_MODEL",
        "context_size": "8192",
        "max_tokens": "4096",
        "completion_type": "chat_completion"
    }
}))
PY
)
    curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/ollama/models" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        -o /tmp/dify-provider.json 2>/dev/null
    grep -q 'error\|exception' /tmp/dify-provider.json \
        && warn "  Ollama provider 등록 경고 — 수동 확인: $DIFY_URL/console/api/workspaces/current/model-providers" \
        || log "  Ollama provider 등록 완료"
}

# ─ Dify: Knowledge Dataset 생성 → dataset_id ───────────────────────────────
dify_create_dataset() {
    local token="$1"
    local cached="$STATE_DIR/dataset_id"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    local payload; payload=$(cat "$DIFY_ASSETS_DIR/code-context-dataset.json" | python3 -c "import json,sys; d=json.load(sys.stdin); d.pop('_comment',None); print(json.dumps(d))")
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/datasets" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    local id; id=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)
    if [ -n "$id" ]; then
        echo "$id" > "$cached"
        log "  Dataset 생성: $id"
        echo "$id"
    else
        warn "  Dataset 생성 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: Dataset API key 발급 ───────────────────────────────────────────────
dify_issue_dataset_api_key() {
    local token="$1" dataset_id="$2"
    local cached="$STATE_DIR/dataset_api_key"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/datasets/$dataset_id/api-keys" \
        -H "Authorization: Bearer $token" 2>/dev/null)
    local key; key=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$key" ]; then
        echo "$key" > "$cached"
        log "  Dataset API key 발급"
        echo "$key"
    else
        warn "  Dataset API key 발급 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: Sonar Analyzer Workflow import → app_id ──────────────────────────
dify_import_workflow() {
    local token="$1"
    local cached="$STATE_DIR/workflow_app_id"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    local yaml_content; yaml_content=$(cat "$DIFY_ASSETS_DIR/sonar-analyzer-workflow.yaml")
    local payload; payload=$(python3 <<PY
import json
with open("$DIFY_ASSETS_DIR/sonar-analyzer-workflow.yaml", encoding="utf-8") as f:
    yc = f.read()
print(json.dumps({"mode": "yaml-content", "yaml_content": yc}))
PY
)
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/imports" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    local id; id=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('app_id') or d.get('id') or '')" 2>/dev/null || true)
    if [ -n "$id" ]; then
        echo "$id" > "$cached"
        log "  Workflow import: $id"
        echo "$id"
    else
        warn "  Workflow import 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: App API key 발급 ──────────────────────────────────────────────────
dify_issue_app_api_key() {
    local token="$1" app_id="$2"
    local cached="$STATE_DIR/workflow_api_key"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/$app_id/api-keys" \
        -H "Authorization: Bearer $token" 2>/dev/null)
    local key; key=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$key" ]; then
        echo "$key" > "$cached"
        log "  App API key 발급"
        echo "$key"
    else
        warn "  App API key 발급 실패 — 응답: $resp"
        return 1
    fi
}

# ─ GitLab: 대기 + root PAT 발급 ────────────────────────────────────────────
gitlab_wait_ready() {
    log "GitLab 헬스 대기 (최대 15분)..."
    local w=0 limit=900
    until curl -sf -o /dev/null "$GITLAB_URL/users/sign_in"; do
        sleep 10; w=$((w + 10))
        if [ $w -ge $limit ]; then
            err "GitLab 15분 내 준비되지 않음. 컨테이너 로그 확인: docker logs ttc-gitlab"
            return 1
        fi
        [ $((w % 60)) -eq 0 ] && log "  GitLab 대기 중... (${w}s)"
    done
    log "GitLab ready (${w}s)"
}

gitlab_issue_root_pat() {
    local cached="$STATE_DIR/gitlab_root_pat"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    # 1. oauth password grant 로 access_token 획득
    local oauth_resp; oauth_resp=$(curl -sS -X POST "$GITLAB_URL/oauth/token" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json; print(json.dumps({'grant_type':'password','username':'root','password':'$GITLAB_ROOT_PASSWORD'}))")" \
        2>/dev/null)
    local access_token; access_token=$(echo "$oauth_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || true)
    if [ -z "$access_token" ]; then
        warn "GitLab oauth 실패 — 응답: $oauth_resp"
        return 1
    fi

    # 2. root (id=1) 에 대해 personal_access_token 발급 (admin API)
    local pat_resp; pat_resp=$(curl -sS -X POST "$GITLAB_URL/api/v4/users/1/personal_access_tokens" \
        -H "Authorization: Bearer $access_token" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json; print(json.dumps({'name':'ttc-auto','scopes':['api','read_repository','write_repository'],'expires_at':'2099-12-31'}))")" \
        2>/dev/null)
    local pat; pat=$(echo "$pat_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$pat" ]; then
        echo "$pat" > "$cached"
        log "GitLab root PAT 발급"
        echo "$pat"
    else
        warn "GitLab PAT 발급 실패 — 응답: $pat_resp"
        return 1
    fi
}

# ─ Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입 ──────────────────
patch_jenkinsfile_gitlab_credentials() {
    log "Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입"
    # Dockerfile 빌드 시점에 이미 `GITLAB_PAT = ''` / `GITLAB_TOKEN=""` 로 치환됨.
    # 런타임에 이를 credentials('gitlab-pat') 참조로 재치환.
    cd "$JENKINSFILE_DIR"
    sed -i "s|GITLAB_PAT = ''|GITLAB_PAT = credentials('gitlab-pat')|g" *.jenkinsPipeline
    sed -i 's|GITLAB_TOKEN=""|GITLAB_TOKEN="${GITLAB_PAT}"|g' *.jenkinsPipeline
    log "  치환 완료"
}

# ═════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════
log "=========================================="
log "TTC 4-Pipeline All-in-One 자동 프로비저닝 시작"
log "=========================================="

# A. Jenkins 대기
log "Jenkins 응답 확인..."
_w=0
until curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" "$JENKINS_URL/api/json"; do
    sleep 3; _w=$((_w + 3))
    [ $_w -ge 180 ] && { err "Jenkins 3분 내 응답 없음"; exit 1; }
done
log "  Jenkins OK (${_w}s)"

# B+C+D+E+F. Dify 전체 자동화
log "Dify 자동화 시작..."
if dify_setup_admin; then
    DIFY_TOKEN=$(dify_login)
    if [ -n "$DIFY_TOKEN" ]; then
        log "  Dify 로그인 성공"
        dify_register_ollama_provider "$DIFY_TOKEN" || true
        DATASET_ID=$(dify_create_dataset "$DIFY_TOKEN" || echo "")
        [ -n "$DATASET_ID" ] && KNOWLEDGE_KEY=$(dify_issue_dataset_api_key "$DIFY_TOKEN" "$DATASET_ID" || echo "")
        WORKFLOW_APP_ID=$(dify_import_workflow "$DIFY_TOKEN" || echo "")
        [ -n "$WORKFLOW_APP_ID" ] && WORKFLOW_KEY=$(dify_issue_app_api_key "$DIFY_TOKEN" "$WORKFLOW_APP_ID" || echo "")
    else
        warn "Dify 로그인 실패 — 수동 확인 필요"
    fi
else
    warn "Dify setup 실패 — 후속 단계 건너뜀"
fi

# G. GitLab
GITLAB_PAT=""
if gitlab_wait_ready; then
    GITLAB_PAT=$(gitlab_issue_root_pat || echo "")
fi

# H. Jenkins Credentials 주입
log "Jenkins Credentials 주입..."
[ -n "${DATASET_ID:-}" ]     && jenkins_upsert_string_credential "dify-dataset-id"   "$DATASET_ID"     "Dify Code Context Dataset ID"
[ -n "${KNOWLEDGE_KEY:-}" ]  && jenkins_upsert_string_credential "dify-knowledge-key" "$KNOWLEDGE_KEY" "Dify Knowledge API Key"
[ -n "${WORKFLOW_KEY:-}" ]   && jenkins_upsert_string_credential "dify-workflow-key" "$WORKFLOW_KEY"  "Dify Sonar Analyzer Workflow API Key"
[ -n "$GITLAB_PAT" ]         && jenkins_upsert_string_credential "gitlab-pat"        "$GITLAB_PAT"    "GitLab root PAT (auto-issued)"

# I. Jenkinsfile credentials 참조 치환 (Credentials 주입 후)
patch_jenkinsfile_gitlab_credentials

# A (재실행). Jenkins 4개 Job 등록
log "Jenkins 4개 Pipeline Job 등록..."
jenkins_create_pipeline_job "DSCORE-TTC-코드-사전학습"                   "$JENKINSFILE_DIR/DSCORE-TTC 코드 사전학습.jenkinsPipeline" || true
jenkins_create_pipeline_job "DSCORE-TTC-코드-정적분석"                   "$JENKINSFILE_DIR/DSCORE-TTC 코드 정적분석.jenkinsPipeline" || true
jenkins_create_pipeline_job "DSCORE-TTC-정적분석-결과분석-이슈등록"      "$JENKINSFILE_DIR/DSCORE-TTC 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline" || true
jenkins_create_pipeline_job "DSCORE-TTC-AI평가"                          "$JENKINSFILE_DIR/DSCORE-TTC AI평가.jenkinsPipeline" || true

log "=========================================="
log "자동 프로비저닝 완료."
log "  Jenkins    : $JENKINS_URL ($JENKINS_USER / $JENKINS_PASSWORD)"
log "  Dify       : $DIFY_URL ($DIFY_ADMIN_EMAIL / $DIFY_ADMIN_PASSWORD)"
log "  SonarQube  : http://localhost:29000 (admin / admin, 최초 로그인 시 변경)"
log "  GitLab     : http://localhost:28090 (root / $GITLAB_ROOT_PASSWORD)"
log "  Ollama     : $OLLAMA_BASE_URL (호스트)"
log ""
log "수동 확인: SonarQube 토큰 발급 후 Jenkins 'sonarqube-token' Credential 등록"
log "  (추후 Phase 에서 자동화 예정)"
log "=========================================="
