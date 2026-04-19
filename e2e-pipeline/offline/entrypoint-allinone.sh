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
    "$DATA/pg" "$DATA/redis" "$DATA/qdrant" "$DATA/ollama/models" \
    "$DATA/jenkins/plugins" \
    "$DATA/dify/storage" "$DATA/dify/plugins/cwd" \
    "$DATA/logs" "$DATA/jenkins-agent"

  # Ollama 모델 (Dockerfile stage 에서 /opt/seed/ollama 에 모델만 남긴 형태)
  if [ -d "$SEED/ollama" ]; then
    cp -a "$SEED/ollama/." "$DATA/ollama/"
  fi

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
# Ollama 모델 경로는 OLLAMA_MODELS, Dify storage 는 OPENDAL_FS_ROOT 환경변수로
# supervisord.conf 에서 리다이렉트된다.

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
  export OLLAMA_BASE_URL="http://127.0.0.1:11434"
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

  # Jenkins 에이전트 secret 추출 후 supervisord 에 기동 요청
  SECRET=$(curl -sS -u admin:password \
    "http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp" 2>/dev/null \
    | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1 || true)
  if [ -n "$SECRET" ]; then
    cat > /opt/jenkins-agent-run.sh <<AGENT_EOF
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
    chmod +x /opt/jenkins-agent-run.sh
    supervisorctl -c /etc/supervisor/supervisord.conf start jenkins-agent || true
    log "jenkins-agent 기동 요청 완료."
  else
    warn "jenkins-agent NODE_SECRET 추출 실패. 수동으로 agent.jnlp 확인 필요."
  fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 5. foreground wait (supervisord 종료까지 blocking)
# ────────────────────────────────────────────────────────────────────────────
log "준비 완료. supervisord wait..."
wait "$SUPERVISOR_PID"
