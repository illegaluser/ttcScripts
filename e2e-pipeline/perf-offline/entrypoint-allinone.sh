#!/usr/bin/env bash
# ============================================================================
# JMeter Performance All-in-One — 컨테이너 엔트리포인트
# 역할:
#   1) /data (볼륨) 을 최초 기동 시 /opt/seed 로부터 seed
#   2) 호스트 Ollama (host.docker.internal:11434) 도달 가능성 점검 (경고만)
#   3) supervisord 백그라운드 기동
#   4) JMETER_GUI_AUTOSTART 환경변수에 따라 GUI on/off
#   5) foreground wait
# ============================================================================
set -euo pipefail

DATA=/data
SEED=/opt/seed
LOG_PREFIX="[entrypoint-perf]"

log()  { printf '%s %s\n' "$LOG_PREFIX" "$*"; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }

# ────────────────────────────────────────────────────────────────────────────
# 0. 시계 확인
# ────────────────────────────────────────────────────────────────────────────
log "container time: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ────────────────────────────────────────────────────────────────────────────
# 1. /data 최초 seed (볼륨이 비어있을 때만)
# ────────────────────────────────────────────────────────────────────────────
if [ ! -f "$DATA/.initialized" ]; then
  log "최초 seed: /opt/seed → /data"
  mkdir -p \
    "$DATA/jmeter/scenarios" \
    "$DATA/jmeter/results" \
    "$DATA/jmeter/reports" \
    "$DATA/jmeter/lib-ext" \
    "$DATA/logs"

  # 샘플 시나리오 (.jmx) — 폐쇄망 첫 사용자가 즉시 실행해 볼 수 있도록 동봉
  if [ -d "$SEED/jmeter/scenarios" ]; then
    cp -an "$SEED/jmeter/scenarios/." "$DATA/jmeter/scenarios/" || true
  fi

  touch "$DATA/.initialized"
  log "seed 완료."
else
  log "기존 볼륨 감지 — seed 건너뜀."
fi

# ────────────────────────────────────────────────────────────────────────────
# 2. JMeter 사용자 추가 .jar 가 있으면 lib/ext 에 심볼릭
#    (사용자가 /data/jmeter/lib-ext 에 .jar 만 올려두면 자동 인식)
# ────────────────────────────────────────────────────────────────────────────
if [ -d "$DATA/jmeter/lib-ext" ]; then
  for jar in "$DATA/jmeter/lib-ext"/*.jar; do
    [ -e "$jar" ] || continue
    ln -sfn "$jar" "/opt/apache-jmeter/lib/ext/$(basename "$jar")"
  done
fi

# ────────────────────────────────────────────────────────────────────────────
# 3. 호스트 Ollama 도달 가능성 점검 (경고만 — 컨테이너는 계속 기동)
#    Docker Desktop (Mac/Windows) 은 host.docker.internal 자동 매핑.
#    Linux 호스트는 docker run 시 --add-host=host.docker.internal:host-gateway 필요.
# ────────────────────────────────────────────────────────────────────────────
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://host.docker.internal:11434}"
log "호스트 Ollama 도달 점검: $OLLAMA_HOST_URL"
if curl -sf --max-time 3 -o /dev/null "${OLLAMA_HOST_URL}/api/tags"; then
  MODELS=$(curl -sf --max-time 3 "${OLLAMA_HOST_URL}/api/tags" 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(','.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || true)
  log "  ✓ 호스트 Ollama 응답 OK. 사용 가능 모델: ${MODELS:-(없음)}"
  if [ -z "$MODELS" ]; then
    warn "  호스트에 모델이 없습니다. 호스트에서 'ollama pull gemma4:e2b' 실행 필요."
  fi
elif curl -sf --max-time 3 -o /dev/null "${OLLAMA_HOST_URL}/api/version" 2>/dev/null; then
  log "  ✓ 호스트 Ollama 응답 OK (버전 엔드포인트만)."
else
  warn "  ✗ 호스트 Ollama (${OLLAMA_HOST_URL}) 도달 불가."
  warn "    Mac/Windows 호스트:"
  warn "      1) Ollama 가 호스트에 설치/기동되어 있는지: 'ollama list'"
  warn "      2) OLLAMA_HOST=0.0.0.0:11434 환경변수로 외부 바인드되어 있는지"
  warn "      3) docker run 시 --add-host host.docker.internal:host-gateway (Linux)"
  warn "    JMeter 는 정상 동작하지만 jmeter.ai (Feather Wand) 호출은 실패합니다."
fi

# ────────────────────────────────────────────────────────────────────────────
# 4. supervisord 백그라운드 기동
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
# 5. JMeter GUI 토글
# ────────────────────────────────────────────────────────────────────────────
JMETER_GUI_AUTOSTART="${JMETER_GUI_AUTOSTART:-true}"

# supervisord socket 이 준비될 때까지 짧게 대기
for _ in $(seq 1 30); do
  [ -S /var/run/supervisor.sock ] && break
  sleep 1
done

if [ "$JMETER_GUI_AUTOSTART" = "true" ]; then
  log "JMETER_GUI_AUTOSTART=true → jmeter-gui 시작 (브라우저: http://<host>:18090)"
  supervisorctl -c /etc/supervisor/supervisord.conf start jmeter-gui 2>/dev/null || \
    warn "jmeter-gui 시작 실패 (xvfb 준비 전일 수 있음). 잠시 후 supervisorctl 로 재시도하세요."
else
  log "JMETER_GUI_AUTOSTART=false → jmeter-gui 비활성. CLI(-n) 모드로 사용하세요."
fi

# ────────────────────────────────────────────────────────────────────────────
# 6. foreground wait
# ────────────────────────────────────────────────────────────────────────────
log "준비 완료."
log "  GUI:    http://<host>:18090   (noVNC → JMeter Swing GUI)"
log "  CLI:    docker exec <container> jmeter -n -t /data/jmeter/scenarios/<file>.jmx ..."
log "  Ollama: ${OLLAMA_HOST_URL}  (호스트에서 동작; /ollama/ proxy via :18090)"
wait "$SUPERVISOR_PID"
