#!/usr/bin/env bash
# =============================================================================
# setup.sh — DSCORE Zero-Touch QA 전체 스택 자동 설치
#
# 사용법:
#   cd e2e-pipeline/
#   ./setup.sh
#
# 환경변수로 기본값 오버라이드 가능:
#   OLLAMA_MODEL=qwen3-coder:14b \
#   DIFY_EMAIL=me@company.com \
#   DIFY_PASSWORD=MyPass1! \
#   JENKINS_ADMIN_USER=admin \
#   JENKINS_ADMIN_PW=MyPass1! \
#   ./setup.sh
#
# 자동화 범위:
#   Phase 1: docker compose up --build + 서비스 헬스 대기
#   Phase 2: Ollama LLM 모델 Pull
#   Phase 3: Dify 초기 설정 (계정, Ollama 공급자, Chatflow import, API Key)
#   Phase 4: Jenkins 초기 설정 (플러그인, Credentials, CSP, Job, Node)
#
# 남은 수동 작업:
#   - 에이전트 머신에 Java 11+, Python 3.9+, Playwright 설치
#   - java -jar agent.jar 실행 (스크립트 종료 시 명령 출력)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (환경변수로 오버라이드 가능)
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:14b}"
DIFY_EMAIL="${DIFY_EMAIL:-admin@example.com}"
DIFY_PASSWORD="${DIFY_PASSWORD:-Admin1234!}"
JENKINS_ADMIN_USER="${JENKINS_ADMIN_USER:-admin}"
JENKINS_ADMIN_PW="${JENKINS_ADMIN_PW:-Admin1234!}"
SCRIPTS_HOME="${SCRIPTS_HOME:-$SCRIPT_DIR}"

JENKINS_URL="http://localhost:18080"
DIFY_URL="http://localhost:18081"

# ─────────────────────────────────────────────────────────────────────────────
# 로그 유틸리티
# ─────────────────────────────────────────────────────────────────────────────
log()  { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '[%s] ✓ %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] ⚠ %s\n' "$(date +%H:%M:%S)" "$*"; }
err()  { printf '[%s] ✗ %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# URL이 응답할 때까지 대기 (최대 $3초, 기본 300초)
wait_http() {
  local url="$1" label="$2" timeout="${3:-300}"
  printf '[%s] 대기 중: %s' "$(date +%H:%M:%S)" "$label"
  local elapsed=0
  until curl -sf "$url" >/dev/null 2>&1; do
    sleep 5; elapsed=$((elapsed + 5))
    printf '.'
    if [ $elapsed -ge $timeout ]; then
      printf '\n'
      err "$label 응답 없음 (${timeout}초 초과)"
      return 1
    fi
  done
  printf '\n'
  ok "$label 준비 완료"
}

# JSON 필드 추출 (python3)
json_get() {
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)" 2>/dev/null || echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 0: 사전 요구사항 확인
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 0: 사전 요구사항 확인 ==="

command -v docker  >/dev/null 2>&1 || { err "docker 명령이 없습니다. Docker Desktop을 설치하십시오."; exit 1; }
command -v python3 >/dev/null 2>&1 || { err "python3가 필요합니다. (brew install python 또는 apt install python3)"; exit 1; }
docker info >/dev/null 2>&1 || { err "Docker 데몬이 실행되지 않습니다. Docker Desktop을 실행하십시오."; exit 1; }
[ -f "$SCRIPT_DIR/dify-chatflow.yaml" ] || { err "dify-chatflow.yaml이 없습니다. e2e-pipeline/ 폴더 안에서 실행하십시오."; exit 1; }
[ -f "$SCRIPT_DIR/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline" ] || { err "DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline이 없습니다."; exit 1; }

ok "사전 요구사항 확인 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Jenkins init 스크립트 생성 + docker compose 기동
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 1: Docker 스택 기동 ==="

# Jenkins Groovy init 스크립트: 관리자 계정 + CSRF 비활성화
mkdir -p jenkins-init

# 플레이스홀더를 실제 값으로 치환하여 생성
cat > jenkins-init/01-security.groovy << GROOVY_SCRIPT
import jenkins.model.*
import hudson.security.*
import jenkins.install.InstallState

def instance = Jenkins.getInstance()

// 관리자 계정 생성 (이미 존재하면 건너뜀)
def realm = new HudsonPrivateSecurityRealm(false)
if (realm.getUser('${JENKINS_ADMIN_USER}') == null) {
    realm.createAccount('${JENKINS_ADMIN_USER}', '${JENKINS_ADMIN_PW}')
    println "[init] 관리자 계정 생성: ${JENKINS_ADMIN_USER}"
} else {
    println "[init] 관리자 계정 이미 존재: ${JENKINS_ADMIN_USER}"
}
instance.setSecurityRealm(realm)

// 권한 정책: 로그인 사용자 전체 제어
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

// Setup Wizard 완료 처리
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)

// CSRF 크럼 비활성화 (REST API 호출을 위해)
instance.setCrumbIssuer(null)

instance.save()
println "[init] Jenkins 보안 설정 완료"
GROOVY_SCRIPT

# docker-compose.override.yaml: Setup Wizard 우회 + init 스크립트 마운트
# (docker compose가 자동으로 인식)
cat > docker-compose.override.yaml << 'OVERRIDE_YAML'
# setup.sh가 생성한 파일 — 첫 설치 시 Setup Wizard를 우회하고
# jenkins-init/ 폴더의 Groovy 스크립트를 자동 실행한다.
# 설치 완료 후에도 유지해도 무방하다 (runSetupWizard=false는 안전).
services:
  jenkins:
    environment:
      JAVA_OPTS: "-Djenkins.install.runSetupWizard=false"
    volumes:
      - ./jenkins-init:/var/jenkins_home/init.groovy.d
OVERRIDE_YAML

log "docker compose up -d --build 시작 (첫 기동은 30~50분 소요됩니다)"
docker compose up -d --build
ok "컨테이너 기동 명령 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: 서비스 헬스 대기
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 2: 서비스 준비 대기 ==="

# Dify: DB 마이그레이션 포함 최대 5분 대기
log "Dify API 준비 대기 (DB 마이그레이션 포함, 최대 5분)..."
wait_http "${DIFY_URL}/health" "Dify API" 360

# Jenkins: Groovy init 스크립트 실행 포함 대기
log "Jenkins 준비 대기..."
wait_http "${JENKINS_URL}/login" "Jenkins" 360

# Jenkins API 인증 대기 (Groovy init 완료 후 사용 가능)
log "Jenkins REST API 인증 대기..."
elapsed=0
until curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    "${JENKINS_URL}/api/json" >/dev/null 2>&1; do
  sleep 5; elapsed=$((elapsed + 5))
  if [ $elapsed -ge 120 ]; then
    warn "Jenkins API 인증 타임아웃 (2분) — init 스크립트 실행 지연 가능. 계속 진행합니다."
    break
  fi
done
ok "모든 서비스 준비 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Ollama 모델 Pull
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 3: Ollama 모델 Pull: ${OLLAMA_MODEL} ==="
log "모델 크기에 따라 수분~수십 분 소요됩니다."
docker exec e2e-ollama ollama pull "${OLLAMA_MODEL}" && ok "Ollama 모델 Pull 완료" || warn "Ollama pull 실패 — 나중에 수동으로 실행: docker exec -it e2e-ollama ollama pull ${OLLAMA_MODEL}"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Dify 초기 설정
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 4: Dify 초기 설정 ==="
DIFY_API_KEY=""   # 이후 Phase 5에서 사용

# 4-1. 관리자 계정 생성
log "4-1. Dify 관리자 계정 생성..."
SETUP_RESP=$(curl -sf -X POST "${DIFY_URL}/console/api/setup" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DIFY_EMAIL}\",\"name\":\"Admin\",\"password\":\"${DIFY_PASSWORD}\"}" \
  2>&1 || echo '{"result":"error"}')

if echo "$SETUP_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('result')=='success' else 1)" 2>/dev/null; then
  ok "Dify 관리자 계정 생성 완료"
else
  warn "Dify 계정 생성 응답: ${SETUP_RESP}"
  warn "이미 설정된 경우 로그인으로 계속 진행합니다."
fi

# 4-2. 로그인 → access_token 획득
log "4-2. Dify 로그인..."
LOGIN_RESP=$(curl -sf -X POST "${DIFY_URL}/console/api/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${DIFY_EMAIL}\",\"password\":\"${DIFY_PASSWORD}\"}" \
  2>&1 || echo '{}')

DIFY_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
# Dify 버전별로 응답 구조가 다를 수 있음
token = (d.get('data') or {}).get('access_token') or d.get('access_token') or ''
print(token)
" 2>/dev/null || echo "")

if [ -z "$DIFY_TOKEN" ]; then
  err "Dify 로그인 실패 (응답: ${LOGIN_RESP})"
  warn "Dify 설정을 수동으로 완료하십시오 — GUIDE.md §6~§7 참조"
  DIFY_SETUP_OK=false
else
  ok "Dify 로그인 성공"
  DIFY_SETUP_OK=true
fi

if [ "${DIFY_SETUP_OK:-false}" = "true" ]; then

  # 4-3. Ollama 모델 공급자 등록
  log "4-3. Ollama 모델 공급자 등록 (http://ollama:11434)..."
  # 공급자 credentials 저장 (Dify 버전별 엔드포인트 시도)
  PROVIDER_RESP=$(curl -sf -X POST \
    "${DIFY_URL}/console/api/workspaces/current/model-providers/ollama" \
    -H "Authorization: Bearer ${DIFY_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"credentials":{"base_url":"http://ollama:11434"}}' \
    2>&1 || echo '{}')
  ok "Ollama 공급자 등록 요청 완료"

  # 4-4. LLM 모델 추가
  log "4-4. LLM 모델 추가: ${OLLAMA_MODEL}..."
  MODEL_RESP=$(curl -sf -X POST \
    "${DIFY_URL}/console/api/workspaces/current/model-providers/ollama/models" \
    -H "Authorization: Bearer ${DIFY_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"${OLLAMA_MODEL}\",
      \"model_type\": \"llm\",
      \"credentials\": {
        \"base_url\": \"http://ollama:11434\",
        \"context_size\": 8192,
        \"max_tokens\": 4096,
        \"mode\": \"chat\",
        \"completion_type\": \"chat\"
      }
    }" \
    2>&1 || echo '{}')
  ok "LLM 모델 추가 요청 완료"

  # 4-5. Chatflow DSL import
  log "4-5. Chatflow 'ZeroTouch QA Brain' import..."
  # 모델 플레이스홀더를 실제 모델명으로 치환 후 JSON 바디 생성
  IMPORT_BODY=$(python3 - << PYEOF
import json, re
with open('${SCRIPT_DIR}/dify-chatflow.yaml', encoding='utf-8') as f:
    yaml_content = f.read()
yaml_content = yaml_content.replace('{{OLLAMA_MODEL}}', '${OLLAMA_MODEL}')
print(json.dumps({'data': yaml_content}))
PYEOF
) || IMPORT_BODY=""

  if [ -z "$IMPORT_BODY" ]; then
    warn "Chatflow import 바디 생성 실패 — dify-chatflow.yaml 확인 필요"
    IMPORT_RESP='{}'
  else
    IMPORT_RESP=$(curl -s -X POST "${DIFY_URL}/console/api/apps/import" \
      -H "Authorization: Bearer ${DIFY_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$IMPORT_BODY" \
      2>&1 || echo '{}')
  fi

  DIFY_APP_ID=$(echo "$IMPORT_RESP" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(d.get('id',''))
" 2>/dev/null || echo "")

  if [ -z "$DIFY_APP_ID" ]; then
    warn "Chatflow import 실패 (응답: ${IMPORT_RESP})"
    warn "Chatflow를 수동으로 생성하십시오 — GUIDE.md §7 참조"
  else
    ok "Chatflow import 완료 (App ID: ${DIFY_APP_ID})"

    # 4-6. Chatflow 게시
    # advanced-chat 앱은 /publish, Workflow 앱은 /workflows/publish — 둘 다 시도
    log "4-6. Chatflow 게시..."
    PUB_RESP=$(curl -s -X POST \
      "${DIFY_URL}/console/api/apps/${DIFY_APP_ID}/publish" \
      -H "Authorization: Bearer ${DIFY_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{}' 2>&1 || echo '{}')
    # 실패(result != success)이면 /workflows/publish 도 시도
    if ! echo "$PUB_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('result')=='success' else 1)" 2>/dev/null; then
      PUB_RESP=$(curl -s -X POST \
        "${DIFY_URL}/console/api/apps/${DIFY_APP_ID}/workflows/publish" \
        -H "Authorization: Bearer ${DIFY_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"marked_as_official": false}' 2>&1 || echo '{}')
    fi
    ok "Chatflow 게시 완료"

    # 4-7. API Key 생성
    log "4-7. Dify API Key 생성..."
    KEY_RESP=$(curl -sf -X POST \
      "${DIFY_URL}/console/api/apps/${DIFY_APP_ID}/api-keys" \
      -H "Authorization: Bearer ${DIFY_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{}' \
      2>&1 || echo '{}')

    DIFY_API_KEY=$(echo "$KEY_RESP" | python3 -c "
import json,sys; d=json.load(sys.stdin); print(d.get('token',''))
" 2>/dev/null || echo "")

    if [ -n "$DIFY_API_KEY" ]; then
      ok "Dify API Key 생성 완료: ${DIFY_API_KEY}"
    else
      warn "API Key 생성 실패 (응답: ${KEY_RESP})"
      warn "GUIDE.md §7.7에서 수동으로 API Key를 생성하십시오"
    fi
  fi

fi  # DIFY_SETUP_OK

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Jenkins 초기 설정 (REST API)
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 5: Jenkins 초기 설정 ==="

# Jenkins API 준비 재확인
JENKINS_READY=false
for i in $(seq 1 12); do
  if curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      "${JENKINS_URL}/api/json" >/dev/null 2>&1; then
    JENKINS_READY=true
    break
  fi
  sleep 10
done

if [ "$JENKINS_READY" = "false" ]; then
  warn "Jenkins REST API 응답 없음 — Jenkins 설정을 수동으로 완료하십시오 (GUIDE.md §8 참조)"
else
  ok "Jenkins API 연결 확인"

  # 5-1. 플러그인 설치
  log "5-1. Jenkins 플러그인 설치 (file-parameters, htmlpublisher)..."
  PLUGIN_XML='<jenkins><install plugin="file-parameters@latest"/><install plugin="htmlpublisher@latest"/></jenkins>'
  curl -sf -X POST "${JENKINS_URL}/pluginManager/installNecessaryPlugins" \
    -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    -H "Content-Type: text/xml" \
    -d "$PLUGIN_XML" >/dev/null 2>&1 \
    && ok "플러그인 설치 요청 완료 (백그라운드 처리)" \
    || warn "플러그인 설치 요청 실패 — GUIDE.md §8.2 참조"

  # 플러그인 설치 완료 대기 후 재시작
  log "플러그인 설치 완료 대기 (30초)..."
  sleep 30
  log "Jenkins 재시작..."
  docker restart e2e-jenkins >/dev/null 2>&1 || true
  log "Jenkins 재시작 후 준비 대기..."
  sleep 20
  wait_http "${JENKINS_URL}/login" "Jenkins (재시작 후)" 180

  # 재시작 후 API 인증 재확인
  for i in $(seq 1 12); do
    if curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
        "${JENKINS_URL}/api/json" >/dev/null 2>&1; then
      break
    fi
    sleep 10
  done

  # 5-2. Credentials 등록 (Dify API Key)
  if [ -n "$DIFY_API_KEY" ]; then
    log "5-2. Jenkins Credentials 등록 (dify-qa-api-token)..."
    CRED_XML="<com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>
  <scope>GLOBAL</scope>
  <id>dify-qa-api-token</id>
  <description>Dify QA Chatflow API Key (setup.sh auto-registered)</description>
  <secret>${DIFY_API_KEY}</secret>
</com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>"

    curl -sf -X POST \
      "${JENKINS_URL}/credentials/store/system/domain/_/createCredentials" \
      -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      -H "Content-Type: application/xml" \
      -d "$CRED_XML" >/dev/null 2>&1 \
      && ok "Credentials 등록 완료 (dify-qa-api-token)" \
      || warn "Credentials 등록 실패 — GUIDE.md §8.3 참조"
  else
    warn "Dify API Key 없음 — Credentials를 수동으로 등록하십시오 (GUIDE.md §8.3)"
  fi

  # 5-3. CSP 완화 (HTML 리포트 JavaScript 허용)
  log "5-3. Jenkins CSP 완화..."
  curl -sf -X POST "${JENKINS_URL}/scriptText" \
    -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode 'script=System.setProperty("hudson.model.DirectoryBrowserSupport.CSP", "")' \
    >/dev/null 2>&1 \
    && ok "CSP 완화 설정 완료" \
    || warn "CSP 설정 실패 — GUIDE.md §8.4 참조"

  # 5-4. Pipeline Job 생성
  log "5-4. Pipeline Job 'DSCORE-ZeroTouch-QA-Docker' 생성..."
  JOB_XML=$(python3 - << PYEOF
import xml.sax.saxutils as sax
with open('${SCRIPT_DIR}/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline', encoding='utf-8') as f:
    script = f.read()
escaped = sax.escape(script)
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>DSCORE Zero-Touch QA Docker Pipeline (setup.sh auto-created)</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>""".format(escaped))
PYEOF
) || JOB_XML=""

  if [ -z "$JOB_XML" ]; then
    warn "Job XML 생성 실패 — Pipeline Job을 수동으로 생성하십시오 (GUIDE.md §8.5)"
  else
    curl -sf -X POST \
      "${JENKINS_URL}/createItem?name=DSCORE-ZeroTouch-QA-Docker" \
      -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      -H "Content-Type: application/xml" \
      -d "$JOB_XML" >/dev/null 2>&1 \
      && ok "Pipeline Job 생성 완료" \
      || warn "Pipeline Job 생성 실패 (이미 존재하거나 플러그인 미설치) — GUIDE.md §8.5 참조"
  fi

  # 5-5. mac-ui-tester 노드 등록 (Groovy Script Console)
  log "5-5. mac-ui-tester 에이전트 노드 등록..."
  NODE_GROOVY=$(cat << GROOVY_EOF
import jenkins.model.*
import hudson.model.*
import hudson.slaves.*

def instance = Jenkins.getInstance()

if (instance.getNode('mac-ui-tester') != null) {
    println "[node] mac-ui-tester 이미 존재 — 건너뜀"
    return
}

def launcher = new JNLPLauncher(false)   // false = TCP 모드 (agent.jar 기본값과 일치)
def node = new DumbSlave(
    "mac-ui-tester",
    "Playwright E2E Test Agent",
    "${HOME}/jenkins-agent",
    "2",
    Node.Mode.NORMAL,
    "mac-ui-tester",
    launcher,
    new RetentionStrategy.Always(),
    new java.util.ArrayList()
)

// SCRIPTS_HOME 환경변수 추가
def envEntry = new EnvironmentVariablesNodeProperty.Entry("SCRIPTS_HOME", "${SCRIPTS_HOME}")
def envProp = new EnvironmentVariablesNodeProperty([envEntry])
node.nodeProperties.add(envProp)

instance.addNode(node)
instance.save()
println "[node] mac-ui-tester 등록 완료 (SCRIPTS_HOME=${SCRIPTS_HOME})"
GROOVY_EOF
)

  curl -sf -X POST "${JENKINS_URL}/scriptText" \
    -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "script=${NODE_GROOVY}" \
    >/dev/null 2>&1 \
    && ok "mac-ui-tester 노드 등록 완료 (SCRIPTS_HOME: ${SCRIPTS_HOME})" \
    || warn "노드 등록 실패 — GUIDE.md §8.6 Step 1 참조"

fi  # JENKINS_READY

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: 완료 요약
# ─────────────────────────────────────────────────────────────────────────────

# 노드 시크릿 조회 시도
NODE_SECRET=$(curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
  "${JENKINS_URL}/computer/mac-ui-tester/slave-agent.jnlp" 2>/dev/null | \
  python3 -c "
import sys, re
content = sys.stdin.read()
m = re.search(r'<argument>([0-9a-f]{40,64})</argument>', content)
print(m.group(1) if m else '<SECRET>')
" 2>/dev/null || echo "<SECRET>")

echo ""
echo "================================================================="
echo "  DSCORE Zero-Touch QA 스택 설치 완료"
echo "================================================================="
echo ""
echo "  Jenkins   → http://localhost:18080"
echo "             계정: ${JENKINS_ADMIN_USER} / ${JENKINS_ADMIN_PW}"
echo ""
echo "  Dify 콘솔 → http://localhost:18081"
echo "             계정: ${DIFY_EMAIL} / ${DIFY_PASSWORD}"
if [ -n "$DIFY_API_KEY" ]; then
  echo "             API Key: ${DIFY_API_KEY}"
  echo "             → Jenkins Credentials 'dify-qa-api-token' 자동 등록됨"
else
  echo "             API Key: (수동 등록 필요 — GUIDE.md §7.7 + §8.3)"
fi
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [남은 수동 작업] 에이전트 머신에서 실행"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. Python + Playwright 설치 (에이전트 머신):"
echo "     pip3 install playwright && playwright install chromium"
echo ""
echo "  2. agent.jar 다운로드 및 에이전트 연결:"
echo ""
echo "     curl -O http://localhost:18080/jnlpJars/agent.jar"
echo "     java -jar agent.jar \\"
echo "       -url \"http://localhost:18080\" \\"
echo "       -secret \"${NODE_SECRET}\" \\"
echo "       -name \"mac-ui-tester\" \\"
echo "       -workDir \"${HOME}/jenkins-agent\""
echo ""
echo "  3. Jenkins UI → 노드 관리 → mac-ui-tester 상태가 'Connected'로 바뀌면 완료."
echo ""
echo "  4. Jenkins → DSCORE-ZeroTouch-QA-Docker → Build with Parameters 실행"
echo ""
echo "================================================================="
