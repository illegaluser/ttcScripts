#!/usr/bin/env bash
# ============================================================================
# mac-agent-setup.sh — macOS 호스트에 Jenkins agent + Playwright 환경 구성
#
# 역할:
#   1) JDK 21 존재 확인 (없으면 brew 안내)
#   2) Python 3.11+ venv 생성 — ~/.dscore-qa-agent/venv
#   3) Playwright + Chromium 호스트 설치 (headed 모드에서 macOS 화면에 창 뜨도록)
#   4) Jenkins controller 에서 agent.jar 다운로드
#   5) 기동 스크립트 생성 + 포그라운드 agent 연결
#
# 컨테이너 기동 후 entrypoint 로그에 찍히는 NODE_SECRET 을 env 로 넘겨 실행:
#     NODE_SECRET=<64자> ./offline/mac-agent-setup.sh
#
# 재실행은 idempotent — 이미 설치된 것은 스킵.
# ============================================================================
set -euo pipefail

AGENT_DIR="${MAC_AGENT_WORKDIR:-$HOME/.dscore-qa-agent}"
JENKINS_URL="${JENKINS_URL:-http://localhost:18080}"
AGENT_NAME="${AGENT_NAME:-mac-ui-tester}"
PY_VERSION_MIN="${PY_VERSION_MIN:-3.11}"

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
    NODE_SECRET=$(docker exec dscore-qa curl -sS -u admin:password \
      http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp \
      | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1)
    NODE_SECRET=$NODE_SECRET ./offline/mac-agent-setup.sh
EOT
  exit 1
fi

command -v curl >/dev/null || err "curl 필요"

log "작업 디렉토리: $AGENT_DIR"
mkdir -p "$AGENT_DIR"

# ── 1. JDK 21 확인 (fail-fast) ──────────────────────────────────────────────
# Jenkins 2.479+ 및 remoting 3355.v 는 Java 21 로 컴파일된 bytecode 를 에이전트에
# 다운로드하므로, agent 쪽 JDK 가 21 미만이면 연결 직후 UnsupportedClassVersionError
# 로 offline 상태에 머문다. 따라서 fail-fast 로 처리.
log "[1/5] JDK 21 확인 (fail-fast — 21 미만이면 중단)"

# 자동 탐지: /opt/homebrew/opt/openjdk@21 우선 → /usr/libexec/java_home -v21 → 시스템 java
JAVA_BIN=""
for CAND in \
    "/opt/homebrew/opt/openjdk@21/bin/java" \
    "/usr/local/opt/openjdk@21/bin/java" \
    "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java" \
  ; do
  if [ -x "$CAND" ]; then JAVA_BIN="$CAND"; break; fi
done
if [ -z "$JAVA_BIN" ]; then
  # java_home 은 요구 버전 없을 때 낮은 버전으로 fallback 하므로 엄격히 체크
  HOME21=$(/usr/libexec/java_home -v 21 2>/dev/null || true)
  if [ -n "$HOME21" ] && [ -x "$HOME21/bin/java" ]; then
    if "$HOME21/bin/java" -version 2>&1 | head -1 | grep -qE 'version "21'; then
      JAVA_BIN="$HOME21/bin/java"
    fi
  fi
fi
if [ -z "$JAVA_BIN" ] && command -v java >/dev/null 2>&1; then
  # 시스템 java 가 21 이면 허용
  if java -version 2>&1 | head -1 | grep -qE 'version "21'; then
    JAVA_BIN="$(command -v java)"
  fi
fi

if [ -z "$JAVA_BIN" ]; then
  cat >&2 <<'EOT'
[mac-agent-setup] ERROR: JDK 21 이 설치되지 않았습니다.

Jenkins 2.479+ (현재 이미지) 는 Java 21 bytecode 를 에이전트에 전송하므로
JDK 21 미만은 UnsupportedClassVersionError 로 즉시 연결 실패합니다.

설치:
  brew install openjdk@21                     # sudo 불요 (권장)
  # 또는
  brew install --cask temurin@21              # sudo 필요, GUI 메뉴에서 JDK 노출

설치 후 이 스크립트 재실행.
EOT
  exit 2
fi
export JAVA_BIN
log "  OK: $JAVA_BIN"
log "  $("$JAVA_BIN" -version 2>&1 | head -1)"

# ── 2. Python 3.11+ 및 venv ────────────────────────────────────────────────
log "[2/5] Python $PY_VERSION_MIN+ venv 준비"
PY_BIN="$(command -v python3 2>/dev/null || true)"
[ -z "$PY_BIN" ] && err "python3 미설치. 설치: brew install python@3.12"

PY_VER=$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
log "  시스템 python3: $PY_VER ($PY_BIN)"

VENV_DIR="$AGENT_DIR/venv"
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  log "  venv 생성: $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
else
  log "  venv 이미 존재 — 스킵"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
pip install --upgrade pip >/dev/null 2>&1
REQ_PKGS=(requests playwright pillow)
log "  pip install: ${REQ_PKGS[*]}"
pip install --quiet "${REQ_PKGS[@]}"

# ── 3. Playwright Chromium (호스트 설치 — headed 모드 핵심) ────────────────
log "[3/5] Playwright Chromium 호스트 설치 (macOS 네이티브)"
# playwright install 은 ~/Library/Caches/ms-playwright/ 에 Chromium 설치
if [ -d "$HOME/Library/Caches/ms-playwright/chromium-"* ] 2>/dev/null; then
  log "  이미 설치된 Chromium 감지 — 스킵"
else
  python -m playwright install chromium
fi

# ── 3-1. Jenkins Node remoteFS 절대경로 + workspace venv 심볼릭 링크 사전 준비 ──
#
# provision-apps.sh 는 Node 생성 시 remoteFS 를 "~/.dscore-qa-agent" 로 설정했지만
# Jenkins remoting 이 `~` 를 expansion 하지 않아 workspace 가
# /absolute/.dscore-qa-agent/~/.dscore-qa-agent/workspace 로 꼬인다. 여기서
# 호스트 쪽 실제 홈 경로를 알고 있으므로 Groovy 로 Node config.xml 을 절대경로로 갱신.
#
# 또한 Pipeline Stage 1 이 ${WORKSPACE}/.qa_home/venv/bin/activate 를 요구하므로,
# 우리가 만든 AGENT_DIR/venv 를 해당 워크스페이스에 미리 심볼릭 링크.
ABS_WORKSPACE="$AGENT_DIR/workspace/DSCORE-ZeroTouch-QA-Docker"
log "[3b] Jenkins Node remoteFS 절대경로 갱신 + workspace venv 사전 링크"

# (a) Node remoteFS 를 절대경로로 (Groovy)
CRUMB=$(curl -sS -u admin:password "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null)
if [ -z "$CRUMB" ]; then
  err "Jenkins crumb 획득 실패. $JENKINS_URL 응답 확인."
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
UPDATE_RESP=$(curl -sS -u admin:password -H "$CRUMB" \
    --data-urlencode "script=$GROOVY_UPDATE" \
    "$JENKINS_URL/scriptText" 2>&1)
log "  $UPDATE_RESP"

# (b) workspace 스켈레톤 + venv 심볼릭 링크
mkdir -p "$ABS_WORKSPACE/.qa_home/artifacts"
if [ -d "$VENV_DIR/bin" ]; then
  ln -sfn "$VENV_DIR" "$ABS_WORKSPACE/.qa_home/venv"
  log "  workspace venv 링크: $ABS_WORKSPACE/.qa_home/venv → $VENV_DIR"
fi

# ── 4. agent.jar 다운로드 ──────────────────────────────────────────────────
log "[4/5] Jenkins agent.jar 다운로드: $JENKINS_URL/jnlpJars/agent.jar"
AGENT_JAR="$AGENT_DIR/agent.jar"
if [ -f "$AGENT_JAR" ] && [ "${FORCE_AGENT_DOWNLOAD:-false}" != "true" ]; then
  log "  agent.jar 이미 존재 — 스킵 (강제 재다운로드: FORCE_AGENT_DOWNLOAD=true)"
else
  curl -fL -o "$AGENT_JAR" "$JENKINS_URL/jnlpJars/agent.jar" \
    || err "agent.jar 다운로드 실패. Jenkins 가 $JENKINS_URL 에서 응답하는지 확인"
  log "  다운로드 완료: $(du -h "$AGENT_JAR" | cut -f1)"
fi

# ── 5. 기동 스크립트 생성 + agent 연결 ─────────────────────────────────────
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
log "[5/5] 기동 스크립트: $RUN_SCRIPT"
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
