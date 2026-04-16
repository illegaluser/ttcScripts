#!/usr/bin/env bash
# =============================================================================
# setup.sh — DSCORE Zero-Touch QA 자동 설치 (Dify 1.13.3 구조 대응)
#
# 사용법:
#   cd e2e-pipeline/
#   ./setup.sh                               # 호스트 Ollama 모드 (기본)
#   OLLAMA_PROFILE=container ./setup.sh      # 컨테이너 Ollama 모드
#
# 환경변수로 기본값 오버라이드 가능:
#   OLLAMA_MODEL=qwen3.5:4b                  # Pull/검증할 Ollama 모델명
#   OLLAMA_PROFILE=host|container            # host(기본) / container
#   DIFY_EMAIL=admin@example.com             # Dify 관리자 이메일
#   DIFY_PASSWORD=Admin1234!                 # Dify 관리자 비밀번호
#   JENKINS_ADMIN_USER=admin
#   JENKINS_ADMIN_PW=Admin1234!
#   SETUP_LOG=/tmp/setup.log                 # 로그 파일 경로
#   DEBUG=1                                  # curl 요청/응답 상세 출력
#
# 자동화 범위:
#   Phase 0: 사전 요구사항 확인 + 프로파일 감지
#   Phase 1: docker compose up --build (프로파일 인식)
#   Phase 2: 서비스 헬스 대기 (nginx, api, plugin_daemon, jenkins)
#   Phase 3: Ollama 모델 확인/Pull + Dify 도달성 검증
#   Phase 4: Dify 관리자 계정 생성 + 로그인
#            ⚠️ Ollama 플러그인/모델 등록은 Dify 1.x 에서 마켓플레이스 기반이라 수동
#   Phase 5: Jenkins 플러그인/Credentials/CSP/Pipeline Job/Node
#
# 제한:
#   - Dify 1.x Ollama 플러그인 설치 자동화는 미지원 (Dify 콘솔에서 수동 수행)
#     → GUIDE.md §6.2 참조
#   - 컨테이너 Ollama 모드는 OLLAMA_PROFILE=container 명시 필요
#
# 로그:
#   모든 출력은 stdout + ${SETUP_LOG}(기본: setup.log) 에 동시 기록된다.
#   DEBUG=1 로 curl 바디까지 출력.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# .env 자동 로드 — 사용자가 .env.example 을 .env 로 복사한 경우 그 값을 우선 적용.
# .env 안의 값은 셸 환경변수보다 약하다 (이미 export 된 값이 있으면 그걸 유지).
# ─────────────────────────────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
  # set -a 로 source 한 변수가 자동 export 되도록 함 (compose 와 동일 동작)
  set -a
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/.env"
  set +a
  _ENV_LOADED=1
else
  _ENV_LOADED=0
fi

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (환경변수 → .env → 기본값 순으로 결정)
# ─────────────────────────────────────────────────────────────────────────────
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:4b}"
OLLAMA_PROFILE="${OLLAMA_PROFILE:-host}"                  # host | container
DIFY_EMAIL="${DIFY_EMAIL:-admin@example.com}"
DIFY_PASSWORD="${DIFY_PASSWORD:-Admin1234!}"
JENKINS_ADMIN_USER="${JENKINS_ADMIN_USER:-admin}"
JENKINS_ADMIN_PW="${JENKINS_ADMIN_PW:-Admin1234!}"
SCRIPTS_HOME="${SCRIPTS_HOME:-$SCRIPT_DIR}"
SETUP_LOG="${SETUP_LOG:-${SCRIPT_DIR}/setup.log}"
DEBUG="${DEBUG:-0}"

# 기본값 사용 여부 판정 (배너에서 경고 출력용)
_USING_DEFAULT_DIFY_PW=0
_USING_DEFAULT_JENKINS_PW=0
[ "$DIFY_PASSWORD"    = "Admin1234!" ] && _USING_DEFAULT_DIFY_PW=1
[ "$JENKINS_ADMIN_PW" = "Admin1234!" ] && _USING_DEFAULT_JENKINS_PW=1

JENKINS_URL="http://localhost:18080"
DIFY_URL="http://localhost:18081"

# ─────────────────────────────────────────────────────────────────────────────
# 전체 stdout/stderr 을 setup.log 에 tee. 이후 모든 echo/printf 가 자동으로 기록된다.
# ─────────────────────────────────────────────────────────────────────────────
: > "$SETUP_LOG"
exec > >(tee -a "$SETUP_LOG") 2>&1

# ─────────────────────────────────────────────────────────────────────────────
# 로그 유틸리티
# ─────────────────────────────────────────────────────────────────────────────
_ts() { date +%H:%M:%S; }

log()   { printf '[%s] [·] %s\n' "$(_ts)" "$*"; }
step()  { printf '\n[%s] [▶] %s\n'  "$(_ts)" "$*"; }
ok()    { printf '[%s] [✓] %s\n' "$(_ts)" "$*"; }
warn()  { printf '[%s] [⚠] %s\n' "$(_ts)" "$*"; }
err()   { printf '[%s] [✗] %s\n' "$(_ts)" "$*" >&2; }
debug() { [ "$DEBUG" = "1" ] && printf '[%s] [D] %s\n' "$(_ts)" "$*"; return 0; }

# 명령 실행 전 echo 하고 실행 (stdout/stderr 는 이미 tee 중)
run() {
  log "\$ $*"
  "$@"
}

# 시도하되 실패해도 계속 (경고만)
try() {
  log "\$ $* (실패 허용)"
  "$@" || warn "명령 실패 — 계속 진행: $*"
}

# Phase 경과 시간 측정
_phase_t0=0
phase_start() {
  step "=== $1 ==="
  _phase_t0=$SECONDS
}
phase_end() {
  local dur=$((SECONDS - _phase_t0))
  ok "Phase 완료 (${dur}초)"
}

# URL 이 지정한 status 로 응답할 때까지 대기
# wait_http_status <url> <expected_status> <label> <timeout_sec>
wait_http_status() {
  local url="$1" expected="$2" label="$3" timeout="${4:-300}"
  log "대기: $label  (기대 status: $expected, 최대 ${timeout}초)  URL: $url"
  local elapsed=0 actual=""
  while :; do
    actual=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
    debug "poll $url → HTTP $actual"
    if [ "$actual" = "$expected" ]; then
      ok "$label 준비 완료 (HTTP $actual, ${elapsed}초 경과)"
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
    if [ $elapsed -ge $timeout ]; then
      err "$label 타임아웃 (마지막 HTTP: $actual, ${timeout}초 초과)"
      return 1
    fi
    printf '.'
  done
}

# JSON 필드 추출 (python3)
json_get() {
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1)" 2>/dev/null || echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# OS 감지 + 호스트 Ollama 자동 설치
# ─────────────────────────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Linux*)                 echo linux ;;
    Darwin*)                echo macos ;;
    MINGW*|MSYS*|CYGWIN*)   echo windows ;;
    *)                      echo unknown ;;
  esac
}

# 호스트에 Ollama 를 설치한다 (OS 별 분기).
# 성공 시 0, 실패 시 1.
install_host_ollama() {
  local os; os=$(detect_os)
  log "호스트 OS 감지: $os"

  case "$os" in
    windows)
      if ! command -v winget >/dev/null 2>&1 && ! command -v winget.exe >/dev/null 2>&1; then
        err "winget 명령을 찾을 수 없음. Windows App Installer 가 필요하다."
        err "  → Microsoft Store 에서 'App Installer' 검색 후 설치/업데이트"
        err "  → 또는 수동 설치: https://ollama.com/download/windows"
        return 1
      fi
      log "winget 으로 Ollama 설치 시작 (UAC 프롬프트가 뜰 수 있음)..."
      if run winget install --id Ollama.Ollama --silent \
                            --accept-package-agreements \
                            --accept-source-agreements; then
        ok "winget install Ollama 완료"
      else
        err "winget install 실패. 수동 설치: https://ollama.com/download/windows"
        return 1
      fi
      # 현재 셸 PATH 에는 즉시 반영되지 않으므로 일반 설치 경로를 추가한다.
      local ollama_dir="${LOCALAPPDATA:-$USERPROFILE/AppData/Local}/Programs/Ollama"
      if [ -x "$ollama_dir/ollama.exe" ]; then
        export PATH="$ollama_dir:$PATH"
        ok "PATH 에 $ollama_dir 추가 (현재 셸 한정)"
      fi
      warn "Windows 에서는 설치 직후 Ollama 데스크톱 앱이 트레이에서 기동되어야 한다."
      warn "  → 시작메뉴 → 'Ollama' 실행 → 트레이 아이콘 확인"
      warn "  → 컨테이너에서 접근하려면 시스템 환경변수 OLLAMA_HOST=0.0.0.0 등록 후 재시작 필요"
      ;;

    macos)
      if ! command -v brew >/dev/null 2>&1; then
        err "brew 명령 없음. Homebrew 설치 후 재실행: https://brew.sh"
        return 1
      fi
      log "brew install ollama ..."
      if run brew install ollama; then
        ok "brew install ollama 완료"
      else
        err "brew install ollama 실패"
        return 1
      fi
      log "ollama 백그라운드 서비스 시작 (brew services start ollama)..."
      try brew services start ollama
      warn "macOS: 컨테이너에서 호스트 Ollama 로 접근하려면 OLLAMA_HOST=0.0.0.0 필요."
      warn "  → 수동 기동 대안: OLLAMA_HOST=0.0.0.0 ollama serve"
      ;;

    linux)
      log "공식 설치 스크립트 실행 (sudo 필요)..."
      if run bash -c 'curl -fsSL https://ollama.com/install.sh | sh'; then
        ok "Ollama 설치 스크립트 완료"
      else
        err "설치 스크립트 실패. 수동 설치: https://ollama.com/download/linux"
        return 1
      fi
      warn "Linux: systemd 유닛이 기본 127.0.0.1 바인딩일 수 있다."
      warn "  → 컨테이너 접근 필요 시: sudo systemctl edit ollama 로 Environment=OLLAMA_HOST=0.0.0.0 추가 후 재시작"
      ;;

    *)
      err "OS 감지 실패 ($(uname -s)). 수동 설치: https://ollama.com/download"
      return 1
      ;;
  esac
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# 시작 배너
# ─────────────────────────────────────────────────────────────────────────────
_global_t0=$SECONDS
log "================================================================="
log " DSCORE Zero-Touch QA 자동 설치 시작"
log "================================================================="
log " 시작 시각       : $(date +'%Y-%m-%d %H:%M:%S')"
log " 작업 디렉터리   : $SCRIPT_DIR"
log " 로그 파일       : $SETUP_LOG"
log " 설정 소스       : $([ "$_ENV_LOADED" = "1" ] && echo '.env 파일 로드됨' || echo '.env 없음 → 기본값/셸 환경변수 사용')"
log " Ollama 프로파일 : $OLLAMA_PROFILE"
log " Ollama 모델     : $OLLAMA_MODEL"
log " Dify 계정       : $DIFY_EMAIL  (비밀번호: $([ "$_USING_DEFAULT_DIFY_PW" = "1" ] && echo '⚠ 기본값 Admin1234!' || echo '✓ 사용자 지정'))"
log " Jenkins 계정    : $JENKINS_ADMIN_USER  (비밀번호: $([ "$_USING_DEFAULT_JENKINS_PW" = "1" ] && echo '⚠ 기본값 Admin1234!' || echo '✓ 사용자 지정'))"
log " DEBUG 모드      : $([ "$DEBUG" = "1" ] && echo ON || echo OFF)"

if [ "$_USING_DEFAULT_DIFY_PW" = "1" ] || [ "$_USING_DEFAULT_JENKINS_PW" = "1" ]; then
  if [ "$_ENV_LOADED" = "0" ]; then
    log ""
    log " ⚠️  비밀번호가 기본값(Admin1234!)이다. 보안상 권장하지 않는다."
    log "    cp .env.example .env 후 .env 안의 비밀번호를 직접 설정하면"
    log "    다음 실행부터 그 값으로 계정이 생성된다 (자세한 내용은 GUIDE.md §자동 설치)."
    log ""
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 0: 사전 요구사항 확인
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 0: 사전 요구사항 확인"

log "docker 명령 확인..."
command -v docker  >/dev/null 2>&1 || { err "docker 명령 없음. Docker Desktop 설치 필요."; exit 1; }
ok "docker 명령 존재"

log "python3 명령 확인..."
command -v python3 >/dev/null 2>&1 || { err "python3 필요. (brew/apt install python3)"; exit 1; }
ok "python3 명령 존재"

log "Docker 데몬 상태 확인..."
docker info >/dev/null 2>&1 || { err "Docker 데몬이 실행되지 않음. Docker Desktop 실행 필요."; exit 1; }
ok "Docker 데몬 응답"

log "필수 파일 존재 확인..."
[ -f "$SCRIPT_DIR/dify-chatflow.yaml" ] || { err "dify-chatflow.yaml 없음. e2e-pipeline/ 폴더 안에서 실행."; exit 1; }
[ -f "$SCRIPT_DIR/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline" ] || { err "DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline 없음."; exit 1; }
[ -f "$SCRIPT_DIR/docker-compose.yaml" ] || { err "docker-compose.yaml 없음."; exit 1; }
ok "필수 파일 모두 존재"

log "Ollama 프로파일 검증..."
case "$OLLAMA_PROFILE" in
  host)
    if command -v ollama >/dev/null 2>&1; then
      ok "호스트 ollama CLI 발견: $(ollama --version 2>/dev/null | head -n1 || echo 'version unknown')"
    else
      warn "호스트 ollama CLI 미발견 — 자동 설치 시도 (OS: $(detect_os))"
      if install_host_ollama; then
        if command -v ollama >/dev/null 2>&1; then
          ok "호스트 ollama 설치 완료: $(ollama --version 2>/dev/null | head -n1 || echo 'version unknown')"
        else
          err "설치는 성공했지만 현재 셸에서 ollama 명령을 찾을 수 없음."
          err "  → 새 터미널을 열고 다시 실행하거나, PATH 갱신 후 재시도"
          err "  → Windows: 시작메뉴 → Ollama 실행 후 새 Git Bash 에서 재실행"
          exit 1
        fi
      else
        err "Ollama 자동 설치 실패. 위의 안내대로 수동 설치 후 재실행."
        exit 1
      fi
    fi
    ;;
  container)
    ok "컨테이너 Ollama 모드 선택 — Phase 1 에서 --profile container-ollama 사용"
    log "  (호스트에 Ollama 가 설치되어 있을 필요 없음)"
    ;;
  *)
    err "OLLAMA_PROFILE 값 잘못됨: '$OLLAMA_PROFILE' (허용값: host | container)"
    exit 1
    ;;
esac

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Jenkins init 스크립트 생성 + docker compose 기동
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 1: Docker 스택 기동"

log "Jenkins Groovy init 스크립트 생성 (jenkins-init/01-security.groovy)..."
mkdir -p jenkins-init

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

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
instance.setCrumbIssuer(null)

instance.save()
println "[init] Jenkins 보안 설정 완료"
GROOVY_SCRIPT
ok "Groovy init 스크립트 생성 완료"

log "docker-compose.override.yaml 생성 (Setup Wizard 우회 + CSP 완화)..."
cat > docker-compose.override.yaml << 'OVERRIDE_YAML'
# setup.sh 가 생성 — Jenkins Setup Wizard 우회 + HTML 리포트 CSP 완화
services:
  jenkins:
    environment:
      JAVA_OPTS: >-
        -Djenkins.install.runSetupWizard=false
        -Dhudson.model.DirectoryBrowserSupport.CSP=
    volumes:
      - ./jenkins-init:/var/jenkins_home/init.groovy.d
OVERRIDE_YAML
ok "override.yaml 생성 완료"

COMPOSE_PROFILE_ARGS=""
if [ "$OLLAMA_PROFILE" = "container" ]; then
  COMPOSE_PROFILE_ARGS="--profile container-ollama"
  log "컨테이너 Ollama 모드 — compose 명령에 $COMPOSE_PROFILE_ARGS 추가"
fi

log "docker compose up -d --build 시작 (첫 기동은 Jenkins 이미지 빌드 포함 30~50분 소요)"
log "BuildKit 진행 출력은 plain 모드로 강제 (BUILDKIT_PROGRESS=plain)"
# --progress 플래그는 Compose 버전에 따라 위치 호환성 문제 있음 → 환경변수로 강제
export BUILDKIT_PROGRESS=plain
export DOCKER_BUILDKIT=1
run docker compose $COMPOSE_PROFILE_ARGS up -d --build
ok "컨테이너 기동 명령 반환 (백그라운드 상태)"

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: 서비스 헬스 대기
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 2: 서비스 준비 대기"

log "compose ps 상태 조회..."
run docker compose $COMPOSE_PROFILE_ARGS ps || true

# (1) nginx + web 경로 — /install 페이지가 200 나오면 web + nginx 둘 다 준비 완료
wait_http_status "${DIFY_URL}/install" "200" "Dify 웹 (/install)" 240

# (2) api 경로 — /console/api/setup 이 200 을 주면 api 와 DB 마이그레이션까지 완료
wait_http_status "${DIFY_URL}/console/api/setup" "200" "Dify API (/console/api/setup)" 420

# (3) plugin_daemon — HTTP 프로세스가 실제로 응답하는지 확인
#     curl 는 연결 실패 시 '000' 을 출력하므로 그 값을 구분해야 의미 있는 체크가 된다.
log "plugin_daemon 도달성 점검 (api 컨테이너 내부에서)..."
DAEMON_CODE=$(docker exec e2e-dify-api sh -c 'curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://plugin_daemon:5002/' 2>/dev/null || echo "000")
debug "plugin_daemon HTTP code: $DAEMON_CODE"
case "$DAEMON_CODE" in
  000)
    warn "plugin_daemon 도달 불가 (연결 실패 또는 타임아웃)."
    warn "  → docker compose logs plugin_daemon 으로 상태 확인"
    warn "  → 플러그인 마켓플레이스 설치가 동작하지 않을 수 있음"
    ;;
  5??)
    warn "plugin_daemon 이 5xx 응답 ($DAEMON_CODE) — 기동은 됐지만 내부 에러 가능성"
    warn "  → docker compose logs plugin_daemon 확인"
    ;;
  *)
    ok "plugin_daemon 응답 OK (HTTP $DAEMON_CODE — 프로세스 정상 기동)"
    ;;
esac

# (4) Jenkins
wait_http_status "${JENKINS_URL}/login" "200" "Jenkins (/login)" 360

# (5) Jenkins REST API 인증 대기 (Groovy init 완료 후 가능)
log "Jenkins REST API 인증 대기..."
_je=0
until curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
    "${JENKINS_URL}/api/json" >/dev/null 2>&1; do
  sleep 5; _je=$((_je + 5))
  if [ $_je -ge 120 ]; then
    warn "Jenkins API 인증 타임아웃 (2분). Groovy init 지연 가능 — 계속 진행."
    break
  fi
done
[ $_je -lt 120 ] && ok "Jenkins REST API 인증 확인 (${_je}초 경과)"

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Ollama 모델 확인/Pull + Dify 도달성 검증
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 3: Ollama 모델 확인/Pull: ${OLLAMA_MODEL}"

if [ "$OLLAMA_PROFILE" = "container" ]; then
  log "컨테이너 Ollama 모드 — e2e-ollama 컨테이너에서 모델 확인"
  if docker exec e2e-ollama ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$OLLAMA_MODEL"; then
    ok "$OLLAMA_MODEL 이미 설치되어 있음 (컨테이너)"
  else
    log "$OLLAMA_MODEL Pull 시작 (컨테이너, 수분~수십 분 소요)..."
    if run docker exec e2e-ollama ollama pull "$OLLAMA_MODEL"; then
      ok "$OLLAMA_MODEL Pull 완료 (컨테이너)"
    else
      warn "Pull 실패. 수동으로 실행: docker exec -it e2e-ollama ollama pull $OLLAMA_MODEL"
    fi
  fi

  log "Dify api → 컨테이너 Ollama 도달성 검증..."
  if docker exec e2e-dify-api sh -c 'curl -s -f http://ollama:11434/api/tags >/dev/null'; then
    ok "Dify api → http://ollama:11434 연결 OK"
  else
    warn "Dify api 가 ollama 컨테이너에 도달 불가. compose up --profile container-ollama 확인."
  fi

else
  log "호스트 Ollama 모드 — 호스트 ollama CLI 사용 (Phase 0 에서 설치 보장 완료)"
  if ! command -v ollama >/dev/null 2>&1; then
    err "호스트 ollama 명령을 찾을 수 없음. Phase 0 설치 로직이 제대로 통과하지 않았다 — 재실행 필요."
    exit 1
  elif ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$OLLAMA_MODEL"; then
    ok "$OLLAMA_MODEL 이미 설치되어 있음 (호스트)"
  else
    log "$OLLAMA_MODEL Pull 시작 (호스트, 수분~수십 분 소요)..."
    if run ollama pull "$OLLAMA_MODEL"; then
      ok "$OLLAMA_MODEL Pull 완료 (호스트)"
    else
      warn "Pull 실패. 수동으로 실행: ollama pull $OLLAMA_MODEL"
    fi
  fi

  log "Dify api → 호스트 Ollama 도달성 검증 (host.docker.internal:11434)..."
  if docker exec e2e-dify-api sh -c 'curl -s -f http://host.docker.internal:11434/api/tags >/dev/null'; then
    ok "Dify api → http://host.docker.internal:11434 연결 OK"
  else
    warn "Dify api 가 호스트 Ollama 에 도달 불가."
    warn "  → 호스트 Ollama 가 OLLAMA_HOST=0.0.0.0 으로 바인딩되어 있는지 확인 (기본값 127.0.0.1 은 컨테이너에서 못 닿음)"
    warn "  → Windows: 시스템 환경변수 OLLAMA_HOST=0.0.0.0 등록 후 ollama.exe 재시작"
    warn "  → Linux/macOS: OLLAMA_HOST=0.0.0.0 ollama serve 로 재기동"
  fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Dify 초기 설정 (관리자 계정 + 로그인)
# Dify 1.x 플러그인/모델 공급자 등록 자동화는 미지원 — 수동 안내
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 4: Dify 초기 설정"

DIFY_API_KEY=""
DIFY_LOGGED_IN=false
DIFY_COOKIES="${SCRIPT_DIR}/.dify-cookies.txt"
DIFY_CSRF_TOKEN=""
rm -f "$DIFY_COOKIES"

# 4-1. 관리자 계정 생성
# POST /console/api/setup  body: {email, name, password}  → {"result":"success"} (201)
log "4-1. Dify 관리자 계정 생성 시도 — POST /console/api/setup"
SETUP_RESP=$(curl -sS -X POST "${DIFY_URL}/console/api/setup" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${DIFY_EMAIL}\",\"name\":\"Admin\",\"password\":\"${DIFY_PASSWORD}\"}" \
    2>&1 || echo '{"result":"error"}')
debug "setup response: $SETUP_RESP"

if echo "$SETUP_RESP" | python3 -c "import json,sys;d=json.load(sys.stdin);exit(0 if d.get('result')=='success' else 1)" 2>/dev/null; then
  ok "Dify 관리자 계정 생성 완료: ${DIFY_EMAIL}"
else
  warn "계정 생성 응답: ${SETUP_RESP}"
  warn "  → 이미 설정된 상태일 가능성 높음. 로그인으로 계속 진행."
fi

# 4-2. 로그인
#      - access_token 은 HttpOnly 쿠키로만 내려온다 (body 에는 {"result":"success"} 만).
#      - Dify 1.13.3 의 /console/api/login 은 @decrypt_password_field 데코레이터가 걸려
#        password 필드를 base64.b64decode() 한다 (RSA 아님. api/libs/encryption.py:18).
#        plaintext 로 보내면 "Invalid encrypted data" 401 이 난다 → 반드시 base64 인코딩.
#      - /console/api/setup 쪽은 이 데코레이터가 없어서 plaintext 로 통과했다.
log "4-2. Dify 로그인 시도 — POST /console/api/login (password base64 인코딩 + 쿠키 jar 저장)"
DIFY_PASSWORD_B64=$(printf '%s' "$DIFY_PASSWORD" | python3 -c 'import base64,sys;print(base64.b64encode(sys.stdin.buffer.read()).decode())')
debug "password b64: $DIFY_PASSWORD_B64"

LOGIN_RESP=$(curl -sS -c "$DIFY_COOKIES" \
    -X POST "${DIFY_URL}/console/api/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${DIFY_EMAIL}\",\"password\":\"${DIFY_PASSWORD_B64}\",\"remember_me\":true}" \
    2>&1 || echo '{}')
debug "login response: $LOGIN_RESP"

LOGIN_RESULT=$(echo "$LOGIN_RESP" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); print(d.get('result',''))
except Exception:
    print('')
" 2>/dev/null || echo "")

if [ "$LOGIN_RESULT" = "success" ] && [ -s "$DIFY_COOKIES" ] && grep -q 'access_token' "$DIFY_COOKIES"; then
  ok "Dify 로그인 성공 (쿠키 jar 저장: $DIFY_COOKIES)"

  # Dify 1.13.3 의 자체 CSRF 보호 — 모든 state-changing console 호출에 X-CSRF-Token 헤더가 필요.
  # login 응답이 csrf_token 쿠키를 HttpOnly=False 로 내려주므로 jar 에서 평문 추출 가능.
  # cookies.txt (Netscape format) 의 6번째 컬럼 = name, 7번째 컬럼 = value
  # HTTPS+빈 COOKIE_DOMAIN 환경에서는 __Host-csrf_token prefix 가 붙을 수 있으므로 둘 다 매치.
  DIFY_CSRF_TOKEN=$(awk '$6 == "csrf_token" || $6 == "__Host-csrf_token" { print $7; exit }' "$DIFY_COOKIES" 2>/dev/null || echo "")
  if [ -n "$DIFY_CSRF_TOKEN" ]; then
    ok "  CSRF 토큰 추출 완료 (앞 16자: ${DIFY_CSRF_TOKEN:0:16}...)"
    DIFY_LOGGED_IN=true
  else
    warn "  CSRF 토큰을 cookies.txt 에서 찾지 못함 — Dify 버전 변경 가능성"
    warn "  → cookies.txt 내용:"
    cat "$DIFY_COOKIES" | sed 's/^/      /' || true
    DIFY_LOGGED_IN=false
  fi
else
  err "Dify 로그인 실패."
  err "  응답 : ${LOGIN_RESP}"
  err "  쿠키 : $([ -s "$DIFY_COOKIES" ] && echo '있음' || echo '비어있음')"
  warn "이후 Chatflow 자동 import 는 건너뛰고 수동 안내로 전환 — GUIDE.md §7 참조"
fi

# 4-3. Dify Ollama 플러그인/모델 등록 안내 (자동화 불가 — 마켓플레이스 UI 강제)
log "4-3. Dify Ollama 플러그인/모델 공급자 등록 (수동 수행 필요)"
warn "Dify 1.x 는 Ollama 공급자가 플러그인 기반이다. 마켓플레이스 설치는 API 자동화를 지원하지 않는다."
warn "아래 단계를 Dify 콘솔에서 직접 수행해야 한다 (GUIDE.md §6.2 참조):"
warn "  1) http://localhost:18081/signin 에서 로그인"
warn "  2) 설정 → 플러그인 → 마켓플레이스 → 'Ollama' 검색 → Install"
warn "  3) 설정 → 모델 공급자 → Ollama 카드 → + Add Model"
if [ "$OLLAMA_PROFILE" = "container" ]; then
  warn "     - Base URL: http://ollama:11434"
else
  warn "     - Base URL: http://host.docker.internal:11434"
fi
warn "     - Model Name: ${OLLAMA_MODEL}  (dify-chatflow.yaml 과 정확히 일치해야 함)"

# 4-4 ~ 4-6. Chatflow DSL import → publish → API Key
# 주의: Dify 1.13.3 에서 import 경로가 복수형으로 바뀌었고 body/응답 스키마가 다르다.
#   URL   : POST /console/api/apps/imports   (imports, 복수)
#   body  : {"mode":"yaml-content","yaml_content":"<raw yaml>"}
#   resp  : {id, status, app_id, ...}   — 'id' 는 import record id, 실제 app_id 는 'app_id' 필드
#   status: "completed" → 바로 사용 가능 / "pending" → /apps/imports/{id}/confirm 로 확정 필요 / "failed" → 에러
if [ "$DIFY_LOGGED_IN" = "true" ]; then
  log "4-4. Chatflow DSL import 시도 (/console/api/apps/imports, mode=yaml-content)"
  IMPORT_BODY=$(python3 - << PYEOF 2>/dev/null
import json
with open('${SCRIPT_DIR}/dify-chatflow.yaml', encoding='utf-8') as f:
    content = f.read()
print(json.dumps({'mode': 'yaml-content', 'yaml_content': content}))
PYEOF
)
  if [ -z "$IMPORT_BODY" ]; then
    warn "Chatflow DSL 로드 실패 — Chatflow 수동 생성 필요 (GUIDE.md §7)"
  else
    IMPORT_RESP=$(curl -sS -b "$DIFY_COOKIES" \
        -X POST "${DIFY_URL}/console/api/apps/imports" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
        -d "$IMPORT_BODY" 2>&1 || echo '{}')
    debug "import response: $IMPORT_RESP"

    # status / import_id / app_id 를 한 번에 파싱
    IMPORT_PARSED=$(echo "$IMPORT_RESP" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('status',''))
    print(d.get('id',''))
    print(d.get('app_id',''))
except Exception:
    print(''); print(''); print('')
" 2>/dev/null || printf '\n\n\n')
    IMPORT_STATUS=$(echo "$IMPORT_PARSED" | sed -n 1p)
    IMPORT_ID=$(echo     "$IMPORT_PARSED" | sed -n 2p)
    DIFY_APP_ID=$(echo   "$IMPORT_PARSED" | sed -n 3p)
    debug "import parsed: status='$IMPORT_STATUS' import_id='$IMPORT_ID' app_id='$DIFY_APP_ID'"

    # status=pending 이면 confirm 단계 추가
    if [ "$IMPORT_STATUS" = "pending" ] && [ -n "$IMPORT_ID" ]; then
      log "  import status=pending → /apps/imports/${IMPORT_ID}/confirm 호출"
      CONFIRM_RESP=$(curl -sS -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/imports/${IMPORT_ID}/confirm" \
          -H "Content-Type: application/json" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
          -d '{}' 2>&1 || echo '{}')
      debug "confirm response: $CONFIRM_RESP"
      DIFY_APP_ID=$(echo "$CONFIRM_RESP" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); print(d.get('app_id','') or d.get('id',''))
except Exception:
    print('')
" 2>/dev/null || echo "")
    fi

    if [ -z "$DIFY_APP_ID" ]; then
      warn "Chatflow import 실패"
      warn "  status : ${IMPORT_STATUS:-(없음)}"
      warn "  응답   : ${IMPORT_RESP}"
      warn "  → Dify 콘솔에서 수동으로 DSL 가져오기 (GUIDE.md §7)"
    else
      ok "Chatflow import 완료 (App ID: ${DIFY_APP_ID})"

      # 4-5. Chatflow publish — body 는 빈 JSON
      log "4-5. Chatflow publish 시도 (POST .../workflows/publish, body={})"
      PUB_RESP=$(curl -sS -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/${DIFY_APP_ID}/workflows/publish" \
          -H "Content-Type: application/json" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
          -d '{}' 2>&1 || echo '{}')
      debug "publish response: $PUB_RESP"
      if echo "$PUB_RESP" | grep -q '"result":"success"'; then
        ok "Chatflow publish 완료"
      else
        warn "publish 응답 비정상: $PUB_RESP"
      fi

      # 4-6. Dify API Key 발급
      log "4-6. Dify API Key 발급 시도 (POST .../api-keys)"
      KEY_RESP=$(curl -sS -b "$DIFY_COOKIES" \
          -X POST "${DIFY_URL}/console/api/apps/${DIFY_APP_ID}/api-keys" \
          -H "Content-Type: application/json" \
          -H "X-CSRF-Token: ${DIFY_CSRF_TOKEN}" \
          -d '{}' 2>&1 || echo '{}')
      debug "api-key response: $KEY_RESP"

      DIFY_API_KEY=$(echo "$KEY_RESP" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); print(d.get('token',''))
except Exception:
    print('')
" 2>/dev/null || echo "")

      if [ -n "$DIFY_API_KEY" ]; then
        ok "Dify API Key 발급 완료: ${DIFY_API_KEY}"
      else
        warn "API Key 발급 실패 (응답: ${KEY_RESP})"
        warn "  → Dify 콘솔 → 앱 → API 접근 → + 새 API Key (GUIDE.md §7.7)"
      fi
    fi
  fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Jenkins 초기 설정 (REST API)
# ─────────────────────────────────────────────────────────────────────────────
phase_start "Phase 5: Jenkins 초기 설정"

JENKINS_READY=false
log "Jenkins API 재확인..."
for i in $(seq 1 12); do
  if curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      "${JENKINS_URL}/api/json" >/dev/null 2>&1; then
    JENKINS_READY=true
    break
  fi
  sleep 10
done

if [ "$JENKINS_READY" = "false" ]; then
  warn "Jenkins REST API 응답 없음. Jenkins 설정을 수동으로 완료 (GUIDE.md §8)."
else
  ok "Jenkins API 응답 OK"

  # ───────────────────────────────────────────────────────────────────────────
  # 5-1. 플러그인 설치 — **동기식** Groovy /scriptText 경로
  #      /pluginManager/installNecessaryPlugins 는 async 라서 의존성 40+ 개를 가진
  #      workflow-aggregator 가 60초 안에 끝난다는 보장이 없다. 실제로 이전 실행에서
  #      restart 시점에 workflow-cps 가 아직 로드 안 된 상태로 /createItem 을 호출해
  #      500 "A problem occurred" 가 발생했다.
  #      Groovy 에서 plugin.deploy().get() 은 download 가 끝날 때까지 블록한다.
  # ───────────────────────────────────────────────────────────────────────────
  log "5-1. 플러그인 동기 설치 (Groovy deploy().get()): workflow-aggregator, file-parameters, htmlpublisher, plain-credentials"

  PLUGIN_INSTALL_GROOVY=$(cat << 'PLUGIN_EOF'
import jenkins.model.Jenkins

def plugins = ['workflow-aggregator', 'file-parameters', 'htmlpublisher', 'plain-credentials']
def pm = Jenkins.getInstance().getPluginManager()
def uc = Jenkins.getInstance().getUpdateCenter()

println "[plugin] Update Center 메타데이터 갱신..."
uc.updateAllSites()

def futures = []
plugins.each { name ->
    if (pm.getPlugin(name) != null) {
        println "[plugin] ${name} 이미 설치됨 — 건너뜀"
        return
    }
    def p = uc.getPlugin(name)
    if (p == null) {
        println "[plugin][ERROR] ${name} 를 Update Center 에서 찾을 수 없음"
        return
    }
    println "[plugin] ${name} 다운로드 시작..."
    futures << [name: name, f: p.deploy(true)]  // dynamicLoad=true
}

futures.each { item ->
    try {
        item.f.get()
        println "[plugin] ${item.name} 설치 완료"
    } catch (Exception e) {
        println "[plugin][ERROR] ${item.name} 설치 실패: ${e.message}"
    }
}
println "[plugin] 전체 설치 요청 종료"
PLUGIN_EOF
)

  # scriptText 는 블로킹 호출이 길 수 있으므로 --max-time 을 넉넉히 (10분)
  PLUGIN_RESP=$(curl -sS --max-time 600 -w $'\nHTTP:%{http_code}' -X POST \
      "${JENKINS_URL}/scriptText" \
      -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      --data-urlencode "script=${PLUGIN_INSTALL_GROOVY}" 2>&1 || echo "HTTP:000")
  debug "plugin install response: $PLUGIN_RESP"
  if echo "$PLUGIN_RESP" | grep -qE 'HTTP:(200|302)'; then
    ok "플러그인 동기 설치 완료"
    # Groovy println 출력을 로그에 드러내기 (디버깅 용도)
    echo "$PLUGIN_RESP" | grep -E '^\[plugin\]' | while IFS= read -r line; do
      log "  $line"
    done
  else
    warn "플러그인 설치 비정상 응답:"
    warn "  $PLUGIN_RESP"
    warn "  → GUIDE.md §8.2 참조"
  fi

  log "Jenkins 재시작 (플러그인 활성화)..."
  try docker restart e2e-jenkins
  log "재시작 후 준비 대기..."
  sleep 20
  wait_http_status "${JENKINS_URL}/login" "200" "Jenkins (재시작 후)" 180

  log "재시작 후 API 인증 재확인..."
  _je2=0
  until curl -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      "${JENKINS_URL}/api/json" >/dev/null 2>&1; do
    sleep 5; _je2=$((_je2 + 5))
    [ $_je2 -ge 120 ] && { warn "Jenkins API 재인증 타임아웃"; break; }
  done

  # 필수 플러그인이 실제 로드되었는지 검증 — Groovy /scriptText 로 직접 PluginManager 조회.
  # 이전에는 /pluginManager/api/json 응답을 파싱했으나 응답이 거대해서 false negative 가 발생했다.
  # PluginManager.getPlugin(name).isActive() 가 가장 신뢰할 수 있는 검사다.
  log "필수 플러그인 로드 상태 검증 (workflow-cps, plain-credentials, file-parameters, htmlpublisher)..."
  PLUGIN_VERIFY_GROOVY='def required = ["workflow-cps", "plain-credentials", "file-parameters", "htmlpublisher"]
def pm = jenkins.model.Jenkins.instance.pluginManager
required.each { name ->
    def p = pm.getPlugin(name)
    if (p == null) {
        println "${name}=MISSING"
    } else if (!p.isActive()) {
        println "${name}=INACTIVE(version=${p.version})"
    } else {
        println "${name}=OK(version=${p.version})"
    }
}'
  PLUGIN_VERIFY_RESP=$(curl -sS --max-time 30 -X POST "${JENKINS_URL}/scriptText" \
      -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      --data-urlencode "script=${PLUGIN_VERIFY_GROOVY}" 2>&1 || echo "scriptText-failed")
  debug "plugin verify response: $PLUGIN_VERIFY_RESP"

  PLUGIN_MISSING=""
  for plugin_name in workflow-cps plain-credentials file-parameters htmlpublisher; do
    line=$(echo "$PLUGIN_VERIFY_RESP" | grep "^${plugin_name}=" || echo "${plugin_name}=NORESPONSE")
    log "  $line"
    if ! echo "$line" | grep -q "=OK"; then
      PLUGIN_MISSING="$PLUGIN_MISSING $plugin_name"
    fi
  done
  if [ -n "$PLUGIN_MISSING" ]; then
    warn "다음 플러그인이 로드되지 않음:$PLUGIN_MISSING"
    warn "  → /createItem / credentials 등록이 500 에러로 실패할 수 있음"
    warn "  → 수동 복구: Jenkins 관리 → Plugins 에서 설치 후 재시작"
  else
    ok "필수 플러그인 4개 모두 로드/활성화 확인"
  fi

  # ───────────────────────────────────────────────────────────────────────────
  # 5-2. Credentials 등록
  #      StringCredentialsImpl 은 plain-credentials 플러그인 소속이므로
  #      클래스 경로는 org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
  #      (com.cloudbees... 에는 해당 클래스가 없음 — 이전 버전은 전량 실패)
  # ───────────────────────────────────────────────────────────────────────────
  if [ -n "$DIFY_API_KEY" ]; then
    log "5-2. Credentials 등록: dify-qa-api-token (StringCredentialsImpl)"
    CRED_XML="<org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl plugin=\"plain-credentials\">
  <scope>GLOBAL</scope>
  <id>dify-qa-api-token</id>
  <description>Dify QA Chatflow API Key (setup.sh auto-registered)</description>
  <secret>${DIFY_API_KEY}</secret>
</org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl>"

    CRED_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -X POST \
        "${JENKINS_URL}/credentials/store/system/domain/_/createCredentials" \
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
        -H "Content-Type: application/xml" \
        --data "$CRED_XML" 2>&1 || echo "HTTP:000")
    debug "credentials response: $CRED_RESP"
    if echo "$CRED_RESP" | grep -qE 'HTTP:(200|302)'; then
      ok "Credentials 등록 완료 (id: dify-qa-api-token)"
    else
      warn "Credentials 등록 실패"
      warn "  응답: $CRED_RESP"
      warn "  → 수동 등록 필요 (GUIDE.md §8.3)"
    fi
  else
    warn "5-2. Dify API Key 없음 → Credentials 수동 등록 필요 (GUIDE.md §8.3)"
  fi

  ok "5-3. CSP 완화: docker-compose.override.yaml JAVA_OPTS 에 이미 영구 적용됨"

  # ───────────────────────────────────────────────────────────────────────────
  # 5-4. Pipeline Job 생성
  # ───────────────────────────────────────────────────────────────────────────
  log "5-4. Pipeline Job 'DSCORE-ZeroTouch-QA-Docker' 생성 시도"
  JOB_XML=$(python3 - << PYEOF 2>/dev/null
import xml.sax.saxutils as sax
with open('${SCRIPT_DIR}/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline', encoding='utf-8') as f:
    script = f.read()
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
</flow-definition>""".format(sax.escape(script)))
PYEOF
)

  if [ -z "$JOB_XML" ]; then
    warn "Job XML 생성 실패 — Pipeline Job 수동 생성 필요 (GUIDE.md §8.5)"
  else
    JOB_RESP=$(printf '%s' "$JOB_XML" | curl -sS -w $'\nHTTP:%{http_code}' -X POST \
        "${JENKINS_URL}/createItem?name=DSCORE-ZeroTouch-QA-Docker" \
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
        -H "Content-Type: application/xml" \
        --data-binary @- 2>&1 || echo "HTTP:000")
    debug "job create response: $JOB_RESP"
    if echo "$JOB_RESP" | grep -qE 'HTTP:(200|302)'; then
      ok "Pipeline Job 생성 완료"
    elif echo "$JOB_RESP" | grep -qE 'HTTP:400' && echo "$JOB_RESP" | grep -qi 'already exists'; then
      ok "Pipeline Job 이미 존재 — 건너뜀"
    else
      warn "Pipeline Job 생성 실패"
      warn "  응답: $JOB_RESP"
      warn "  → GUIDE.md §8.5 참조"
    fi
  fi

  # ───────────────────────────────────────────────────────────────────────────
  # 5-5. mac-ui-tester 노드 등록 (/scriptText 로 Groovy 실행)
  # ───────────────────────────────────────────────────────────────────────────
  log "5-5. mac-ui-tester 에이전트 노드 등록"
  NODE_GROOVY=$(cat << GROOVY_EOF
import jenkins.model.*
import hudson.model.*
import hudson.slaves.*

def instance = Jenkins.getInstance()
if (instance.getNode('mac-ui-tester') != null) {
    println "[node] mac-ui-tester 이미 존재 — 건너뜀"
    return
}
def launcher = new JNLPLauncher()
def node = new DumbSlave(
    "mac-ui-tester",
    "Playwright E2E Test Agent",
    "/home/jenkins-agent",
    "2",
    Node.Mode.NORMAL,
    "mac-ui-tester",
    launcher,
    new RetentionStrategy.Always(),
    new java.util.ArrayList()
)
def envEntry = new EnvironmentVariablesNodeProperty.Entry("SCRIPTS_HOME", "${SCRIPTS_HOME}")
def envProp = new EnvironmentVariablesNodeProperty([envEntry])
node.nodeProperties.add(envProp)
instance.addNode(node)
instance.save()
println "[node] mac-ui-tester 등록 완료 (SCRIPTS_HOME=${SCRIPTS_HOME})"
GROOVY_EOF
)

  NODE_RESP=$(curl -sS -w $'\nHTTP:%{http_code}' -X POST "${JENKINS_URL}/scriptText" \
      -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" \
      --data-urlencode "script=${NODE_GROOVY}" 2>&1 || echo "HTTP:000")
  debug "node register response: $NODE_RESP"
  if echo "$NODE_RESP" | grep -qE 'HTTP:(200|302)'; then
    ok "mac-ui-tester 노드 등록 완료 (SCRIPTS_HOME: ${SCRIPTS_HOME})"
  else
    warn "노드 등록 실패"
    warn "  응답: $NODE_RESP"
    warn "  → GUIDE.md §8.6 Step 1 참조"
  fi
fi

phase_end

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: 완료 요약
# ─────────────────────────────────────────────────────────────────────────────
_total=$((SECONDS - _global_t0))

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
echo "  DSCORE Zero-Touch QA 스택 설치 완료 (총 ${_total}초)"
echo "================================================================="
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📋 로그인 계정 정보 (이 값으로 영구 가입됨 — 다음에도 동일)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Jenkins   → http://localhost:18080/login"
echo "             ID       : ${JENKINS_ADMIN_USER}"
echo "             Password : ${JENKINS_ADMIN_PW}"
if [ "$_USING_DEFAULT_JENKINS_PW" = "1" ]; then
  echo "             ⚠ 기본값 사용 중 — .env 에서 JENKINS_ADMIN_PW 변경 권장"
fi
echo ""
echo "  Dify 콘솔 → http://localhost:18081/signin"
echo "             Email    : ${DIFY_EMAIL}"
echo "             Password : ${DIFY_PASSWORD}"
if [ "$_USING_DEFAULT_DIFY_PW" = "1" ]; then
  echo "             ⚠ 기본값 사용 중 — .env 에서 DIFY_PASSWORD 변경 권장"
fi
if [ -n "$DIFY_API_KEY" ]; then
  echo "             API Key  : ${DIFY_API_KEY}"
  echo "             → Jenkins Credentials 'dify-qa-api-token' 에 자동 등록됨"
else
  echo "             API Key  : (수동 발급 필요 — 아래 수동 작업 3 참조)"
fi
echo ""
if [ "$_USING_DEFAULT_DIFY_PW" = "1" ] || [ "$_USING_DEFAULT_JENKINS_PW" = "1" ]; then
  echo "  💡 비밀번호를 바꾸려면:"
  echo "       1) docker compose down -v   # 볼륨 포함 완전 초기화"
  echo "       2) cp .env.example .env (없으면) → .env 안의 값 수정"
  echo "       3) ./setup.sh 재실행"
  echo "     계정 정보는 Postgres / jenkins_home 볼륨에 저장되므로,"
  echo "     down -v 없이 .env 만 바꾸면 변경되지 않는다."
  echo ""
fi
echo "  로그 파일  → ${SETUP_LOG}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [필수 수동 작업 — Dify 1.x 플러그인 기반 아키텍처]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. Dify 콘솔에서 Ollama 플러그인 설치:"
echo "     http://localhost:18081/signin → 설정 → 플러그인 → 마켓플레이스"
echo "     → 'Ollama' 검색 → Install (plugin_daemon 가 venv 초기화, ~30초)"
echo ""
echo "  2. 설정 → 모델 공급자 → Ollama 카드 → + Add Model"
if [ "$OLLAMA_PROFILE" = "container" ]; then
echo "     Base URL   : http://ollama:11434"
else
echo "     Base URL   : http://host.docker.internal:11434"
fi
echo "     Model Name : ${OLLAMA_MODEL}"
echo ""
if [ -z "$DIFY_API_KEY" ]; then
echo "  3. (Chatflow import 실패 시) Dify 콘솔 → 앱 → + 앱 만들기"
echo "     → DSL 파일에서 가져오기 → ${SCRIPT_DIR}/dify-chatflow.yaml"
echo "     → 게시(Publish) → API 접근 → + 새 API Key → 발급된 키를"
echo "       Jenkins Credentials 'dify-qa-api-token' 로 수동 등록"
echo ""
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  [에이전트 머신 연결 — Playwright 실행 환경]"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  4. Python + Playwright 설치 (에이전트 머신):"
echo "     pip3 install playwright && playwright install chromium"
echo ""
echo "  5. agent.jar 다운로드 및 연결:"
echo ""
echo "     curl -O http://localhost:18080/jnlpJars/agent.jar"
echo "     java -jar agent.jar \\"
echo "       -url \"http://localhost:18080\" \\"
echo "       -secret \"${NODE_SECRET}\" \\"
echo "       -name \"mac-ui-tester\" \\"
echo "       -workDir \"\${HOME}/jenkins-agent\""
echo ""
echo "  6. Jenkins UI → 노드 관리 → mac-ui-tester 상태가 'Connected' 로 바뀌면 완료."
echo ""
echo "  7. Jenkins → DSCORE-ZeroTouch-QA-Docker → Build with Parameters 실행"
echo ""
echo "================================================================="
