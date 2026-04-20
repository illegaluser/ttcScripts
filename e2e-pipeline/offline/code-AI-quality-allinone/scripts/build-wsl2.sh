#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — WSL2 (Windows) 빌드 스크립트
#
# 실행 위치: WSL2 Ubuntu 셸.
# 빌드 컨텍스트: 이 폴더 자체 (code-AI-quality-allinone/). 폴더만 압축해서
#   다른 머신으로 옮겨도 바로 빌드 가능.
# Dockerfile 위치: 이 폴더의 Dockerfile
# 결과 이미지: ttc-allinone:wsl2-<tag>
#
# 빌드 전 선행 조건:
#   1) 온라인 연결로 플러그인 다운로드: bash scripts/download-plugins.sh
#      → jenkins-plugins/, dify-plugins/, jenkins-plugin-manager.jar 생성
#   2) 이후 이 스크립트 실행
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts/ → code-AI-quality-allinone/
ALLINONE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG="${TAG:-dev}"
IMAGE="${IMAGE:-ttc-allinone:wsl2-${TAG}}"
PLATFORM="linux/amd64"

cd "$ALLINONE_DIR"

# 경고: WSL2 는 /mnt/c 가 아닌 native FS 에서 빌드해야 빠르다
if [[ "$(pwd)" == /mnt/* ]]; then
    echo "[build-wsl2] WARN: WSL2 마운트 경로 ($(pwd)) 에서 빌드하면 I/O 가 느립니다." >&2
    echo "[build-wsl2]       WSL2 네이티브 경로로 clone 하는 것을 권장합니다." >&2
fi

# 자체 완결 폴더 전제 검증
[ -f "$ALLINONE_DIR/Dockerfile" ]                          || { echo "Dockerfile 없음" >&2; exit 1; }
[ -f "$ALLINONE_DIR/requirements.txt" ]                    || { echo "requirements.txt 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/pipeline-scripts" ]                    || { echo "pipeline-scripts/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/eval_runner" ]                         || { echo "eval_runner/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkinsfiles" ]                        || { echo "jenkinsfiles/ 없음" >&2; exit 1; }
[ -d "$ALLINONE_DIR/jenkins-init" ]                        || { echo "jenkins-init/ 없음" >&2; exit 1; }

# 플러그인 선행 단계 가드
if [ ! -d "$ALLINONE_DIR/jenkins-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/jenkins-plugins" 2>/dev/null)" ]; then
    echo "[build-wsl2] jenkins-plugins/ 가 비어 있습니다. 먼저 다음을 실행하세요:" >&2
    echo "               bash scripts/download-plugins.sh" >&2
    exit 1
fi
if [ ! -d "$ALLINONE_DIR/dify-plugins" ] || [ -z "$(ls -A "$ALLINONE_DIR/dify-plugins" 2>/dev/null)" ]; then
    echo "[build-wsl2] dify-plugins/ 가 비어 있습니다. 먼저 다음을 실행하세요:" >&2
    echo "               bash scripts/download-plugins.sh" >&2
    exit 1
fi

echo "[build-wsl2] image:      $IMAGE"
echo "[build-wsl2] platform:   $PLATFORM"
echo "[build-wsl2] context:    $ALLINONE_DIR"
echo "[build-wsl2] Dockerfile: $ALLINONE_DIR/Dockerfile"

docker buildx inspect ttc-allinone-builder >/dev/null 2>&1 || \
    docker buildx create --name ttc-allinone-builder --use

docker buildx build \
    --builder ttc-allinone-builder \
    --platform "$PLATFORM" \
    -f "$ALLINONE_DIR/Dockerfile" \
    -t "$IMAGE" \
    --load \
    "$@" \
    "$ALLINONE_DIR"

echo "[build-wsl2] 빌드 완료: $IMAGE"
echo "[build-wsl2] 기동: bash scripts/run-wsl2.sh"
