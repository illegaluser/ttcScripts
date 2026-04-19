#!/usr/bin/env bash
# ============================================================================
# provision-apps.sh — All-in-One 컨테이너 내부 전용 앱 프로비저닝
#
# 이 스크립트는 오프라인 단일 이미지(dscore.ttc.playwright:latest) 의 entrypoint 에서
# 최초 1회 호출된다. 기존 setup.sh 의 Phase 4 (Dify REST 설정) 와 Phase 5
# (Jenkins REST Credentials/Job/Node) 에 해당하는 동작을 수행하되, 다음을 전제한다:
#
#   1) Docker 스택(=supervisord) 이 이미 컨테이너 내부에서 기동 중이다.
#   2) Ollama 모델, Jenkins 플러그인(hpi), Dify 플러그인(.difypkg) 이 빌드 시점에
#      이미 이미지에 seed 돼 있다 → marketplace / Update Center 호출 불필요.
#   3) 모든 upstream 은 127.0.0.1 (동일 네트워크 네임스페이스).
#
# 기존 setup.sh 와 분리된 이유:
#   - setup.sh 는 온라인 macOS 개발자 경험(Phase 0/1/3/6/7 포함)의 검증된 단일
#     엔트리포인트다. 여기에 오프라인 분기를 삽입하면 온라인 흐름을 해칠 위험이
#     있다 → 오프라인 전용 로직을 이 파일로 완전히 분리한다.
#
# 환경변수 (entrypoint-allinone.sh 가 세팅):
#   DIFY_URL                     http://127.0.0.1:18081
#   JENKINS_URL                  http://127.0.0.1:18080
#   DIFY_EMAIL                   Dify 관리자 이메일
#   DIFY_PASSWORD                Dify 관리자 비밀번호
#   JENKINS_ADMIN_USER           Jenkins 관리자 ID
#   JENKINS_ADMIN_PW             Jenkins 관리자 비밀번호
#   OFFLINE_DIFY_PLUGIN_DIR      /opt/seed/dify-plugins (langgenius-ollama-*.difypkg)
#   OFFLINE_DIFY_CHATFLOW_YAML   /opt/dify-chatflow.yaml
#   OFFLINE_JENKINS_PIPELINE     /opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
#   OLLAMA_MODEL                 gemma4:e4b
#   DEBUG=1                      상세 출력
# ============================================================================
set -euo pipefail

# ── 설정 기본값 ────────────────────────────────────────────────────────────
DIFY_URL="${DIFY_URL:-http://127.0.0.1:18081}"
JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:18080}"
DIFY_EMAIL="${DIFY_EMAIL:-admin@example.com}"
DIFY_PASSWORD="${DIFY_PASSWORD:-Admin1234!}"
JENKINS_ADMIN_USER="${JENKINS_ADMIN_USER:-admin}"
JENKINS_ADMIN_PW="${JENKINS_ADMIN_PW:-password}"
OFFLINE_DIFY_PLUGIN_DIR="${OFFLINE_DIFY_PLUGIN_DIR:-/opt/seed/dify-plugins}"
OFFLINE_DIFY_CHATFLOW_YAML="${OFFLINE_DIFY_CHATFLOW_YAML:-/opt/dify-chatflow.yaml}"
OFFLINE_JENKINS_PIPELINE="${OFFLINE_JENKINS_PIPELINE:-/opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
DEBUG="${DEBUG:-0}"

# Ollama 모델 등록 시 Dify plugin 이 사용할 기본 컨텍스트/토큰 크기
OLLAMA_CONTEXT_SIZE="${OLLAMA_CONTEXT_SIZE:-8192}"
OLLAMA_MAX_TOKENS="${OLLAMA_MAX_TOKENS:-4096}"

DIFY_COOKIES="${DIFY_COOKIES:-/tmp/dify-cookies.txt}"
DIFY_CSRF_TOKEN=""
DIFY_LOGGED_IN="false"
DIFY_OLLAMA_INSTALLED="false"
DIFY_API_KEY=""

# 스크립트 전용 Python — Debian apt python3 (3.13) 을 고정 사용.
# /usr/local/bin/python3 는 dify-api 전용 python3.12 (pyyaml 등 pip 설치 안 됨) 이므로
# `python3` 을 쓰면 import 실패. 시스템 python 을 명시해 의존성(pyyaml/jsonschema 등) 보장.
PY=/usr/bin/python3

# ── 로깅 유틸 ──────────────────────────────────────────────────────────────
_ts()   { date +%H:%M:%S; }
log()   { printf '[%s] [·] %s\n' "$(_ts)" "$*"; }
step()  { printf '\n[%s] [▶] %s\n' "$(_ts)" "$*"; }
ok()    { printf '[%s] [✓] %s\n' "$(_ts)" "$*"; }
warn()  { printf '[%s] [⚠] %s\n' "$(_ts)" "$*" >&2; }
err()   { printf '[%s] [✗] %s\n' "$(_ts)" "$*" >&2; }
debug() { [ "$DEBUG" = "1" ] && printf '[%s] [D] %s\n' "$(_ts)" "$*"; return 0; }

# ── HTTP 준비 대기 ─────────────────────────────────────────────────────────
wait_http_status() {
  local url="$1" expected="$2" label="$3" timeout="${4:-120}"
  local waited=0
  log "  $label 대기 (최대 ${timeout}s): $url"
  while [ $waited -lt "$timeout" ]; do
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
    if [ "$code" = "$expected" ]; then
      ok "  $label 응답 OK ($code, ${waited}s)"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  warn "  $label 타임아웃 (${timeout}s)"
  return 1
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 2 축약: 서비스 헬스체크
# ────────────────────────────────────────────────────────────────────────────
step "=== 1. 서비스 헬스체크 ==="
rm -f "$DIFY_COOKIES"

wait_http_status "${DIFY_URL}/install"               "200" "Dify 웹 (/install)"               240 || true
wait_http_status "${DIFY_URL}/console/api/setup"     "200" "Dify API (/console/api/setup)"    420 || true
wait_http_status "${JENKINS_URL}/login"              "200" "Jenkins (/login)"                  360 || true

# Jenkins REST 인증 대기 (Groovy init 완료 후)
log "Jenkins REST API 인증 대기..."
_je=0
until curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    "${JENKINS_URL}/api/json" >/dev/null 2>&1; do
  sleep 5; _je=$((_je + 5))
  [ $_je -ge 180 ] && { warn "Jenkins REST 인증 타임아웃 — 계속 진행"; break; }
done
[ $_je -lt 180 ] && ok "Jenkins REST 인증 확인 (${_je}초)"

# ────────────────────────────────────────────────────────────────────────────
# Jenkins CSRF crumb 준비 — Jenkins 2.479+ 는 POST 에 항상 crumb 필요.
# /crumbIssuer 로 한 번 받아서 이후 모든 curl 에 동일 쿠키 jar + header 로 붙인다.
# ────────────────────────────────────────────────────────────────────────────
JENKINS_COOKIES="/tmp/jenkins-cookies.txt"
JENKINS_CRUMB_HEADER=""
rm -f "$JENKINS_COOKIES"

# PoC 2026-04-20: 이전 버전은 /crumbIssuer/api/xml?xpath=... 로 받아 ':' 포함 여부만
# 체크했는데, Jenkins 2.555 이 인증/세션 상태에 따라 200 + HTML 페이지를 내려주는
# 경우가 관찰됐다. HTML 에는 ':' 이 흔하므로 조건을 통과하고 쓰레기 HTML 전체가
# crumb 헤더로 주입 → 이후 모든 POST 가 403 (HTML 로그인 페이지) 반환 → Pipeline
# Job/Credentials/Node 생성이 전부 실패하는 연쇄가 발생했다.
# 해결: JSON 엔드포인트로 받아 파이썬 파서로 명시적 검증. 파싱 실패 시 empty 로 두면
# ${JENKINS_CRUMB_HEADER:+-H ...} 패턴이 자동으로 헤더를 빼기 때문에 basic auth 만으로
# POST 가 진행된다 (Jenkins 2.555 은 basic auth 요청에 대해 crumb 를 요구하지 않음).
JENKINS_CRUMB_JSON=$(curl -sS -c "$JENKINS_COOKIES" \
    -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    -H "Accept: application/json" \
    "${JENKINS_URL}/crumbIssuer/api/json" \
    2>/dev/null || echo "")
JENKINS_CRUMB_HEADER=$(echo "$JENKINS_CRUMB_JSON" | $PY -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    f = d.get('crumbRequestField','')
    c = d.get('crumb','')
    if f and c and all(ch.isalnum() or ch == '-' for ch in f):
        print(f'{f}:{c}')
    else:
        print('')
except Exception:
    print('')
")
if [ -n "$JENKINS_CRUMB_HEADER" ]; then
  ok "Jenkins crumb 획득: ${JENKINS_CRUMB_HEADER%%:*} (${#JENKINS_CRUMB_HEADER}자)"
else
  warn "Jenkins crumb 파싱 실패 — basic auth 로만 진행 (Jenkins 2.555+ 는 crumb 없이도 POST 허용)"
fi

# Jenkins POST 요청용 헬퍼 — -u 인증 + crumb 헤더 + 쿠키 jar 자동 부착
jkpost() {
  curl -sS -b "$JENKINS_COOKIES" -c "$JENKINS_COOKIES" \
       -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
       ${JENKINS_CRUMB_HEADER:+-H "$JENKINS_CRUMB_HEADER"} \
       "$@"
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 4: Dify 초기 설정
# ────────────────────────────────────────────────────────────────────────────
step "=== 2. Dify 초기 설정 ==="

# 2-1. 관리자 계정 생성 (이미 있으면 실패는 무시)
log "2-1. 관리자 계정 생성 — POST /console/api/setup"
SETUP_RESP=$(curl -sS -X POST "${DIFY_URL}/console/api/setup" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${DIFY_EMAIL}\",\"name\":\"Admin\",\"password\":\"${DIFY_PASSWORD}\"}" \
    2>&1 || echo '{"result":"error"}')
debug "setup response: $SETUP_RESP"
if echo "$SETUP_RESP" | $PY -c "import json,sys;d=json.load(sys.stdin);exit(0 if d.get('result')=='success' else 1)" 2>/dev/null; then
  ok "관리자 계정 생성 완료: ${DIFY_EMAIL}"
else
  warn "계정 생성 응답: ${SETUP_RESP}"
  warn "  → 이미 설정된 상태일 가능성. 로그인으로 계속 진행."
fi

# 2-2. 로그인 (password base64 인코딩 필수: @decrypt_password_field 데코레이터)
log "2-2. 로그인 — POST /console/api/login (password base64 인코딩 + 쿠키 저장)"
DIFY_PASSWORD_B64=$(printf '%s' "$DIFY_PASSWORD" | $PY -c 'import base64,sys;print(base64.b64encode(sys.stdin.buffer.read()).decode())')

LOGIN_RESP=$(curl -sS -c "$DIFY_COOKIES" \
    -X POST "${DIFY_URL}/console/api/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${DIFY_EMAIL}\",\"password\":\"${DIFY_PASSWORD_B64}\",\"remember_me\":true}" \
    2>&1 || echo '{}')
debug "login response: $LOGIN_RESP"

LOGIN_RESULT=$(echo "$LOGIN_RESP" | $PY -c "
import json,sys
try: d=json.load(sys.stdin); print(d.get('result',''))
except Exception: print('')
" 2>/dev/null || echo "")

if [ "$LOGIN_RESULT" = "success" ] && [ -s "$DIFY_COOKIES" ] && grep -q 'access_token' "$DIFY_COOKIES"; then
  DIFY_CSRF_TOKEN=$(awk '$6 == "csrf_token" || $6 == "__Host-csrf_token" { print $7; exit }' "$DIFY_COOKIES" 2>/dev/null || echo "")
  if [ -n "$DIFY_CSRF_TOKEN" ]; then
    ok "로그인 성공 + CSRF 토큰 추출 완료"
    DIFY_LOGGED_IN=true
  else
    warn "CSRF 토큰 추출 실패"
  fi
else
  err "로그인 실패 — 응답: ${LOGIN_RESP}"
fi

# 2-3a. Ollama 플러그인 설치 (오프라인: 로컬 .difypkg 업로드)
if [ "$DIFY_LOGGED_IN" = "true" ]; then
  log "2-3a. Ollama 플러그인 설치 상태 확인"
  PLUGIN_LIST_RESP=$(curl -sS -b "$DIFY_COOKIES" \
      "${DIFY_URL}/console/api/workspaces/current/plugin/list" \
      -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" 2>&1 || echo '{}')
  if echo "$PLUGIN_LIST_RESP" | $PY -c "
import json,sys
try:
    d=json.load(sys.stdin)
    sys.exit(0 if any(p.get('plugin_id')=='langgenius/ollama' for p in d.get('plugins',[])) else 1)
except Exception: sys.exit(1)
" 2>/dev/null; then
    ok "  Ollama 플러그인 이미 설치됨"
    DIFY_OLLAMA_INSTALLED="true"
  else
    log "  [offline] 로컬 .difypkg 업로드 시도"
    OFFLINE_PKG=$(ls "$OFFLINE_DIFY_PLUGIN_DIR"/langgenius-ollama-*.difypkg 2>/dev/null | head -n1 || true)
    if [ -z "$OFFLINE_PKG" ] || [ ! -f "$OFFLINE_PKG" ]; then
      err "  $OFFLINE_DIFY_PLUGIN_DIR 에 langgenius-ollama-*.difypkg 없음"
    else
      log "  패키지: $(basename "$OFFLINE_PKG")"
      # PoC 검증됨 (2026-04-19):
      #   실제 엔드포인트는 /plugin/upload/pkg (POST multipart, field 'pkg') →
      #   응답 {unique_identifier, manifest, verification}
      #   참조: api/controllers/console/workspace/plugin.py @console_ns.route
      UPLOAD_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/workspaces/current/plugin/upload/pkg" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
          -F "pkg=@${OFFLINE_PKG}" 2>&1 || echo "HTTP:000")
      debug "upload response: $UPLOAD_RESP"
      if echo "$UPLOAD_RESP" | grep -qE 'HTTP:(200|201|202)'; then
        UNIQUE_ID=$(echo "$UPLOAD_RESP" | $PY -c "
import json,sys,re
body = re.split(r'\\nHTTP:', sys.stdin.read())[0]
try:
    d = json.loads(body)
    print(d.get('unique_identifier',''))
except Exception: print('')
" 2>/dev/null || echo "")
        if [ -n "$UNIQUE_ID" ]; then
          log "  pkg 업로드 완료. 설치 트리거: ${UNIQUE_ID:0:60}..."
          curl -sS -b "$DIFY_COOKIES" \
              -X POST "${DIFY_URL}/console/api/workspaces/current/plugin/install/pkg" \
              -H "Content-Type: application/json" \
              -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
              -d "{\"plugin_unique_identifiers\":[\"${UNIQUE_ID}\"]}" >/dev/null 2>&1 || true
          # 폴링
          _waited=0
          while [ $_waited -lt 120 ]; do
            sleep 5; _waited=$((_waited + 5))
            CHECK_RESP=$(curl -sS -b "$DIFY_COOKIES" \
                "${DIFY_URL}/console/api/workspaces/current/plugin/list" \
                -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" 2>&1 || echo '{}')
            if echo "$CHECK_RESP" | $PY -c "
import json,sys
try:
    d=json.load(sys.stdin)
    sys.exit(0 if any(p.get('plugin_id')=='langgenius/ollama' for p in d.get('plugins',[])) else 1)
except Exception: sys.exit(1)
" 2>/dev/null; then
              ok "  Ollama 플러그인 설치 완료 (${_waited}s)"
              DIFY_OLLAMA_INSTALLED="true"
              break
            fi
          done
          [ "$DIFY_OLLAMA_INSTALLED" != "true" ] && warn "  120s 내 설치 미완료"
        else
          warn "  unique_identifier 추출 실패"
        fi
      else
        warn "  pkg 업로드 실패: $UPLOAD_RESP"
      fi
    fi
  fi
fi

# 2-3b. Ollama 모델 공급자 등록
#
# Dify 1.13.3 의 credential 저장 구조 (실측 2026-04-19):
#   provider_models.credential_id ─▶ provider_model_credentials.id
#   "Add Model credentials" POST 는 **새 credential 레코드** 추가만 할 뿐
#   provider_models 의 credential_id 포인터를 바꾸지 않는다. 또한 Redis 에
#   provider_model_credentials:tenant_id:...:id:<uuid> 키로 값을 캐시한다.
#   OLLAMA_BASE_URL 을 변경한 뒤 이 두 가지를 직접 처리하지 않으면 Dify 는
#   계속 옛 base_url 로 요청을 보내 ConnectionError 가 난다.
#
# 대응:
#   1) POST credentials 로 새 레코드 등록 (upsert 가 아님 — 항상 추가)
#   2) DB UPDATE 로 provider_models.credential_id 를 가장 최근 레코드로 swap
#   3) Redis FLUSH — provider_model_credentials:* 및 model 다이제스트 캐시 삭제
if [ "$DIFY_OLLAMA_INSTALLED" = "true" ]; then
  log "2-3b. 모델 공급자 등록: ${OLLAMA_MODEL} (base_url=${OLLAMA_BASE_URL})"
  REG_BODY=$($PY - << PYEOF
import json
print(json.dumps({
    "model": "${OLLAMA_MODEL}",
    "model_type": "llm",
    "credentials": {
        "base_url": "${OLLAMA_BASE_URL}",
        "mode": "chat",
        "context_size": "${OLLAMA_CONTEXT_SIZE}",
        "max_tokens": "${OLLAMA_MAX_TOKENS}",
        "vision_support": "false",
        "function_call_support": "false"
    }
}))
PYEOF
)
  REG_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -b "$DIFY_COOKIES" \
      -X POST "${DIFY_URL}/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials" \
      -H "Content-Type: application/json" \
      -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
      -d "$REG_BODY" 2>&1 || echo "HTTP:000")
  debug "model register response: $REG_RESP"
  if echo "$REG_RESP" | grep -qE 'HTTP:(200|201)'; then
    ok "  credentials POST 완료 (새 레코드 추가됨)"
  elif echo "$REG_RESP" | grep -q 'already_exist'; then
    ok "  credentials 이미 동일 구성 존재"
  else
    warn "  credentials POST 이상 응답: $REG_RESP"
  fi

  # 2-3c. credential_id swap: 가장 최근 추가된 host URL 레코드를 활성화
  log "2-3c. credential_id swap (base_url=${OLLAMA_BASE_URL} 레코드 활성화)"
  SWAP_TARGET_PATTERN=$(echo "${OLLAMA_BASE_URL}" | sed "s|'|''|g")
  SWAP_SQL=$(cat <<SQL
UPDATE provider_models pm
   SET credential_id = (
         SELECT id FROM provider_model_credentials
          WHERE provider_name = pm.provider_name
            AND model_name    = pm.model_name
            AND model_type    = pm.model_type
            AND encrypted_config LIKE '%${SWAP_TARGET_PATTERN}%'
          ORDER BY updated_at DESC LIMIT 1
       ),
       updated_at = now()
 WHERE provider_name LIKE '%ollama%'
   AND model_name = '${OLLAMA_MODEL}'
   AND EXISTS (
         SELECT 1 FROM provider_model_credentials pmc
          WHERE pmc.provider_name = pm.provider_name
            AND pmc.model_name    = pm.model_name
            AND pmc.model_type    = pm.model_type
            AND pmc.encrypted_config LIKE '%${SWAP_TARGET_PATTERN}%'
       );
-- 중복된 구 credential 정리 (동일 모델 × 다른 base_url)
DELETE FROM provider_model_credentials pmc
 WHERE pmc.provider_name LIKE '%ollama%'
   AND pmc.model_name = '${OLLAMA_MODEL}'
   AND pmc.encrypted_config NOT LIKE '%${SWAP_TARGET_PATTERN}%'
   AND NOT EXISTS (SELECT 1 FROM provider_models pm WHERE pm.credential_id = pmc.id);
SQL
)
  SWAP_OUT=$(PGPASSWORD=difyai123456 psql -h 127.0.0.1 -U postgres -d dify -tAc "$SWAP_SQL" 2>&1 || echo "psql-error")
  debug "swap result: $SWAP_OUT"
  ok "  credential_id 스왑 + 잔존 레코드 정리 완료"

  # 2-3d. Redis 캐시 FLUSH — Dify 는 provider_model_credentials:* 키에 base_url 포함
  # JSON 을 캐시한다. DB 를 바꿔도 재기동 전까지 옛 값이 서빙되므로 명시 삭제.
  log "2-3d. Redis provider_model_credentials 캐시 FLUSH"
  # scan + del 을 bash 루프로 (Redis 는 비동기 unlink 가 없는 경우 DEL 로 대체)
  redis-cli -h 127.0.0.1 --scan --pattern 'provider_model_credentials:*' 2>/dev/null \
    | while read k; do redis-cli -h 127.0.0.1 DEL "$k" >/dev/null 2>&1; done
  # 모델 다이제스트 키 (tenant:langgenius/ollama:... 형태)
  redis-cli -h 127.0.0.1 --scan --pattern "*:langgenius/ollama:ollama:llm:${OLLAMA_MODEL}*" 2>/dev/null \
    | while read k; do redis-cli -h 127.0.0.1 DEL "$k" >/dev/null 2>&1; done
  ok "  Redis 캐시 삭제 완료"

  # 2-3e. 반영 위해 dify-api + dify-plugin-daemon 재기동
  if command -v supervisorctl >/dev/null 2>&1; then
    log "2-3e. dify-api + dify-plugin-daemon 재기동 (credential cache 리로드)"
    supervisorctl -c /etc/supervisor/supervisord.conf restart dify-api dify-plugin-daemon >/dev/null 2>&1 || true
    # 재기동 후 dify-api /console/api/setup 이 다시 200 뜰 때까지 60s 대기
    _w=0
    until curl -sf --max-time 3 -o /dev/null http://127.0.0.1:5001/console/api/setup; do
      sleep 3; _w=$((_w+3))
      [ $_w -ge 60 ] && break
    done
    ok "  재기동 완료 (${_w}s)"
  fi
fi

# 2-4. Chatflow import (기존 app 이 있으면 삭제 후 재import)
if [ "$DIFY_LOGGED_IN" = "true" ] && [ -f "$OFFLINE_DIFY_CHATFLOW_YAML" ]; then
  log "2-4. Chatflow DSL import"
  APP_NAME=$($PY -c "
import yaml,sys
with open('$OFFLINE_DIFY_CHATFLOW_YAML') as f:
    d = yaml.safe_load(f)
print(d.get('app',{}).get('name',''))
" 2>/dev/null || echo "")
  if [ -z "$APP_NAME" ]; then
    warn "  chatflow.yaml 에서 app.name 추출 실패"
  else
    log "  대상 App: $APP_NAME"
    # 기존 App 삭제
    APP_LIST=$(curl -sS -b "$DIFY_COOKIES" \
        "${DIFY_URL}/console/api/apps?page=1&limit=100" \
        -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" 2>&1 || echo '{}')
    EXISTING_IDS=$(echo "$APP_LIST" | $PY -c "
import json,sys
target='''$APP_NAME'''
try:
    d=json.load(sys.stdin)
    print(' '.join(a['id'] for a in d.get('data',[]) if a.get('name')==target))
except Exception: pass
" 2>/dev/null || echo "")
    for aid in $EXISTING_IDS; do
      log "  기존 App 삭제: $aid"
      curl -sS -X DELETE -b "$DIFY_COOKIES" \
          "${DIFY_URL}/console/api/apps/$aid" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" >/dev/null 2>&1 || true
    done

    # Import
    YAML_CONTENT=$($PY -c "
import json,sys
with open('$OFFLINE_DIFY_CHATFLOW_YAML') as f: content = f.read()
print(json.dumps({'mode':'yaml-content','yaml_content':content}))
")
    IMPORT_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -b "$DIFY_COOKIES" \
        -X POST "${DIFY_URL}/console/api/apps/imports" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
        -d "$YAML_CONTENT" 2>&1 || echo "HTTP:000")
    debug "import response: $IMPORT_RESP"

    IMPORT_ID=$(echo "$IMPORT_RESP" | $PY -c "
import json,sys,re
body = re.split(r'\\nHTTP:', sys.stdin.read())[0]
try:
    d = json.loads(body)
    print(d.get('id',''))
except Exception: print('')
" 2>/dev/null || echo "")
    IMPORT_STATUS=$(echo "$IMPORT_RESP" | $PY -c "
import json,sys,re
body = re.split(r'\\nHTTP:', sys.stdin.read())[0]
try:
    d = json.loads(body)
    print(d.get('status',''))
except Exception: print('')
" 2>/dev/null || echo "")
    APP_ID=$(echo "$IMPORT_RESP" | $PY -c "
import json,sys,re
body = re.split(r'\\nHTTP:', sys.stdin.read())[0]
try:
    d = json.loads(body)
    print(d.get('app_id',''))
except Exception: print('')
" 2>/dev/null || echo "")

    if [ "$IMPORT_STATUS" = "pending" ] && [ -n "$IMPORT_ID" ]; then
      log "  import pending — confirm 호출"
      CONFIRM_RESP=$(curl -sS -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/imports/$IMPORT_ID/confirm" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" 2>&1 || echo '{}')
      APP_ID=$(echo "$CONFIRM_RESP" | $PY -c "
import json,sys
try: print(json.load(sys.stdin).get('app_id',''))
except Exception: print('')
" 2>/dev/null || echo "")
    fi

    if [ -n "$APP_ID" ]; then
      ok "  Import 완료 — App ID: $APP_ID"
      # Publish — Dify 1.13.3 은 빈 JSON body 라도 Content-Type: application/json
      # 헤더가 없으면 400 bad_request 를 반환한다. 이후 /v1/chat-messages 호출이
      # "Workflow not published" 로 영구 실패하므로 응답 검증을 명시적으로 수행.
      log "  Workflow publish"
      PUBLISH_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/$APP_ID/workflows/publish" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
          -H "Content-Type: application/json" \
          -d "{}" 2>&1 || echo "HTTP:000")
      debug "publish response: $PUBLISH_RESP"
      if echo "$PUBLISH_RESP" | grep -qE 'HTTP:(200|201)'; then
        ok "  Publish 성공"
      else
        warn "  Publish 실패: $PUBLISH_RESP"
        warn "  → 이 상태에서 /v1/chat-messages 호출은 'Workflow not published' 로 실패한다."
      fi

      # API Key 발급
      log "  API Key 발급"
      KEY_RESP=$(curl -sS -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/$APP_ID/api-keys" \
          -H "Content-Type: application/json" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" 2>&1 || echo '{}')
      DIFY_API_KEY=$(echo "$KEY_RESP" | $PY -c "
import json,sys
try: print(json.load(sys.stdin).get('token',''))
except Exception: print('')
" 2>/dev/null || echo "")
      if [ -n "$DIFY_API_KEY" ]; then
        ok "  API Key 발급 완료 (앞 12자: ${DIFY_API_KEY:0:12}...)"
      else
        warn "  API Key 추출 실패: $KEY_RESP"
      fi
    else
      warn "  Import 응답에서 app_id 추출 실패"
    fi
  fi
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 5: Jenkins 초기 설정
# ────────────────────────────────────────────────────────────────────────────
step "=== 3. Jenkins 초기 설정 ==="

# 3-1. 플러그인 로드 검증 (설치는 hpi 선 배치로 완료됨)
log "3-1. 플러그인 로드 상태 검증 (workflow-cps, plain-credentials, file-parameters, htmlpublisher)"
PLUGIN_VERIFY_GROOVY='def required = ["workflow-cps", "plain-credentials", "file-parameters", "htmlpublisher"]
def pm = jenkins.model.Jenkins.instance.pluginManager
required.each { name ->
    def p = pm.getPlugin(name)
    if (p == null) println "${name}=MISSING"
    else if (!p.isActive()) println "${name}=INACTIVE(version=${p.version})"
    else println "${name}=OK(version=${p.version})"
}'
PLUGIN_VERIFY_RESP=$(jkpost --max-time 30 -X POST "${JENKINS_URL}/scriptText" \
    --data-urlencode "script=${PLUGIN_VERIFY_GROOVY}" 2>&1 || echo "failed")
PLUGIN_MISSING=""
for pname in workflow-cps plain-credentials file-parameters htmlpublisher; do
  line=$(echo "$PLUGIN_VERIFY_RESP" | grep "^${pname}=" || echo "${pname}=NORESPONSE")
  log "  $line"
  echo "$line" | grep -q "=OK" || PLUGIN_MISSING="$PLUGIN_MISSING $pname"
done
if [ -n "$PLUGIN_MISSING" ]; then
  err "필수 플러그인 미로드:$PLUGIN_MISSING"
  err "  → 이미지 빌드에 hpi 누락 가능성. offline/jenkins-plugins/ 확인 필요"
else
  ok "필수 플러그인 4개 모두 활성"
fi

# 3-2. Credentials 등록 (upsert)
if [ -n "$DIFY_API_KEY" ]; then
  log "3-2. Credentials 등록: dify-qa-api-token"
  CRED_XML="<org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl plugin=\"plain-credentials\">
  <scope>GLOBAL</scope>
  <id>dify-qa-api-token</id>
  <description>Dify QA Chatflow API Key (provision-apps.sh auto-registered)</description>
  <secret>${DIFY_API_KEY}</secret>
</org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl>"

  CRED_RESP=$(jkpost -w $'\nHTTP:%{http_code}' -X POST \
      "${JENKINS_URL}/credentials/store/system/domain/_/createCredentials" \
      -H "Content-Type: application/xml" \
      --data "$CRED_XML" 2>&1 || echo "HTTP:000")
  if echo "$CRED_RESP" | grep -qE 'HTTP:(200|302)'; then
    ok "Credentials 등록 완료"
  else
    UPDATE_CRED_RESP=$(jkpost -w $'\nHTTP:%{http_code}' -X POST \
        "${JENKINS_URL}/credentials/store/system/domain/_/credential/dify-qa-api-token/config.xml" \
        -H "Content-Type: application/xml" \
        --data "$CRED_XML" 2>&1 || echo "HTTP:000")
    if echo "$UPDATE_CRED_RESP" | grep -qE 'HTTP:(200|302)'; then
      ok "Credentials 업데이트 완료"
    else
      warn "Credentials 등록/업데이트 실패"
    fi
  fi
else
  warn "3-2. Dify API Key 없음 — Credentials 등록 건너뜀"
fi

# 3-3. Pipeline Job 생성
if [ -f "$OFFLINE_JENKINS_PIPELINE" ]; then
  log "3-3. Pipeline Job 생성: DSCORE-ZeroTouch-QA-Docker"
  JOB_XML=$($PY - << PYEOF 2>/dev/null
import xml.sax.saxutils as sax
with open('${OFFLINE_JENKINS_PIPELINE}', encoding='utf-8') as f:
    script = f.read()
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>DSCORE Zero-Touch QA Docker Pipeline (provision-apps.sh auto-created)</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>""".format(sax.escape(script)))
PYEOF
)
  if [ -z "$JOB_XML" ]; then
    warn "Job XML 생성 실패"
  else
    JOB_RESP=$(printf '%s' "$JOB_XML" | jkpost -w $'\nHTTP:%{http_code}' -X POST \
        "${JENKINS_URL}/createItem?name=DSCORE-ZeroTouch-QA-Docker" \
        -H "Content-Type: application/xml" \
        --data-binary @- 2>&1 || echo "HTTP:000")
    if echo "$JOB_RESP" | grep -qE 'HTTP:(200|302)'; then
      ok "Pipeline Job 생성 완료"
    elif echo "$JOB_RESP" | grep -qE 'HTTP:400' && echo "$JOB_RESP" | grep -qi 'already exists'; then
      UPDATE_RESP=$(printf '%s' "$JOB_XML" | jkpost -w $'\nHTTP:%{http_code}' -X POST \
          "${JENKINS_URL}/job/DSCORE-ZeroTouch-QA-Docker/config.xml" \
          -H "Content-Type: application/xml" \
          --data-binary @- 2>&1 || echo "HTTP:000")
      echo "$UPDATE_RESP" | grep -qE 'HTTP:(200|302)' && ok "Pipeline Job 업데이트 완료" \
        || warn "Pipeline Job 업데이트 실패"
    else
      warn "Pipeline Job 생성 실패: $JOB_RESP"
    fi
  fi
fi

# 3-4. mac-ui-tester 노드 등록 — 호스트 Mac agent 전제
#
# 하이브리드 설계: Jenkins controller 는 컨테이너, agent 는 호스트 Mac (JDK21 + Playwright).
# remoteFS 는 호스트 사용자의 홈 기준 절대 경로. agent 는 Jenkins 에게 이 경로를
# 워크스페이스 루트로 알린다. SCRIPTS_HOME 환경변수는 Node 에 **설정하지 않는다** —
# 호스트 agent 의 mac-agent-setup.sh 가 시스템 또는 run-script 환경변수로 주입한다
# (각 사용자의 e2e-pipeline clone 경로가 달라 컨테이너가 예측 불가).
log "3-4. mac-ui-tester 에이전트 노드 등록 (호스트 Mac JNLP)"
NODE_GROOVY=$(cat << 'GROOVY_EOF'
import jenkins.model.*
import hudson.model.*
import hudson.slaves.*

def instance = Jenkins.getInstance()
def existingNode = instance.getNode('mac-ui-tester')
def node

// 하이브리드 설계: remoteFS 를 호스트 사용자의 ~/.dscore.ttc.playwright-agent 기준 절대 경로로.
// mac-agent-setup.sh 가 이 디렉토리를 생성하므로 Node 는 prescriptive 값을 사용.
// Jenkins master 입장에선 텍스트일 뿐이고 실제 해석은 agent 쪽.
def REMOTE_FS = System.getenv('MAC_AGENT_WORKDIR') ?: '~/.dscore.ttc.playwright-agent'

if (existingNode != null) {
    node = existingNode
    println "[node] mac-ui-tester 기존 노드 발견 — 호스트 agent 전제로 갱신"
} else {
    def launcher = new JNLPLauncher(true)
    def strategy = new RetentionStrategy.Always()
    node = new DumbSlave('mac-ui-tester', REMOTE_FS, launcher)
    node.setNodeDescription('Host-side JNLP Agent (macOS, headed Playwright)')
    node.setNumExecutors(1)
    node.setLabelString('mac-ui-tester')
    node.setMode(Node.Mode.EXCLUSIVE)
    node.setRetentionStrategy(strategy)
    instance.addNode(node)
    println "[node] mac-ui-tester 생성 완료 (remoteFS=" + REMOTE_FS + ")"
}

// 환경변수 갱신 — Node 에는 SCRIPTS_HOME 을 세팅하지 않는다. 호스트 agent 측에서
// 실행 env 로 주입하는 쪽이 사용자별 clone 경로 차이를 수용하기 좋다.
// 기존 EnvironmentVariablesNodeProperty 가 있으면 (과거 brands 의 잔재) 제거.
node.getNodeProperties().removeAll { it instanceof hudson.slaves.EnvironmentVariablesNodeProperty }
// Jenkins 2.479+ 는 instance.save() 만으로 Node config.xml 에 디스크 flush 가 되지
// 않는다. updateNode() 가 명시적으로 nodes/<name>/config.xml 을 재작성한다.
instance.updateNode(node)
println "[node] 호스트 Mac agent 연결 대기 중 (remoteFS=" + REMOTE_FS + ")"
GROOVY_EOF
)
NODE_RESP=$(jkpost --max-time 30 -X POST "${JENKINS_URL}/scriptText" \
    --data-urlencode "script=${NODE_GROOVY}" 2>&1 || echo "failed")
debug "node create response: $NODE_RESP"
if echo "$NODE_RESP" | grep -q '\[node\]'; then
  ok "  $(echo "$NODE_RESP" | grep '\[node\]' | head -1)"
else
  warn "  Node 등록 응답 이상: $NODE_RESP"
fi

# ────────────────────────────────────────────────────────────────────────────
# 완료 요약
# ────────────────────────────────────────────────────────────────────────────
step "=== 프로비저닝 완료 ==="
echo ""
echo "  Jenkins:   ${JENKINS_URL}  (${JENKINS_ADMIN_USER} / ${JENKINS_ADMIN_PW})"
echo "  Dify:      ${DIFY_URL}     (${DIFY_EMAIL} / ${DIFY_PASSWORD})"
[ -n "$DIFY_API_KEY" ] && echo "  API Key:   ${DIFY_API_KEY:0:12}... (Jenkins Credentials 'dify-qa-api-token' 등록됨)"
echo ""
echo "  ⚠️  Jenkins Node 'mac-ui-tester' 는 **호스트 Mac** 에서 JNLP 로 연결해야 합니다."
echo "     컨테이너 내부 agent 는 없으며, Playwright 는 호스트에서 실행되어야 Chromium"
echo "     창이 macOS 에 뜹니다 (headed 모드). 연결 방법:"
echo "       cd <e2e-pipeline 위치>"
echo "       ./offline/mac-agent-setup.sh"
echo "     entrypoint 로그에 NODE_SECRET 이 찍혀 있으니 그 값을 사용합니다."
echo ""
