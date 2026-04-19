#!/usr/bin/env bash
# ============================================================================
# mac-agent-setup.sh — macOS 호스트에 Jenkins agent + Playwright 환경 구성
#
# 역할 (7 단계, idempotent):
#   1) Ollama 데몬 + 모델 확인 (없으면 설치/기동/pull)
#   2) JDK 21 확인/설치
#   3) Python 3.11+ 확인/설치
#   4) venv 생성 + Playwright Chromium 호스트 설치
#   5) Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크
#   6) Jenkins controller 에서 agent.jar 다운로드
#   7) 기동 스크립트 생성 + 포그라운드 agent 연결
#
# 기본 실행 (이미 ollama/JDK 21/python 3.11+ 설치된 환경):
#     NODE_SECRET=<64자> ./offline/mac-agent-setup.sh
#
# 의존성 자동 설치 (brew 필요; ollama + 모델 pull, openjdk@21, python@3.12):
#     NODE_SECRET=<64자> AUTO_INSTALL_DEPS=true ./offline/mac-agent-setup.sh
#
# 재실행은 idempotent — 이미 설치된 것은 스킵.
# ============================================================================
set -euo pipefail

AGENT_DIR="${MAC_AGENT_WORKDIR:-$HOME/.dscore.ttc.playwright-agent}"
JENKINS_URL="${JENKINS_URL:-http://localhost:18080}"
AGENT_NAME="${AGENT_NAME:-mac-ui-tester}"
PY_VERSION_MIN="${PY_VERSION_MIN:-3.11}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"
AUTO_INSTALL_DEPS="${AUTO_INSTALL_DEPS:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"     # e2e-pipeline/

log()  { printf '[mac-agent-setup] %s\n' "$*"; }
err()  { printf '[mac-agent-setup] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[mac-agent-setup] WARN:  %s\n' "$*" >&2; }

# ── 0. 사전 검증 ────────────────────────────────────────────────────────────
[[ "$(uname -s)" == "Darwin" ]] || err "macOS 전용 스크립트. 현재 OS: $(uname -s)"

if [ -z "${NODE_SECRET:-}" ]; then
  cat >&2 <<'EOT'
[mac-agent-setup] ERROR: NODE_SECRET 환경변수가 필요합니다.

  컨테이너 로그에서 "NODE_SECRET: <64자 hex>" 줄을 찾아 아래처럼 실행하세요:
    NODE_SECRET=abcdef0123... ./offline/mac-agent-setup.sh

  또는 docker exec 로 직접 추출:
    NODE_SECRET=$(docker exec dscore.ttc.playwright curl -sS -u admin:password \
      http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp \
      | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1)
    NODE_SECRET=$NODE_SECRET ./offline/mac-agent-setup.sh
EOT
  exit 1
fi

command -v curl >/dev/null || err "curl 필요"

# AUTO_INSTALL_DEPS=true 일 때는 brew 가 반드시 있어야 함.
if [ "$AUTO_INSTALL_DEPS" = "true" ] && ! command -v brew >/dev/null 2>&1; then
  err "AUTO_INSTALL_DEPS=true 지만 Homebrew 미설치. https://brew.sh 설치 후 재시도."
fi

log "작업 디렉토리: $AGENT_DIR"
log "AUTO_INSTALL_DEPS=$AUTO_INSTALL_DEPS  OLLAMA_MODEL=$OLLAMA_MODEL"
mkdir -p "$AGENT_DIR"

# ── 1. Ollama 데몬 + 모델 ──────────────────────────────────────────────────
log "[1/7] Ollama 데몬 + 모델($OLLAMA_MODEL) 확인"

if ! command -v ollama >/dev/null 2>&1; then
  if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  ollama 미설치 — brew install ollama"
    brew install ollama
  else
    err "ollama 미설치. 'brew install ollama' 실행 (또는 AUTO_INSTALL_DEPS=true 재실행)"
  fi
fi

# 데몬 기동 여부 — API ping 으로 최종 판정. brew 로 설치된 경우 services start 시도.
if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    log "  ollama 데몬 미기동 — brew services start ollama"
    brew services start ollama >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi
  curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
    || err "ollama 데몬 기동 실패. 'brew services start ollama' 또는 Ollama.app 수동 실행 확인."
fi
log "  데몬 OK — http://127.0.0.1:11434"

# 모델 존재 여부 — ollama list 의 NAME 컬럼(첫 필드) 정확 일치
if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qxF "$OLLAMA_MODEL"; then
  log "  모델 $OLLAMA_MODEL 이미 존재 — 스킵"
else
  if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  모델 $OLLAMA_MODEL 미존재 — ollama pull (4GB 내외, 1-3분)"
    ollama pull "$OLLAMA_MODEL"
  else
    err "모델 $OLLAMA_MODEL 없음. 'ollama pull $OLLAMA_MODEL' 실행 (또는 AUTO_INSTALL_DEPS=true)"
  fi
fi

# ── 2. JDK 21 확인/설치 ────────────────────────────────────────────────────
# Jenkins 2.479+ 및 remoting 3355.v 는 Java 21 로 컴파일된 bytecode 를 에이전트에
# 다운로드하므로, agent 쪽 JDK 가 21 미만이면 연결 직후 UnsupportedClassVersionError
# 로 offline 상태에 머문다. 따라서 fail-fast 로 처리.
log "[2/7] JDK 21 확인"

detect_java21() {
  local cand home21
  for cand in \
      "/opt/homebrew/opt/openjdk@21/bin/java" \
      "/usr/local/opt/openjdk@21/bin/java" \
      "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java" \
    ; do
    if [ -x "$cand" ]; then echo "$cand"; return 0; fi
  done
  # java_home 은 요구 버전 없을 때 낮은 버전으로 fallback 하므로 엄격히 체크
  home21=$(/usr/libexec/java_home -v 21 2>/dev/null || true)
  if [ -n "$home21" ] && [ -x "$home21/bin/java" ]; then
    if "$home21/bin/java" -version 2>&1 | head -1 | grep -qE 'version "21'; then
      echo "$home21/bin/java"; return 0
    fi
  fi
  if command -v java >/dev/null 2>&1 && java -version 2>&1 | head -1 | grep -qE 'version "21'; then
    command -v java
    return 0
  fi
  return 1
}

JAVA_BIN="$(detect_java21 || true)"
if [ -z "$JAVA_BIN" ]; then
  if [ "$AUTO_INSTALL_DEPS" = "true" ]; then
    log "  JDK 21 미설치 — brew install openjdk@21 (formula, sudo 불요)"
    brew install openjdk@21
    JAVA_BIN="$(detect_java21 || true)"
    [ -z "$JAVA_BIN" ] && err "openjdk@21 설치 후에도 JDK 21 바이너리 탐색 실패"
  else
    cat >&2 <<'EOT'
[mac-agent-setup] ERROR: JDK 21 이 설치되지 않았습니다.

Jenkins 2.479+ (현재 이미지) 는 Java 21 bytecode 를 에이전트에 전송하므로
JDK 21 미만은 UnsupportedClassVersionError 로 즉시 연결 실패합니다.

설치:
  brew install openjdk@21                     # sudo 불요 (권장)
  # 또는
  brew install --cask temurin@21              # sudo 필요, GUI 메뉴에서 JDK 노출

자동 설치 (이 스크립트가 brew install 수행):
  AUTO_INSTALL_DEPS=true NODE_SECRET=... ./offline/mac-agent-setup.sh
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
      "/opt/homebrew/bin/python3.12" \
      "/opt/homebrew/bin/python3.11" \
      "/usr/local/bin/python3.12" \
      "/usr/local/bin/python3.11" \
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
    log "  python3 $PY_VERSION_MIN+ 미존재 — brew install python@3.12"
    brew install python@3.12
    PY_BIN="$(detect_python || true)"
    [ -z "$PY_BIN" ] && err "python@3.12 설치 후에도 python3 탐색 실패"
  else
    err "python3 $PY_VERSION_MIN+ 필요. 'brew install python@3.12' 실행 (또는 AUTO_INSTALL_DEPS=true)"
  fi
fi
PY_VER=$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "  OK: $PY_BIN (python $PY_VER)"

# ── 4. venv + Playwright Chromium (호스트 설치 — headed 모드 핵심) ─────────
log "[4/7] venv 준비 + Playwright Chromium 호스트 설치 (macOS 네이티브)"

VENV_DIR="$AGENT_DIR/venv"
# venv 는 pip / playwright / 기타 console_scripts 의 shebang 에 원래 생성 경로가
# 하드코딩된다 (sys.prefix 는 실행 파일 위치에서 계산되므로 OK 지만 스크립트는 깨짐).
# AGENT_DIR 가 옮겨졌거나 Python 이 교체된 경우 `pip ...` 호출이 "bad interpreter"
# 로 실패하면서 set -e 에 의해 스크립트가 조용히 죽는다 → 무결성을 확실히 검증.
venv_ok() {
  [ -x "$VENV_DIR/bin/python3" ] || return 1
  "$VENV_DIR/bin/python3" -c "import sys; assert sys.prefix == '$VENV_DIR'" 2>/dev/null || return 1
  # pip 모듈이 살아있고 또한 console script 의 shebang 도 유효해야 함
  "$VENV_DIR/bin/python3" -m pip --version >/dev/null 2>&1 || return 1
  [ -x "$VENV_DIR/bin/pip" ] && "$VENV_DIR/bin/pip" --version >/dev/null 2>&1
}
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  log "  venv 생성: $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
elif ! venv_ok; then
  log "  venv 무결성 손상 감지 (이동됐거나 Python 이 교체됨) — 재생성"
  rm -rf "$VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
else
  log "  venv 이미 존재 — 스킵"
fi

# pip / playwright 를 console script 대신 `python -m` 으로 호출 — shebang 깨짐 회피
VENV_PY="$VENV_DIR/bin/python3"
"$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1
REQ_PKGS=(requests playwright pillow)
log "  pip install: ${REQ_PKGS[*]}"
"$VENV_PY" -m pip install --quiet "${REQ_PKGS[@]}"

# playwright install 은 ~/Library/Caches/ms-playwright/ 에 Chromium 설치
if ls -d "$HOME/Library/Caches/ms-playwright/chromium-"* >/dev/null 2>&1; then
  log "  Chromium 이미 설치됨 — 스킵"
else
  "$VENV_PY" -m playwright install chromium
fi

# ── 5. Jenkins Node remoteFS 절대경로 + workspace venv 심볼릭 링크 사전 준비 ──
#
# provision-apps.sh 는 Node 생성 시 remoteFS 를 "~/.dscore.ttc.playwright-agent" 로 설정했지만
# Jenkins remoting 이 `~` 를 expansion 하지 않아 workspace 가
# /absolute/.dscore.ttc.playwright-agent/~/.dscore.ttc.playwright-agent/workspace 로 꼬인다. 여기서
# 호스트 쪽 실제 홈 경로를 알고 있으므로 Groovy 로 Node config.xml 을 절대경로로 갱신.
#
# 또한 Pipeline Stage 1 이 ${WORKSPACE}/.qa_home/venv/bin/activate 를 요구하므로,
# 우리가 만든 AGENT_DIR/venv 를 해당 워크스페이스에 미리 심볼릭 링크.
ABS_WORKSPACE="$AGENT_DIR/workspace/DSCORE-ZeroTouch-QA-Docker"
log "[5/7] Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크"

# (a) Node remoteFS 를 절대경로로 (Groovy)
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
// reflection 으로 remoteFS 갱신 (setRemoteFS 는 public 이 아닐 수 있음)
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

# (b) workspace 스켈레톤 + venv 심볼릭 링크
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
  # SIGTERM → 최대 5초 대기 → SIGKILL
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
  # Jenkins 가 disconnect 를 인지할 때까지 잠깐 대기 (최대 10초).
  # 인지 전에 새 연결 시도하면 다시 "already connected" 로 거부됨.
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
# 이 파일은 mac-agent-setup.sh 가 생성한 agent 기동 스크립트입니다.
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
log "=========================================================================="
log ""
# nohup 백그라운드가 아닌 foreground 로 — 사용자가 로그를 직접 보게 함
exec "$RUN_SCRIPT"
