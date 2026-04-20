#!/usr/bin/env bash
# ============================================================================
# wsl-agent-setup.sh — WSL2 Ubuntu 에서 Jenkins agent + Playwright 환경 구성
#
# mac-agent-setup.sh 의 WSL2 버전. Windows 11 의 하이브리드 토폴로지는:
#
#   Windows 네이티브: Ollama (GPU CUDA 직접) — 본 스크립트와 별개로 사용자가 설치
#   WSL2 Ubuntu     : 본 스크립트가 구성하는 Jenkins agent + Playwright
#   컨테이너         : Jenkins master + Dify → host.docker.internal:11434 로
#                     Docker Desktop 을 거쳐 Windows Ollama 에 도달
#   Chromium 창      : WSLg 를 경유해 Windows 데스크탑에 headed 표시
#
# 즉 agent 는 Ollama 를 직접 호출하지 않는다 (추론은 컨테이너 몫). 본 스크립트
# 1단계의 Ollama 체크는 **사용자 편의용 사전 확인**이며 실패해도 진행한다.
#
# 일반 Linux 서버에서도 동일하게 동작한다 — 이 경우 Ollama 가 같은 호스트에
# 설치됐다면 127.0.0.1:11434 로 잡히고, headed 창은 X server (:0) 필요.
#
# 역할 (7 단계, idempotent):
#   1) 호스트 Ollama 도달성 확인 (정보성, 실패 시 warn 만)
#   2) JDK 21 확인/설치
#   3) Python 3.11+ 확인/설치
#   4) venv 생성 + Playwright Chromium 호스트 설치 (WSLg/X 로 창 표시)
#   5) Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크
#   6) Jenkins controller 에서 agent.jar 다운로드
#   7) 기동 스크립트 생성 + 포그라운드 agent 연결
#
# 기본 실행 (이미 JDK 21 / python 3.11+ 설치된 환경):
#     NODE_SECRET=<64자> ./offline/wsl-agent-setup.sh
#
# 의존성 자동 설치 (sudo 필요; apt install openjdk-21 / python3.12):
#     NODE_SECRET=<64자> AUTO_INSTALL_DEPS=true ./offline/wsl-agent-setup.sh
#
# 환경변수:
#   OLLAMA_PING_URL  - 호스트 Ollama 확인 URL 강제 지정 (기본: 자동 탐색)
#   OLLAMA_MODEL     - 존재 확인 대상 모델 (정보성, 기본 gemma4:e4b)
#
# 재실행은 idempotent — 이미 설치된 것은 스킵.
# ============================================================================
set -euo pipefail

AGENT_DIR="${WSL_AGENT_WORKDIR:-$HOME/.dscore.ttc.playwright-agent}"
JENKINS_URL="${JENKINS_URL:-http://localhost:18080}"
AGENT_NAME="${AGENT_NAME:-mac-ui-tester}"
PY_VERSION_MIN="${PY_VERSION_MIN:-3.11}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"
AUTO_INSTALL_DEPS="${AUTO_INSTALL_DEPS:-false}"
# NODE_SECRET 자동 추출 시 읽을 컨테이너 (docker logs) — 이름 override 가능
CONTAINER_NAME="${CONTAINER_NAME:-dscore.ttc.playwright}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"     # e2e-pipeline/

log()  { printf '[wsl-agent-setup] %s\n' "$*"; }
err()  { printf '[wsl-agent-setup] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[wsl-agent-setup] WARN:  %s\n' "$*" >&2; }

# ── 0. 사전 검증 ────────────────────────────────────────────────────────────
[[ "$(uname -s)" == "Linux" ]] || err "Linux / WSL2 전용 스크립트. 현재 OS: $(uname -s) (Mac 은 mac-agent-setup.sh 사용)"

# WSL2 감지 (정보성 — 순수 Linux 서버에서도 동작)
if grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
  IS_WSL=true
  log "WSL2 감지됨 — headed Chromium 은 WSLg 경유로 Windows 데스크탑에 표시됩니다"
else
  IS_WSL=false
  log "일반 Linux — headed 창은 X server ($DISPLAY) 가 있어야 표시됩니다"
fi

# ── 0-A. 기존 세션 정리 — "빌드/재배포 시마다 깨끗하게" 보장 ──────────────────
# 이전 run 에서 남은 agent.jar 프로세스가 살아있으면 새 NODE_SECRET 으로 연결할 때
# Jenkins master 가 "already connected" 로 거부한다. 또 여러 번 실행하면 중복 인스턴스가
# 쌓여 리소스 낭비. 시작 시점에 먼저 전부 정리한다.
#
# 자기 자신 필터링: bash `$(...)` 는 subshell 을 띄우는데 그 subshell 은 부모
# 스크립트의 cmdline 을 그대로 상속 → pgrep -f "wsl-agent-setup.sh" 에 같이 잡힌다.
# MY_PID/PPID 만으론 이 subshell 을 걸러낼 수 없으므로 **session id (SID) 기준**으로
# 같은 세션의 프로세스는 전부 제외한다. setsid 로 분리 기동된 이전 인스턴스는
# 다른 SID 를 가지므로 정확히 타겟팅됨.
MY_PID=$$
MY_SID=$({ ps -o sid= -p "$MY_PID" 2>/dev/null || true; } | tr -d ' ')
[ -z "$MY_SID" ] && MY_SID="$MY_PID"
log "[0-A] 기존 agent / setup 프로세스 정리 (my_sid=$MY_SID)"

# agent.jar 프로세스 (현재 $AGENT_NAME 기준 — 다른 세션 포함 모두)
EXISTING_AGENT_PIDS=$(pgrep -f "agent.jar.*-name $AGENT_NAME" 2>/dev/null || true)
if [ -n "$EXISTING_AGENT_PIDS" ]; then
  log "  기존 agent.jar 감지 (pid: $(echo "$EXISTING_AGENT_PIDS" | tr '\n' ' ')) — 종료"
  kill $EXISTING_AGENT_PIDS 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    pgrep -f "agent.jar.*-name $AGENT_NAME" >/dev/null 2>&1 || break
    sleep 1
  done
  STILL=$(pgrep -f "agent.jar.*-name $AGENT_NAME" 2>/dev/null || true)
  [ -n "$STILL" ] && kill -9 $STILL 2>/dev/null || true
fi

# 다른 wsl-agent-setup.sh 인스턴스 (자기 세션 제외). ps/pgrep 실패를 set -e 가
# 스크립트 전체 종료로 연결하지 않도록 각 단계마다 `|| true` 로 묶는다.
OTHER_SETUP_PIDS=""
_setup_pids=$(pgrep -f "wsl-agent-setup.sh" 2>/dev/null || true)
for _pid in $_setup_pids; do
  _sid=$({ ps -o sid= -p "$_pid" 2>/dev/null || true; } | tr -d ' ')
  if [ -n "$_sid" ] && [ "$_sid" != "$MY_SID" ]; then
    OTHER_SETUP_PIDS="$OTHER_SETUP_PIDS $_pid"
  fi
done
# 양쪽 공백 trim (xargs 없이 bash 내장으로)
OTHER_SETUP_PIDS="${OTHER_SETUP_PIDS# }"
OTHER_SETUP_PIDS="${OTHER_SETUP_PIDS% }"
if [ -n "$OTHER_SETUP_PIDS" ]; then
  log "  이전 wsl-agent-setup.sh 인스턴스 감지 (pid: $OTHER_SETUP_PIDS) — 종료"
  kill $OTHER_SETUP_PIDS 2>/dev/null || true
  sleep 1
  # 여전히 살아있는 것만 SIGKILL (PID 지정 — pkill -f 로 자기 자신까지 죽이는 사고 방지)
  for _pid in $OTHER_SETUP_PIDS; do
    kill -0 "$_pid" 2>/dev/null && kill -9 "$_pid" 2>/dev/null || true
  done
fi

# Jenkins 가 기존 연결을 disconnect 로 인지할 때까지 대기 — **실제로 kill 한 게
# 있을 때만**. 처음부터 깨끗한 상태 (kill 대상 0개) 면 즉시 통과.
if [ -n "$EXISTING_AGENT_PIDS" ] || [ -n "$OTHER_SETUP_PIDS" ]; then
  log "  Jenkins 가 disconnect 를 인지할 때까지 대기 (최대 15s)"
  for _ in 1 2 3 4 5; do
    if curl -fsS --max-time 2 -u admin:password "$JENKINS_URL/computer/$AGENT_NAME/api/json" 2>/dev/null \
        | grep -q '"offline":true'; then
      break
    fi
    sleep 1
  done
fi
log "  정리 완료"

# ── 0-B. NODE_SECRET 자동 추출 (env 미지정 시 docker logs 에서) ──────────────
if [ -z "${NODE_SECRET:-}" ]; then
  log "[0-B] NODE_SECRET 미지정 — docker logs '$CONTAINER_NAME' 에서 자동 추출 시도"
  if command -v docker >/dev/null 2>&1 && \
     docker ps --filter "name=^${CONTAINER_NAME}$" --format '{{.Names}}' | grep -q .; then
    NODE_SECRET=$(docker logs "$CONTAINER_NAME" 2>&1 \
      | grep -oE 'NODE_SECRET: [a-f0-9]{64}' \
      | tail -n1 \
      | awk '{print $2}' || true)
  fi
  if [ -z "${NODE_SECRET:-}" ]; then
    cat >&2 <<EOT
[wsl-agent-setup] ERROR: NODE_SECRET 을 찾을 수 없습니다.

  자동 추출 실패 원인 (아래 중 하나):
    - 컨테이너 '$CONTAINER_NAME' 이 기동되어 있지 않음
    - 프로비저닝이 아직 완료되지 않음 (NODE_SECRET 로그 라인이 아직 안 찍힘)
    - 컨테이너 이름이 다름 — CONTAINER_NAME env 로 override

  수동 지정:
    NODE_SECRET=<64자 hex> ./offline/wsl-agent-setup.sh

  또는 Jenkins REST 로 직접 추출:
    NODE_SECRET=\$(curl -sS -u admin:password \\
      "$JENKINS_URL/computer/$AGENT_NAME/slave-agent.jnlp" \\
      | sed -n 's/.*<argument>\\([a-f0-9]\\{64\\}\\)<\\/argument>.*/\\1/p' | head -n1)
    NODE_SECRET=\$NODE_SECRET ./offline/wsl-agent-setup.sh
EOT
    exit 1
  fi
  export NODE_SECRET
  log "  NODE_SECRET 자동 추출 완료: ${NODE_SECRET:0:16}..."
fi

command -v curl >/dev/null || err "curl 필요 (apt install curl)"

# AUTO_INSTALL_DEPS=true 일 때는 sudo 가 필요 (apt 설치).
if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    err "AUTO_INSTALL_DEPS=true 지만 sudo 미설치 — 의존성 자동 설치 불가. 수동 설치 후 AUTO_INSTALL_DEPS 없이 재실행."
  fi
  if ! command -v apt >/dev/null 2>&1; then
    warn "apt 가 없습니다 (Debian/Ubuntu 계열이 아님). 의존성은 수동으로 설치해주세요."
    AUTO_INSTALL_DEPS=false
  fi
fi

log "작업 디렉토리: $AGENT_DIR"
log "AUTO_INSTALL_DEPS=$AUTO_INSTALL_DEPS  OLLAMA_MODEL=$OLLAMA_MODEL"
mkdir -p "$AGENT_DIR"

# ── 1. 호스트 Ollama 도달성 확인 (정보성) ──────────────────────────────────
# 이 설계에서 Ollama 는 **Windows 네이티브** 에 설치되어 있고 (GPU 직접 CUDA),
# Docker 컨테이너는 `host.docker.internal:11434` 로 Docker Desktop 을 거쳐
# Windows Ollama 에 접근한다. WSL agent 자체는 Ollama 를 호출하지 않는다
# (추론은 컨테이너 내 Dify 의 책임). 이 단계는 **호스트 Ollama 기동 여부
# 사전 확인**만 수행하고, 실패해도 치명적 에러가 아니다 — 컨테이너 쪽 경로가
# 독립적으로 열려있다면 Pipeline 은 동작한다.
log "[1/7] 호스트 Ollama 도달성 확인 (정보성 — 실제 호출은 컨테이너 → host.docker.internal)"

# Windows Ollama 는 기본적으로 127.0.0.1 에만 바인드되어 WSL 에서 직접
# 보이지 않는다. 사용자가 `OLLAMA_HOST=0.0.0.0` 으로 바인드를 열어둔 경우에만
# 여기서 성공. 열지 않았어도 Docker Desktop 은 여전히 host.docker.internal 로
# 컨테이너 → Windows localhost:11434 를 포워딩하므로 전체 파이프라인은 동작.
OLLAMA_PING_URL="${OLLAMA_PING_URL:-}"
if [ -z "$OLLAMA_PING_URL" ]; then
  # 자동 탐색: WSL default gateway (Windows 호스트) 와 loopback 둘 다 시도
  WIN_HOST=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')
  CANDIDATES="http://127.0.0.1:11434 http://${WIN_HOST:-127.0.0.1}:11434 http://host.docker.internal:11434"
else
  CANDIDATES="$OLLAMA_PING_URL"
fi

OLLAMA_REACHABLE=""
OLLAMA_MODELS_JSON=""
for url in $CANDIDATES; do
  if OLLAMA_MODELS_JSON=$(curl -fsS --max-time 2 "$url/api/tags" 2>/dev/null); then
    OLLAMA_REACHABLE="$url"
    break
  fi
done

if [ -n "$OLLAMA_REACHABLE" ]; then
  log "  호스트 Ollama 도달 가능: $OLLAMA_REACHABLE"
  if command -v python3 >/dev/null 2>&1; then
    HAS_MODEL=$(printf '%s' "$OLLAMA_MODELS_JSON" \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if any(m.get('name','')==sys.argv[1] or m.get('name','').split(':')[0]==sys.argv[1].split(':')[0] for m in d.get('models',[])) else 'no')" \
        "$OLLAMA_MODEL" 2>/dev/null || echo "unknown")
    case "$HAS_MODEL" in
      yes) log "  모델 $OLLAMA_MODEL (또는 동일 family) 존재 확인" ;;
      no)  warn "  모델 $OLLAMA_MODEL 이 Ollama 에 없습니다 — Windows 쪽에서 'ollama pull $OLLAMA_MODEL' 실행 필요"
           warn "  (Pipeline 실행 시 Dify Stage 3 에서 ConnectionError 가 납니다)" ;;
      *)   : ;;
    esac
  fi
else
  warn "  호스트 Ollama 에 WSL 에서 도달 불가 ($CANDIDATES 모두 실패)"
  warn "  → Windows Ollama 가 기본 127.0.0.1 바인드면 WSL 에서 안 보이는 게 정상입니다."
  warn "  → 컨테이너 쪽은 Docker Desktop 이 host.docker.internal → Windows localhost:11434 로 포워딩하므로 Pipeline 은 동작할 수 있습니다."
  warn "  → Windows 쪽에서 'ollama list' 로 모델 존재 여부를 수동 확인해주세요."
  warn "  → WSL 에서도 보고 싶다면 Windows User env OLLAMA_HOST=0.0.0.0 설정 후 Ollama 재기동."
fi

# GPU 가시성 점검 (정보성) — Windows 네이티브 Ollama 는 Windows 드라이버의
# CUDA 를 직접 쓰므로 WSL 안 nvidia-smi 여부와는 무관. 그냥 참고용.
if command -v nvidia-smi >/dev/null 2>&1; then
  if GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1); then
    [ -n "$GPU_NAME" ] && log "  WSL2 nvidia-smi 감지: $GPU_NAME (Windows 드라이버 WSL2 노출 OK)"
  fi
fi

# ── 2. JDK 21 확인/설치 ────────────────────────────────────────────────────
# Jenkins 2.479+ 및 remoting 3355.v 는 Java 21 로 컴파일된 bytecode 를 에이전트에
# 다운로드하므로, agent 쪽 JDK 가 21 미만이면 연결 직후 UnsupportedClassVersionError
# 로 offline 상태에 머문다.
log "[2/7] JDK 21 확인"

detect_java21() {
  local cand
  for cand in \
      "/usr/lib/jvm/temurin-21-jdk-amd64/bin/java" \
      "/usr/lib/jvm/temurin-21-jdk-arm64/bin/java" \
      "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" \
      "/usr/lib/jvm/java-21-openjdk-arm64/bin/java" \
      "/usr/lib/jvm/openjdk-21/bin/java" \
    ; do
    if [ -x "$cand" ]; then echo "$cand"; return 0; fi
  done
  # update-alternatives 로 선택된 java 가 21 이면 수용
  if command -v java >/dev/null 2>&1 && java -version 2>&1 | head -1 | grep -qE 'version "21'; then
    command -v java
    return 0
  fi
  return 1
}

JAVA_BIN="$(detect_java21 || true)"
if [ -z "$JAVA_BIN" ]; then
  if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  JDK 21 미설치 — apt install openjdk-21-jdk-headless"
    sudo apt-get update
    sudo apt-get install -y openjdk-21-jdk-headless
    JAVA_BIN="$(detect_java21 || true)"
    [ -z "$JAVA_BIN" ] && err "openjdk-21 설치 후에도 JDK 21 바이너리 탐색 실패"
  else
    cat >&2 <<'EOT'
[wsl-agent-setup] ERROR: JDK 21 이 설치되지 않았습니다.

Jenkins 2.479+ (현재 이미지) 는 Java 21 bytecode 를 에이전트에 전송하므로
JDK 21 미만은 UnsupportedClassVersionError 로 즉시 연결 실패합니다.

설치 (Ubuntu 22.04+ 는 apt 에 openjdk-21 포함):
  sudo apt update && sudo apt install -y openjdk-21-jdk-headless

Temurin (Eclipse Adoptium) apt repo 가 필요하면:
  sudo apt install -y wget apt-transport-https
  wget -O - https://packages.adoptium.net/artifactory/api/gpg/key/public | sudo tee /etc/apt/trusted.gpg.d/adoptium.asc
  echo "deb https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/adoptium.list
  sudo apt update && sudo apt install -y temurin-21-jdk

자동 설치 (이 스크립트가 apt install 수행):
  AUTO_INSTALL_DEPS=true NODE_SECRET=... ./offline/wsl-agent-setup.sh
EOT
    exit 2
  fi
fi
export JAVA_BIN
log "  OK: $JAVA_BIN"
log "  $("$JAVA_BIN" -version 2>&1 | head -1)"

# ── 3. Python 3.11+ 확인/설치 ──────────────────────────────────────────────
log "[3/7] Python $PY_VERSION_MIN+ 확인"

detect_python() {
  local min_major min_minor cand
  min_major="${PY_VERSION_MIN%%.*}"
  min_minor="${PY_VERSION_MIN##*.}"
  for cand in \
      "/usr/bin/python3.12" \
      "/usr/bin/python3.11" \
      "$(command -v python3.12 2>/dev/null || true)" \
      "$(command -v python3.11 2>/dev/null || true)" \
      "$(command -v python3 2>/dev/null || true)" \
    ; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info >= ($min_major,$min_minor) else 1)" 2>/dev/null; then
      echo "$cand"; return 0
    fi
  done
  return 1
}

PY_BIN="$(detect_python || true)"
if [ -z "$PY_BIN" ]; then
  if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  python3 $PY_VERSION_MIN+ 미존재 — apt install python3.12-venv (또는 python3.11-venv)"
    sudo apt-get update
    # Ubuntu 22.04 는 python3.10 이 기본 — 3.12 는 deadsnakes PPA 필요할 수 있음.
    # 22.04 native 로는 python3.11-venv 가 가용한 경우 많음. 기본 3.10 이면 아래가 실패할 수 있어
    # deadsnakes fallback 을 순차 시도.
    if ! sudo apt-get install -y python3.12 python3.12-venv 2>/dev/null; then
      if ! sudo apt-get install -y python3.11 python3.11-venv 2>/dev/null; then
        warn "  apt 기본 repo 에 python3.11+ 가 없습니다. deadsnakes PPA 추가 시도:"
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update
        sudo apt-get install -y python3.12 python3.12-venv
      fi
    fi
    PY_BIN="$(detect_python || true)"
    [ -z "$PY_BIN" ] && err "python 설치 후에도 python3 탐색 실패"
  else
    err "python3 $PY_VERSION_MIN+ 필요. 'sudo apt install python3.12 python3.12-venv' 실행 (또는 AUTO_INSTALL_DEPS=true)"
  fi
fi
PY_VER=$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "  OK: $PY_BIN (python $PY_VER)"

# ── 4. venv + Playwright Chromium (호스트 설치 — headed 모드 핵심) ─────────
log "[4/7] venv 준비 + Playwright Chromium 호스트 설치"

VENV_DIR="$AGENT_DIR/venv"
venv_ok() {
  [ -x "$VENV_DIR/bin/python3" ] || return 1
  "$VENV_DIR/bin/python3" -c "import sys; assert sys.prefix == '$VENV_DIR'" 2>/dev/null || return 1
  "$VENV_DIR/bin/python3" -m pip --version >/dev/null 2>&1 || return 1
  [ -x "$VENV_DIR/bin/pip" ] && "$VENV_DIR/bin/pip" --version >/dev/null 2>&1
}
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  log "  venv 생성: $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
elif ! venv_ok; then
  log "  venv 무결성 손상 감지 — 재생성"
  rm -rf "$VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
else
  log "  venv 이미 존재 — 스킵"
fi

VENV_PY="$VENV_DIR/bin/python3"
"$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
REQ_PKGS=(requests playwright pillow)
log "  pip install: ${REQ_PKGS[*]}"
"$VENV_PY" -m pip install --quiet "${REQ_PKGS[@]}"

# Playwright 캐시는 ~/.cache/ms-playwright/ (Linux 표준)
if ls -d "$HOME/.cache/ms-playwright/chromium-"* >/dev/null 2>&1; then
  log "  Chromium 이미 설치됨 — 스킵"
else
  # playwright install --with-deps 는 sudo 가 필요 — 여기선 의존 deb 은 사용자가 사전에 깔아둔다고 가정
  # Chromium 자체만 설치. Ubuntu 22.04 기본 이미지에 대부분 GTK/NSS/fonts 가 있음.
  "$VENV_PY" -m playwright install chromium
fi

# WSLg 경유 headed 창 사전 체크 (정보성)
if [ "$IS_WSL" = "true" ]; then
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    warn "  WSL2 인데 DISPLAY / WAYLAND_DISPLAY 가 비어있음 — WSLg 미활성 가능성"
    warn "  Windows 11 은 WSLg 기본 탑재. 'wsl --update' 로 WSL 커널 업데이트 권장"
  else
    log "  WSLg 활성화됨 (DISPLAY=${DISPLAY:-unset} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset}) — Chromium 창이 Windows 데스크탑에 뜰 예정"
  fi
fi

# ── 5. Jenkins Node remoteFS 절대경로 + workspace venv 심볼릭 링크 사전 준비 ──
#
# provision-apps.sh 는 Node 생성 시 remoteFS 를 "~/.dscore.ttc.playwright-agent" 로 설정했지만
# Jenkins remoting 이 `~` 를 expansion 하지 않아 workspace 가
# /absolute/.dscore.ttc.playwright-agent/~/.dscore.ttc.playwright-agent/workspace 로 꼬인다. 여기서
# 호스트 쪽 실제 홈 경로를 알고 있으므로 Groovy 로 Node config.xml 을 절대경로로 갱신.
#
# Pipeline Stage 1 이 ${WORKSPACE}/.qa_home/venv/bin/activate 를 요구하므로,
# 우리가 만든 AGENT_DIR/venv 를 해당 워크스페이스에 미리 심볼릭 링크.
ABS_WORKSPACE="$AGENT_DIR/workspace/DSCORE-ZeroTouch-QA-Docker"
log "[5/7] Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크"

# PoC 2026-04-20: Jenkins 2.555 의 /crumbIssuer/api/json 은 404 HTML 을 반환함 (엔드포인트 자체 부재).
# crumb 없이도 basic auth 요청이면 POST 가 통과되므로, 파싱 실패 시 empty 로 두고 warn 후 진행.
CRUMB=$(curl -sS -u admin:password "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null \
  || true)
if [ -z "$CRUMB" ]; then
  warn "Jenkins crumb 획득 실패 (2.555+ 는 /crumbIssuer 미제공) — basic auth 만으로 진행"
fi
GROOVY_UPDATE=$(cat <<GROOVY
import jenkins.model.Jenkins
def n = Jenkins.get().getNode("$AGENT_NAME")
if (n == null) { println "ERR: Node '$AGENT_NAME' 없음"; return }
def f = n.getClass().getSuperclass().getDeclaredField("remoteFS")
f.setAccessible(true)
f.set(n, "$AGENT_DIR")
Jenkins.get().updateNode(n)
println "OK remoteFS=" + n.getRemoteFS()
GROOVY
)
UPDATE_RESP=$(curl -sS -u admin:password ${CRUMB:+-H "$CRUMB"} \
    --data-urlencode "script=$GROOVY_UPDATE" \
    "$JENKINS_URL/scriptText" 2>&1)
log "  $UPDATE_RESP"

# workspace 스켈레톤 + venv 심볼릭 링크
mkdir -p "$ABS_WORKSPACE/.qa_home/artifacts"
if [ -d "$VENV_DIR/bin" ]; then
  ln -sfn "$VENV_DIR" "$ABS_WORKSPACE/.qa_home/venv"
  log "  workspace venv 링크: $ABS_WORKSPACE/.qa_home/venv → $VENV_DIR"
fi

# ── 6. agent.jar 다운로드 ──────────────────────────────────────────────────
log "[6/7] Jenkins agent.jar 다운로드: $JENKINS_URL/jnlpJars/agent.jar"
AGENT_JAR="$AGENT_DIR/agent.jar"
if [ -f "$AGENT_JAR" ] && [ "${FORCE_AGENT_DOWNLOAD:-false}" != "true" ]; then
  log "  agent.jar 이미 존재 — 스킵 (강제 재다운로드: FORCE_AGENT_DOWNLOAD=true)"
else
  curl -fL -o "$AGENT_JAR" "$JENKINS_URL/jnlpJars/agent.jar" \
    || err "agent.jar 다운로드 실패. Jenkins 가 $JENKINS_URL 에서 응답하는지 확인"
  log "  다운로드 완료: $(du -h "$AGENT_JAR" | cut -f1)"
fi

# ── 7. 기동 스크립트 생성 + agent 연결 ─────────────────────────────────────
# 기존 agent.jar 프로세스 정리는 이미 step 0-A 에서 수행됨 (스크립트 시작 시점).
# 여기선 run-agent.sh 스크립트를 써 두고 foreground 로 agent 를 기동한다.

RUN_SCRIPT="$AGENT_DIR/run-agent.sh"
cat > "$RUN_SCRIPT" <<RUN_EOF
#!/usr/bin/env bash
# 이 파일은 wsl-agent-setup.sh 가 생성한 agent 기동 스크립트입니다.
# SCRIPTS_HOME 은 e2e-pipeline 저장소 위치 (이 파일의 부모 디렉토리 기준).
# JAVA_BIN 은 JDK 21 의 절대 경로 — 시스템 PATH 에 JDK 17 이 있더라도
# agent 는 반드시 JDK 21 로 기동해야 remoting 호환성이 맞는다.
set -e
export SCRIPTS_HOME="$ROOT_DIR"
# venv PATH 주입 — Pipeline 이 python3 을 호출할 때 host Chromium 이 깔린 venv 사용
export PATH="$VENV_DIR/bin:\$PATH"
cd "$AGENT_DIR"
exec "$JAVA_BIN" -jar agent.jar \\
  -url "$JENKINS_URL" \\
  -secret "$NODE_SECRET" \\
  -name "$AGENT_NAME" \\
  -workDir "$AGENT_DIR"
RUN_EOF
chmod +x "$RUN_SCRIPT"
log "[7/7] 기동 스크립트: $RUN_SCRIPT"
log "  SCRIPTS_HOME=$ROOT_DIR"
log "  venv=$VENV_DIR"
log "  JENKINS_URL=$JENKINS_URL"
log "  AGENT_NAME=$AGENT_NAME"

log "=========================================================================="
log "설정 완료. agent 를 연결합니다 (Ctrl+C 로 종료, 재연결 시 이 스크립트 재실행)."
if [ "$IS_WSL" = "true" ]; then
  log "Pipeline 이 headed 모드로 실행되면 Windows 데스크탑에 Chromium 창이 뜹니다 (WSLg)."
fi
log "=========================================================================="
log ""
exec "$RUN_SCRIPT"
