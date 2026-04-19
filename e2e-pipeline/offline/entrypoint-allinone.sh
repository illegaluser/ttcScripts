#!/usr/bin/env bash
# ============================================================================
# Zero-Touch QA All-in-One — 컨테이너 엔트리포인트
# 역할:
#   1) /data (볼륨) 을 최초 기동 시 /opt/seed 로부터 seed
#   2) Jenkins/Dify/Ollama/PG 경로 symlink 연결
#   3) supervisord 백그라운드 기동
#   4) 최초 1회만 앱 프로비저닝 (Dify API 설정 + Jenkins REST Credentials/Job)
#   5) foreground wait
# ============================================================================
set -euo pipefail

DATA=/data
SEED=/opt/seed
LOG_PREFIX="[entrypoint-allinone]"

log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
err()  { printf '%s ERROR: %s\n' "$LOG_PREFIX" "$*" >&2; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }

# ────────────────────────────────────────────────────────────────────────────
# 0. 시계 확인 (폐쇄망 NTP 미설정 시 Dify 세션 쿠키 만료 등 이상동작 가능)
# ────────────────────────────────────────────────────────────────────────────
log "container time: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
YEAR=$(date -u '+%Y')
if [ "$YEAR" -lt 2025 ]; then
  warn "시스템 시계가 2025년 이전입니다. 폐쇄망이라 NTP 가 없는 경우"
  warn "  docker run 시 --env TZ 및 호스트 시계를 먼저 맞춰주세요."
fi

# ────────────────────────────────────────────────────────────────────────────
# 1. /data 최초 seed (볼륨이 비어있을 때만)
# ────────────────────────────────────────────────────────────────────────────
if [ ! -f "$DATA/.initialized" ]; then
  log "최초 seed: /opt/seed → /data"
  mkdir -p \
    "$DATA/pg" "$DATA/redis" "$DATA/qdrant" \
    "$DATA/jenkins/plugins" \
    "$DATA/dify/storage" "$DATA/dify/plugins/cwd" \
    "$DATA/logs" "$DATA/jenkins-agent"

  # Ollama 모델 seed 제거됨 (Mac 브랜치) — 호스트 Ollama 를 사용하므로 컨테이너에
  # 모델 파일이 포함되지 않는다. 연결 경로는 OLLAMA_BASE_URL env 로 지정.

  # Jenkins 플러그인 (hpi 파일)
  if [ -d "$SEED/jenkins-plugins" ]; then
    cp -a "$SEED/jenkins-plugins/." "$DATA/jenkins/plugins/"
  fi

  # Jenkins 기본 설정 (관리자 계정 groovy, JENKINS_HOME 기본 구조)
  if [ -d "$SEED/jenkins-home" ]; then
    cp -an "$SEED/jenkins-home/." "$DATA/jenkins/" || true
  fi

  # PostgreSQL initdb 스냅샷 (빌드 타임에 initdb + Alembic migration 완료된 상태)
  if [ -d "$SEED/pg" ] && [ -z "$(ls -A "$DATA/pg" 2>/dev/null || true)" ]; then
    cp -a "$SEED/pg/." "$DATA/pg/"
  fi

  # Dify 플러그인 .difypkg (plugin_daemon 이 최초 기동 시 스캔해 자동 로드)
  if [ -d "$SEED/dify-plugins" ]; then
    mkdir -p "$DATA/dify/plugins/packages"
    cp -a "$SEED/dify-plugins/." "$DATA/dify/plugins/packages/"
  fi

  # 소유권 — 각 서비스 사용자
  chown -R postgres:postgres "$DATA/pg"          || true
  chown -R jenkins:jenkins   "$DATA/jenkins"     || true
  chown -R redis:redis       "$DATA/redis"       || true

  touch "$DATA/.initialized"
  log "seed 완료."
else
  log "기존 볼륨 감지 — seed 건너뜀."
fi

# ────────────────────────────────────────────────────────────────────────────
# 2. 경로 리다이렉트 — 모두 환경변수 기반 (symlink 불가)
# ────────────────────────────────────────────────────────────────────────────
# PoC 2026-04-19: 이전 버전은 /var/jenkins_home → /data/jenkins 심볼릭 링크를
# 시도했으나, jenkins/jenkins base 이미지가 `VOLUME /var/jenkins_home` 를 선언해
# Docker 가 해당 경로에 익명 볼륨을 자동 마운트한다. 마운트 포인트는 rm 불가라
# `rm: cannot remove '/var/jenkins_home': Device or resource busy` 로 엔트리포인트가
# 즉사하고 crash loop 가 발생했다. 해결: symlink 를 포기하고 supervisord 의
# [program:jenkins] 환경변수 JENKINS_HOME="/data/jenkins" 로만 리다이렉트한다.
# /var/jenkins_home 익명 볼륨은 사용되지 않은 채 남지만 런타임 영향은 없다.
#
# Dify storage 는 OPENDAL_FS_ROOT 환경변수로 supervisord.conf 에서 리다이렉트된다.
# (내부 Ollama 는 Mac 브랜치에서 제거됐으므로 OLLAMA_MODELS 는 불필요.)

# ────────────────────────────────────────────────────────────────────────────
# 3. supervisord 백그라운드 기동
# ────────────────────────────────────────────────────────────────────────────
log "supervisord 기동..."
mkdir -p "$DATA/logs"
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf &
SUPERVISOR_PID=$!

# 시그널 전파 — docker stop 시 supervisord 에 SIGTERM 전달
_term() {
  log "shutdown signal — supervisord 종료 중..."
  kill -TERM "$SUPERVISOR_PID" 2>/dev/null || true
  wait "$SUPERVISOR_PID" 2>/dev/null || true
  exit 0
}
trap _term SIGTERM SIGINT

# ────────────────────────────────────────────────────────────────────────────
# 4. 최초 앱 프로비저닝 (volume 최초 생성 후 1회만)
#    Dify API 설정 (setup 4-1,4-2,4-3b,4-3c,4-3d,4-3e) +
#    Jenkins REST Credentials/Job/Node (setup 5-2,5-4,5-5)
# ────────────────────────────────────────────────────────────────────────────
if [ ! -f "$DATA/.app_provisioned" ]; then
  log "서비스 헬스 대기 (dify-api/dify-web/jenkins 전부 HTTP 200, 최대 10분)..."
  # NOTE: provision-apps.sh 의 curl 폭탄이 dify-api gunicorn 워커를 데드락시키지 않도록
  # 반드시 dify-api (/console/api/setup) 까지 실제 200 응답하는 것을 확인한 뒤 진입한다.
  # --max-time 3 으로 개별 curl hang 을 차단 (gunicorn 마스터만 뜨고 워커가 import 중이면
  # connect 는 성공해도 응답은 지연됨).
  _waited=0
  _limit=600
  until curl -sf --max-time 3 -o /dev/null http://127.0.0.1:5001/console/api/setup \
     && curl -sf --max-time 3 -o /dev/null http://127.0.0.1:18081/install \
     && curl -sf --max-time 3 -o /dev/null -u admin:password http://127.0.0.1:18080/api/json; do
    sleep 5
    _waited=$((_waited + 5))
    if [ $_waited -ge $_limit ]; then
      err "dify-api/dify-web/jenkins 중 일부가 10분 내 준비되지 않음. /data/logs 확인."
      break
    fi
  done
  log "헬스 대기 완료 (${_waited}s 경과)."

  log "앱 프로비저닝 시작 (provision-apps.sh)"
  export DIFY_URL="http://127.0.0.1:18081"
  export JENKINS_URL="http://127.0.0.1:18080"
  # OLLAMA_BASE_URL: Mac 브랜치 이미지는 내부 Ollama 가 없으므로 **항상 호스트 Ollama**
  # 로 라우팅되어야 한다. 기본값은 `host.docker.internal:11434` 이며 docker run 시
  # `--add-host host.docker.internal:host-gateway` 가 반드시 포함되어야 해석된다
  # (Docker Desktop Mac 은 자동 해석, Linux 는 host-gateway 매핑 필수).
  # 사용자가 다른 경로 (예: 사내 Ollama 게이트웨이) 를 쓴다면 docker run -e 로 덮어쓴다.
  export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
  log "  → Ollama 라우팅: ${OLLAMA_BASE_URL}"
  if [[ "${OLLAMA_BASE_URL}" != *"host.docker.internal"* ]] && \
     [[ "${OLLAMA_BASE_URL}" == *"127.0.0.1"* || "${OLLAMA_BASE_URL}" == *"localhost"* ]]; then
    warn "  Mac 브랜치 이미지에는 내부 Ollama 가 없습니다. localhost 로 설정된"
    warn "  OLLAMA_BASE_URL 은 ConnectionError 로 이어질 가능성이 큽니다."
    warn "  의도한 설정이 아니라면 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 권장."
  fi
  export OFFLINE_DIFY_PLUGIN_DIR="$SEED/dify-plugins"
  export OFFLINE_DIFY_CHATFLOW_YAML="/opt/dify-chatflow.yaml"
  export OFFLINE_JENKINS_PIPELINE="/opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline"

  if bash /opt/provision-apps.sh; then
    touch "$DATA/.app_provisioned"
    log "앱 프로비저닝 완료."
  else
    warn "앱 프로비저닝 실패. 컨테이너는 계속 실행됩니다."
    warn "재시도: docker exec <container> bash /opt/provision-apps.sh"
  fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 5. Jenkins 에이전트 기동 준비 — 매 기동마다 실행 (멱등)
#
# /opt 는 컨테이너 ephemeral 이므로 docker restart 시 사라진다. 실제 run-script 와
# agent.jar 는 /data/jenkins-agent 에 저장하고 /opt/jenkins-agent-run.sh 는 심볼릭
# 링크로만 유지한다. NODE_SECRET 은 Jenkins master 측에서 node 별로 고정되므로
# 재추출해도 값이 바뀌지 않는다 (Node 를 삭제하지 않는 한 멱등).
# ────────────────────────────────────────────────────────────────────────────
if [ -f "$DATA/.app_provisioned" ]; then
  log "Jenkins 에이전트 기동 준비..."
  # Jenkins 가 응답 가능한지 재확인 (provision 없이 단순 restart 된 경우)
  _waited=0
  until curl -sf --max-time 3 -o /dev/null -u admin:password http://127.0.0.1:18080/api/json; do
    sleep 3
    _waited=$((_waited + 3))
    if [ $_waited -ge 120 ]; then
      warn "Jenkins 가 2분 내 준비되지 않아 에이전트 기동 스킵. 수동 재시도 필요."
      break
    fi
  done

  # Jenkins 워크스페이스 스켈레톤 — Pipeline Stage 1 이 .qa_home/venv/bin/activate 를
  # 요구하지만 워크스페이스는 첫 빌드 시에만 생긴다. 미리 생성하고 전역 venv 를
  # 심볼릭 링크해둬 첫 빌드도 바로 venv 를 찾게 한다. (멱등)
  JENKINS_WS=/data/jenkins-agent/workspace/DSCORE-ZeroTouch-QA-Docker
  mkdir -p "$JENKINS_WS/.qa_home/artifacts"
  if [ -d /opt/qa-venv ]; then
    ln -sfn /opt/qa-venv "$JENKINS_WS/.qa_home/venv"
    log "  워크스페이스 .qa_home/venv → /opt/qa-venv 링크 완료"
  else
    warn "  /opt/qa-venv 미존재 — Pipeline 실행 시 venv 에러 가능 (이미지 재빌드 필요)"
  fi

  SECRET=$(curl -sS -u admin:password \
    "http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp" 2>/dev/null \
    | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1 || true)
  if [ -n "$SECRET" ]; then
    mkdir -p /data/jenkins-agent
    AGENT_RUN=/data/jenkins-agent/run.sh
    cat > "$AGENT_RUN" <<AGENT_EOF
#!/usr/bin/env bash
set -e
cd /data/jenkins-agent
if [ ! -f agent.jar ]; then
  cp /opt/jenkins-agent.jar agent.jar 2>/dev/null || \
    curl -sf -o agent.jar http://127.0.0.1:18080/jnlpJars/agent.jar
fi
exec java -jar agent.jar -url http://127.0.0.1:18080 \\
  -secret $SECRET -name mac-ui-tester -workDir /data/jenkins-agent
AGENT_EOF
    chmod +x "$AGENT_RUN"
    # supervisord.conf 는 /opt/jenkins-agent-run.sh 를 참조하므로 심볼릭 링크 유지
    ln -sfn "$AGENT_RUN" /opt/jenkins-agent-run.sh
    supervisorctl -c /etc/supervisor/supervisord.conf start jenkins-agent 2>/dev/null || true
    log "jenkins-agent 기동 요청 완료 (run-script: $AGENT_RUN)."
  else
    warn "jenkins-agent NODE_SECRET 추출 실패. Jenkins Node 'mac-ui-tester' 존재 확인 필요."
    warn "  수동 복구: docker exec <container> bash /opt/provision-apps.sh"
  fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 5. foreground wait (supervisord 종료까지 blocking)
# ────────────────────────────────────────────────────────────────────────────
log "준비 완료. supervisord wait..."
wait "$SUPERVISOR_PID"
