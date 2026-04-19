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
    "$DATA/logs"
  # NOTE: /data/jenkins-agent 는 더 이상 사용 안 함 — agent 는 호스트 Mac 에서 실행

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
# 3-2. dify-plugin-daemon 이 확실히 기동된 뒤 dify-api 를 수동 start
#
# 과거 재현된 race: supervisord 가 plugin-daemon 과 dify-api 를 거의 동시에 spawn
# → plugin-daemon 이 초반에 한 번 exit 1 로 죽었다가 재시작 → 그 공백 동안
# dify-api 의 Flask app import 가 plugin-daemon 에 blocking call 을 시도 →
# "Booting worker" 를 못 찍고 gunicorn worker 가 futex_wait_queue 에 영구 대기.
#
# 해결: supervisord.conf 에서 dify-api 를 autostart=false 로 두고 여기서 수동 start.
# plugin-daemon /health/check 가 200 응답하는 것을 확인한 뒤에만 start.
# ────────────────────────────────────────────────────────────────────────────
log "dify-plugin-daemon 헬스 대기 (race 방지)..."
_w=0
until curl -sf --max-time 2 -o /dev/null http://127.0.0.1:5002/health/check; do
  sleep 2
  _w=$((_w + 2))
  if [ $_w -ge 120 ]; then
    warn "dify-plugin-daemon 이 2분 내 ready 되지 않음. dify-api 를 그래도 기동 시도."
    break
  fi
done
log "  dify-plugin-daemon ready (${_w}s 경과) — dify-api start"
# 추가 안전 지연: plugin-daemon 이 health 응답 후에도 내부 플러그인 로드 1-2초 필요
sleep 3
supervisorctl -c /etc/supervisor/supervisord.conf start dify-api >/dev/null 2>&1 || \
  warn "supervisorctl start dify-api 실패 — 수동 확인 필요"

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
# 5. 호스트 Mac agent 연결 안내 (컨테이너 내부 agent 는 제거됐음 — 하이브리드 설계)
#
# 원본 설계 복원 — Jenkins agent 는 호스트 Mac 에서 직접 실행해야 Playwright 가
# macOS 화면에 Chromium 창을 띄울 수 있다 (시각 검증). 컨테이너는 JNLP 포트 50001
# 만 노출하고, 호스트 agent.jar 가 외부에서 이 포트로 접속한다.
# ────────────────────────────────────────────────────────────────────────────
if [ -f "$DATA/.app_provisioned" ]; then
  # Jenkins 응답 대기 (provision 없이 restart 된 경우)
  _waited=0
  until curl -sf --max-time 3 -o /dev/null -u admin:password http://127.0.0.1:18080/api/json; do
    sleep 3
    _waited=$((_waited + 3))
    [ $_waited -ge 120 ] && { warn "Jenkins 2분 내 응답 없음 — agent secret 추출 스킵"; break; }
  done

  SECRET=$(curl -sS -u admin:password \
    "http://127.0.0.1:18080/computer/mac-ui-tester/slave-agent.jnlp" 2>/dev/null \
    | sed -n 's/.*<argument>\([a-f0-9]\{64\}\)<\/argument>.*/\1/p' | head -n1 || true)
  if [ -n "$SECRET" ]; then
    log "=========================================================================="
    log "Jenkins agent 연결 정보 (호스트 Mac 에서 mac-agent-setup.sh 로 연결)"
    log "  NODE_SECRET: $SECRET"
    log "  AGENT_NAME : mac-ui-tester"
    log "  JENKINS_URL: http://localhost:18080   (호스트 Mac 기준)"
    log "=========================================================================="
    log "호스트 Mac 에서:"
    log "  cd <e2e-pipeline 위치>"
    log "  NODE_SECRET=$SECRET ./offline/mac-agent-setup.sh"
    log ""
    log "첫 실행 시 JDK21 + Python venv + Playwright Chromium 설치 후 agent 연결 → headed Chromium 창이 Mac 에 뜸"
    log "=========================================================================="
  else
    warn "Jenkins Node 'mac-ui-tester' 미등록 또는 응답 불가. 수동 복구: docker exec <container> bash /opt/provision-apps.sh"
  fi
fi

# ────────────────────────────────────────────────────────────────────────────
# 6. foreground wait (supervisord 종료까지 blocking)
# ────────────────────────────────────────────────────────────────────────────
log "준비 완료. supervisord wait..."
wait "$SUPERVISOR_PID"
