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

if [ -z "${NODE_SECRET:-}" ]; then
  cat >&2 <<'EOT'
[wsl-agent-setup] ERROR: NODE_SECRET 환경변수가 필요합니다.

  컨테이너 로그에서 "NODE_SECRET: <64자 hex>" 줄을 찾아 아래처럼 실행하세요:
    NODE_SECRET=abcdef0123... ./offline/wsl-agent-setup.sh

  또는 docker exec 로 직접 추출:
    NODE_SECRET=$(docker exec dscore.ttc.playwright curl -sS -u admin:password \
      http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp \
      | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1)
    NODE_SECRET=$NODE_SECRET ./offline/wsl-agent-setup.sh
EOT
  exit 1
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
#
# 이전 run 에서 띄운 java agent.jar 프로세스가 살아있으면 Jenkins controller 가
# 동일 Node 의 새 연결을 "already connected" 로 거부한다 (ConnectionRefusalException).
# 재실행 시에는 기존 프로세스를 먼저 정리해야 깨끗하게 재연결된다.
AGENT_MATCH="agent.jar.*-name $AGENT_NAME"
EXISTING_AGENT_PIDS=$(pgrep -f "$AGENT_MATCH" 2>/dev/null || true)
if [ -n "$EXISTING_AGENT_PIDS" ]; then
  log "기존 agent 프로세스 감지 — 종료 후 재연결 (pid: $(echo "$EXISTING_AGENT_PIDS" | tr '\n' ' '))"
  kill $EXISTING_AGENT_PIDS 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    pgrep -f "$AGENT_MATCH" >/dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -f "$AGENT_MATCH" >/dev/null 2>&1; then
    log "  SIGTERM 무응답 — SIGKILL"
    pkill -9 -f "$AGENT_MATCH" 2>/dev/null || true
    sleep 1
  fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fsS -u admin:password "$JENKINS_URL/computer/$AGENT_NAME/api/json" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('offline') else 1)" 2>/dev/null
    then
      break
    fi
    sleep 1
  done
  log "  기존 프로세스 정리 완료 — Jenkins disconnect 인지됨"
fi

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
