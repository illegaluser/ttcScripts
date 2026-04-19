#!/usr/bin/env bash
# ============================================================================
# perf-allinone — Mac 폐쇄망 자동 설치 스크립트
#
# 동작 (모두 자동, 사용자 입력 거의 없음):
#   1) 사전 점검 (Docker Desktop 기동 여부, assets/ 무결성)
#   2) Ollama 설치 (Ollama.dmg → Applications)
#   3) Ollama 환경변수 설정 (launchctl + ~/.zshrc/~/.bash_profile)
#   4) Ollama 기동 + 헬스체크
#   5) 모델 archive 복원 (ollama-models-*.tgz → ~/.ollama/models)
#   6) JMeter 컨테이너 이미지 로드 + 기동
#   7) 호스트 Ollama ↔ 컨테이너 도달 검증
#   8) 브라우저로 GUI 자동 열기
#
# 사용법:
#   bash install-mac.sh                    # 전체 자동 설치
#   bash install-mac.sh --uninstall        # 모두 제거 (모델/볼륨 보존 옵션 안내)
#   bash install-mac.sh --check            # 현재 상태만 점검 (변경 없음)
#
# 환경변수 (선택):
#   PERF_DATA_VOLUME       기본 perf-data
#   CONTAINER_NAME         기본 perf-allinone
#   OLLAMA_BIND_HOST       기본 0.0.0.0:11434
#   ASSETS_DIR             기본 ./assets
#   SKIP_BROWSER           '1' 이면 마지막 브라우저 자동 열기 건너뜀
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="${ASSETS_DIR:-${SCRIPT_DIR}/assets}"
PERF_DATA_VOLUME="${PERF_DATA_VOLUME:-perf-data}"
CONTAINER_NAME="${CONTAINER_NAME:-perf-allinone}"
OLLAMA_BIND_HOST="${OLLAMA_BIND_HOST:-0.0.0.0:11434}"
SKIP_BROWSER="${SKIP_BROWSER:-0}"

# ── 컬러 출력 ────────────────────────────────────────────────────────────
C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'
C_ERR='\033[1;31m';  C_RST='\033[0m';   C_BOLD='\033[1m'

step()  { printf '\n%b▶ %s%b\n' "$C_BOLD$C_INFO" "$*" "$C_RST"; }
log()   { printf '   %s\n' "$*"; }
ok()    { printf '   %b✓%b %s\n' "$C_OK" "$C_RST" "$*"; }
warn()  { printf '   %b!%b %s\n' "$C_WARN" "$C_RST" "$*" >&2; }
err()   { printf '\n%b✗ %s%b\n' "$C_ERR" "$*" "$C_RST" >&2; exit 1; }

# ── 모드 분기 ────────────────────────────────────────────────────────────
MODE="install"
case "${1:-}" in
  --uninstall) MODE="uninstall" ;;
  --check)     MODE="check" ;;
  -h|--help)   sed -n '3,28p' "$0"; exit 0 ;;
esac

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
arch_suffix() {
  case "$(uname -m)" in
    arm64)  echo "arm64" ;;
    x86_64) echo "amd64" ;;
    *) err "지원하지 않는 아키텍처: $(uname -m)" ;;
  esac
}

find_image_tar() {
  local arch; arch="$(arch_suffix)"
  local f
  f=$(ls -1t "$ASSETS_DIR"/dscore-qa-perf-allinone-${arch}-*.tar.gz 2>/dev/null | head -n1 || true)
  [ -n "$f" ] && [ -f "$f" ] || err "이미지 tar.gz 없음: $ASSETS_DIR/dscore-qa-perf-allinone-${arch}-*.tar.gz"
  echo "$f"
}

find_model_archive() {
  local f
  f=$(ls -1t "$ASSETS_DIR"/ollama-models-*.tgz 2>/dev/null | head -n1 || true)
  echo "$f"
}

verify_sha256() {
  local file="$1"
  local sumfile="${ASSETS_DIR}/SHA256SUMS"
  [ -f "$sumfile" ] || { warn "SHA256SUMS 누락 — 무결성 검증 건너뜀"; return 0; }
  local fname; fname="$(basename "$file")"
  local expected; expected=$(grep "  ${fname}$" "$sumfile" | awk '{print $1}' | head -n1)
  [ -z "$expected" ] && { warn "$fname 의 sha256 항목 없음 — 건너뜀"; return 0; }
  local actual; actual=$(shasum -a 256 "$file" | awk '{print $1}')
  if [ "$expected" != "$actual" ]; then
    err "$fname 무결성 검증 실패\n  expected: $expected\n  actual:   $actual"
  fi
  ok "  무결성 OK: $fname"
}

# ────────────────────────────────────────────────────────────────────────────
# Mode: --uninstall
# ────────────────────────────────────────────────────────────────────────────
if [ "$MODE" = "uninstall" ]; then
  step "perf-allinone 제거"
  log "컨테이너 중지/제거..."
  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm   "$CONTAINER_NAME" 2>/dev/null || true
  log "이미지 제거..."
  docker rmi "dscore-qa-perf:allinone-$(arch_suffix)" 2>/dev/null || true

  printf '\n%bperf-data 볼륨(시나리오/결과)도 삭제할까요? [y/N]: %b' "$C_WARN" "$C_RST"
  read -r ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    docker volume rm "$PERF_DATA_VOLUME" 2>/dev/null || true
    ok "  perf-data 볼륨 제거"
  else
    log "  perf-data 볼륨 보존"
  fi

  printf '\n%bOllama.app 와 모델(~/.ollama)도 제거할까요? [y/N]: %b' "$C_WARN" "$C_RST"
  read -r ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    osascript -e 'tell application "Ollama" to quit' 2>/dev/null || true
    sleep 2
    rm -rf /Applications/Ollama.app
    rm -rf "$HOME/.ollama"
    launchctl unsetenv OLLAMA_HOST OLLAMA_NOPRUNE OLLAMA_KEEP_ALIVE OLLAMA_NO_CLOUD 2>/dev/null || true
    ok "  Ollama 제거 완료"
  fi

  ok "제거 완료"
  exit 0
fi

# ────────────────────────────────────────────────────────────────────────────
# Mode: --check
# ────────────────────────────────────────────────────────────────────────────
if [ "$MODE" = "check" ]; then
  step "perf-allinone 상태 점검"
  printf '%-30s ' "Docker Desktop:"
  if docker info >/dev/null 2>&1; then printf '%b가동 중%b\n' "$C_OK" "$C_RST"
  else printf '%b미가동%b\n' "$C_ERR" "$C_RST"; fi

  printf '%-30s ' "Ollama 설치:"
  if [ -d "/Applications/Ollama.app" ]; then printf '%b설치됨%b\n' "$C_OK" "$C_RST"
  else printf '%b미설치%b\n' "$C_ERR" "$C_RST"; fi

  printf '%-30s ' "Ollama 응답 (11434):"
  if curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null; then
    printf '%bOK%b\n' "$C_OK" "$C_RST"
    log "  모델: $(curl -sf http://localhost:11434/api/tags | python3 -c 'import json,sys;print(", ".join(m["name"] for m in json.load(sys.stdin).get("models",[])))')"
  else
    printf '%b응답 없음%b\n' "$C_ERR" "$C_RST"
  fi

  printf '%-30s ' "perf-allinone 컨테이너:"
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    printf '%b가동 중%b\n' "$C_OK" "$C_RST"
  elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    printf '%b중지됨%b\n' "$C_WARN" "$C_RST"
  else
    printf '%b없음%b\n' "$C_ERR" "$C_RST"
  fi

  printf '%-30s ' "GUI (http://localhost:18090):"
  if curl -sf --max-time 3 http://localhost:18090 >/dev/null; then
    printf '%bOK%b\n' "$C_OK" "$C_RST"
  else
    printf '%b응답 없음%b\n' "$C_ERR" "$C_RST"
  fi
  exit 0
fi

# ────────────────────────────────────────────────────────────────────────────
# Mode: install (기본)
# ────────────────────────────────────────────────────────────────────────────
clear
cat <<'BANNER'
╔════════════════════════════════════════════════════════════════╗
║  perf-allinone — JMeter Performance All-in-One                 ║
║  🍎 Mac 폐쇄망 자동 설치 스크립트                                ║
╚════════════════════════════════════════════════════════════════╝
BANNER

# ────────────────────────────────────────────────────────────────────────────
# 1. 사전 점검
# ────────────────────────────────────────────────────────────────────────────
step "1/8 사전 점검"

[ -d "$ASSETS_DIR" ] || err "assets/ 디렉토리 없음: $ASSETS_DIR"
log "Mac 아키텍처: $(uname -m) → $(arch_suffix)"
log "사용자: $USER  /  HOME: $HOME"

# Docker Desktop
if ! docker info >/dev/null 2>&1; then
  warn "Docker Desktop 이 가동 중이 아닙니다."
  log  "Docker Desktop.app 실행 후 다시 시도하세요. 자동으로 기동을 시도합니다..."
  open -a Docker || err "Docker Desktop 미설치 또는 실행 실패. https://www.docker.com/products/docker-desktop/ 에서 설치하세요."
  log "Docker Desktop 기동 대기 (최대 60초)..."
  for i in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 1
  done
  docker info >/dev/null 2>&1 || err "Docker Desktop 기동 실패 — 수동으로 실행 후 재시도"
fi
ok "Docker Desktop OK ($(docker --version | awk '{print $3}' | tr -d ','))"

# 디스크 여유 (최소 15GB)
free_gb=$(df -g "$HOME" | awk 'NR==2{print $4}')
[ "$free_gb" -lt 15 ] && warn "홈 디스크 여유 ${free_gb}GB — 15GB 이상 권장"

# 자산 무결성 검증
step "2/8 자산 무결성 검증"
IMAGE_TAR="$(find_image_tar)"
ok "이미지: $(basename "$IMAGE_TAR") ($(du -h "$IMAGE_TAR" | cut -f1))"
verify_sha256 "$IMAGE_TAR"

DMG_PATH="$ASSETS_DIR/Ollama.dmg"
[ -f "$DMG_PATH" ] || err "Ollama.dmg 없음: $DMG_PATH"
ok "Ollama 인스톨러: Ollama.dmg ($(du -h "$DMG_PATH" | cut -f1))"
verify_sha256 "$DMG_PATH"

MODEL_TGZ="$(find_model_archive)"
if [ -n "$MODEL_TGZ" ]; then
  ok "모델 archive: $(basename "$MODEL_TGZ") ($(du -h "$MODEL_TGZ" | cut -f1))"
  verify_sha256 "$MODEL_TGZ"
else
  warn "모델 archive 없음. Ollama 설치 후 호스트에서 직접 'ollama pull' 필요."
fi

# ────────────────────────────────────────────────────────────────────────────
# 3. Ollama 설치
# ────────────────────────────────────────────────────────────────────────────
step "3/8 Ollama 설치"

if [ -d "/Applications/Ollama.app" ]; then
  ok "Ollama.app 이미 설치됨 — 건너뜀"
else
  log "Ollama.dmg 마운트 + Applications 복사..."
  MNT="/Volumes/Ollama-installer-$$"
  hdiutil attach "$DMG_PATH" -mountpoint "$MNT" -nobrowse -quiet
  cp -R "$MNT/Ollama.app" /Applications/
  hdiutil detach "$MNT" -quiet
  ok "Ollama.app → /Applications/"
fi

# ────────────────────────────────────────────────────────────────────────────
# 4. Ollama 환경변수 설정
# ────────────────────────────────────────────────────────────────────────────
step "4/8 Ollama 환경변수 설정 (외부 바인드 + 폐쇄망 옵션)"

log "launchctl setenv (현재 세션 + GUI 앱 적용)..."
launchctl setenv OLLAMA_HOST       "$OLLAMA_BIND_HOST"
launchctl setenv OLLAMA_NOPRUNE    "1"
launchctl setenv OLLAMA_KEEP_ALIVE "24h"
launchctl setenv OLLAMA_NO_CLOUD   "1"
ok "  OLLAMA_HOST=$OLLAMA_BIND_HOST OLLAMA_NOPRUNE=1 OLLAMA_KEEP_ALIVE=24h OLLAMA_NO_CLOUD=1"

# ~/.zshrc 영구 등록
ZRC="$HOME/.zshrc"; [ -f "$ZRC" ] || touch "$ZRC"
if ! grep -q '# perf-allinone OLLAMA env' "$ZRC"; then
  cat >> "$ZRC" <<EOF

# perf-allinone OLLAMA env (added by install-mac.sh)
export OLLAMA_HOST=$OLLAMA_BIND_HOST
export OLLAMA_NOPRUNE=1
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_NO_CLOUD=1
EOF
  ok "  ~/.zshrc 영구 등록"
else
  log "  ~/.zshrc 항목 이미 존재 — 건너뜀"
fi

# ────────────────────────────────────────────────────────────────────────────
# 5. Ollama 기동 + 헬스체크
# ────────────────────────────────────────────────────────────────────────────
step "5/8 Ollama 기동 + 헬스체크"

# 이미 떠있으면 종료 후 환경변수 적용된 상태로 재기동
osascript -e 'tell application "Ollama" to quit' 2>/dev/null || true
sleep 2
open -a Ollama
log "Ollama 기동 대기..."
for i in $(seq 1 30); do
  if curl -sf --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi
  sleep 1
done

curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null \
  || err "Ollama 응답 없음. Activity Monitor 에서 'ollama' 프로세스 확인 필요."
ok "Ollama 응답 OK (http://localhost:11434)"

# ────────────────────────────────────────────────────────────────────────────
# 6. 모델 archive 복원
# ────────────────────────────────────────────────────────────────────────────
step "6/8 Ollama 모델 복원"

if [ -n "$MODEL_TGZ" ]; then
  mkdir -p "$HOME/.ollama"
  log "archive 풀기: $(basename "$MODEL_TGZ") → ~/.ollama/"
  tar xzf "$MODEL_TGZ" -C "$HOME/.ollama/"

  # Ollama 재시작 (모델 인식 위해)
  osascript -e 'tell application "Ollama" to quit' 2>/dev/null || true
  sleep 2
  open -a Ollama
  for i in $(seq 1 30); do
    if curl -sf --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then break; fi
    sleep 1
  done

  models=$(curl -sf http://localhost:11434/api/tags | python3 -c 'import json,sys;print(", ".join(m["name"] for m in json.load(sys.stdin).get("models",[])))')
  if [ -n "$models" ]; then
    ok "모델 인식: $models"
  else
    warn "모델 archive 복원 후에도 'ollama list' 가 비어있음. 매니페스트 경로 확인 필요."
  fi
else
  log "(모델 archive 미동봉 — 호스트에서 'ollama pull <model>' 실행 필요)"
fi

# ────────────────────────────────────────────────────────────────────────────
# 7. JMeter 컨테이너 로드 + 기동
# ────────────────────────────────────────────────────────────────────────────
step "7/8 JMeter 컨테이너 로드 + 기동"

log "이미지 로드: $(basename "$IMAGE_TAR")..."
LOADED_TAG=$(docker load -i "$IMAGE_TAR" 2>&1 | tail -n1 | awk '{print $NF}')
ok "이미지 로드: $LOADED_TAG"

# 기존 컨테이너 정리
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  log "기존 컨테이너 제거: $CONTAINER_NAME"
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm   "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

log "컨테이너 기동..."
docker run -d \
  --name "$CONTAINER_NAME" \
  -p 18090:18090 \
  -v "$PERF_DATA_VOLUME:/data" \
  -e JMETER_GUI_AUTOSTART=true \
  -e TZ=Asia/Seoul \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  "$LOADED_TAG" \
  >/dev/null

ok "컨테이너 기동: $CONTAINER_NAME"

# ────────────────────────────────────────────────────────────────────────────
# 8. 도달성 검증 + 브라우저 열기
# ────────────────────────────────────────────────────────────────────────────
step "8/8 도달성 검증"

log "컨테이너 → 호스트 Ollama 도달..."
for i in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" curl -sf --max-time 3 \
       http://host.docker.internal:11434/api/tags >/dev/null 2>&1; then
    ok "컨테이너 ↔ 호스트 Ollama OK"
    break
  fi
  sleep 1
  [ "$i" -eq 30 ] && warn "도달 검증 timeout — JMeter는 동작하지만 jmeter.ai 호출은 실패할 수 있음"
done

log "GUI(noVNC) 응답 대기 (최대 60초)..."
for i in $(seq 1 60); do
  if curl -sf --max-time 2 http://localhost:18090 >/dev/null 2>&1; then break; fi
  sleep 1
done
if curl -sf --max-time 3 http://localhost:18090 >/dev/null; then
  ok "GUI OK (http://localhost:18090)"
else
  warn "GUI 응답 없음 — 'docker logs $CONTAINER_NAME' 확인"
fi

# ────────────────────────────────────────────────────────────────────────────
# 완료
# ────────────────────────────────────────────────────────────────────────────
cat <<EOF

${C_OK}╔════════════════════════════════════════════════════════════════╗
║  ✓ 설치 완료                                                    ║
╚════════════════════════════════════════════════════════════════╝${C_RST}

  🌐 GUI:    http://localhost:18090
  ⚙️  Ollama: http://localhost:11434  (호스트, GPU 가속 활성)
  📦 컨테이너: $CONTAINER_NAME
  💾 데이터:  Docker volume '$PERF_DATA_VOLUME' (/data)

  📚 사용법:
     - 평상시(GUI):  브라우저에서 http://localhost:18090
     - 정식 시험(CLI):
         docker exec $CONTAINER_NAME jmeter -n -t /data/jmeter/scenarios/<file>.jmx \\
           -l /data/jmeter/results/<file>-\$(date +%s).jtl \\
           -e -o /data/jmeter/reports/<file>-\$(date +%s)/

  🛠  관리 명령:
     bash install-mac.sh --check       # 상태 점검
     bash install-mac.sh --uninstall   # 제거

  📖 자세한 사용법:  README-mac.md / README.md (5장 JMeter 심화)

EOF

if [ "$SKIP_BROWSER" != "1" ]; then
  log "브라우저 자동 열기..."
  sleep 1
  open "http://localhost:18090"
fi
