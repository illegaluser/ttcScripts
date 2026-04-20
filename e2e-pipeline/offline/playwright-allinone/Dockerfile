# ============================================================================
# Zero-Touch QA All-in-One — 호스트 하이브리드 이미지 (Mac / WSL2 Ubuntu 공용)
#
# 본 Dockerfile 은 호스트 Ollama + 호스트 Jenkins agent 를 전제로 한 하이브리드
# 설계다. Mac 과 Windows (WSL2 Ubuntu) 환경의 공통 요구 때문에 이렇게 나왔다:
#
#   - Docker Desktop on Mac: Linux 컨테이너에 Metal GPU passthrough 미지원 →
#     컨테이너 내 Ollama 가 CPU 모드 (gemma4:e4b 기준 1-2 tok/s) → 타임아웃.
#   - Playwright headed Chromium 은 컨테이너 안에서 호스트 데스크탑에 창을
#     띄울 수 없음 (컨테이너에 X/Wayland 소켓 마운트해도 경험이 일관되지 않음).
#
# 해결:
#   - Ollama / Jenkins agent 는 **호스트에서 실행**
#     - Mac: macOS 네이티브에 둘 다
#     - Windows 11: Ollama 는 **Windows 네이티브** (GPU CUDA 직접),
#                   Jenkins agent 는 **WSL2 Ubuntu 안** 에서 실행 (WSLg 로
#                   Windows 데스크탑에 Chromium 창).
#     - 컨테이너는 `host.docker.internal:11434` 로 Docker Desktop 포워딩을
#       거쳐 호스트 Ollama 에 도달 (Mac 호스트 또는 Windows 호스트 동일).
#   - 컨테이너는 Jenkins controller + Dify + PG/Redis/Qdrant/nginx 만 담당.
#
# 따라서 이미지 내부에서:
#   - Ollama 바이너리 + 모델 파일 (~4.5GB): 완전 제거 (호스트 ollama 로 대체)
#   - Jenkins agent 런타임: 컨테이너 내부에 없음 (호스트에서 JNLP 연결)
#
# ⚠️ 본 이미지는 호스트에 Ollama 데몬이 설치·기동된 상태에서만 동작한다.
#   docker run -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
#              -e OLLAMA_MODEL=gemma4:e4b (또는 호스트에 pull 된 모델) ... 필수.
#   호스트 agent 스크립트: mac-agent-setup.sh (Mac) / wsl-agent-setup.sh (WSL2)
#   자세한 가이드: README §4 (Mac), §5 (Windows 11 / WSL2 Ubuntu).
#
# 결과 이미지 크기 예상: 5-7GB (압축 전). 내부-Ollama 버전 대비 4-5GB 절감.
# 빌드 요구사항: 디스크 여유 20GB+, 네트워크(마지막 단계까지), Docker buildx 권장.
# 멀티 아키: docker buildx build --platform linux/<arm64|amd64> -f e2e-pipeline/offline/playwright-allinone/Dockerfile e2e-pipeline/offline/playwright-allinone
# ============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Dify 서비스 이미지에서 애플리케이션 파일 확보
# 각 공식 이미지를 참조만 해서 필요한 디렉토리를 복사한다.
# ─────────────────────────────────────────────────────────────────────────────
FROM langgenius/dify-api:1.13.3           AS dify-api-src
FROM langgenius/dify-web:1.13.3           AS dify-web-src
# PoC 2026-04-19: upstream dify 1.13.3 docker-compose 공식 값과 동일.
# 0.5.8-local 은 GORM AutoMigrate 를 더이상 수행하지 않아 plugin_declarations
# 등 테이블이 생성되지 않는다 (SQLSTATE 42P01). 0.5.3-local 이 최신 안정조합.
FROM langgenius/dify-plugin-daemon:0.5.3-local AS dify-plugin-src

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: 최종 런타임 — Jenkins LTS (JDK 21) 기반에 모든 서비스 패키지를 추가
# ─────────────────────────────────────────────────────────────────────────────
FROM jenkins/jenkins:lts-jdk21

USER root
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Seoul

# -- PostgreSQL 15 공식 apt repo (pgdg) 등록 --
# PoC 2026-04-19: jenkins/jenkins:lts-jdk21 base 는 Debian 13 (trixie) 이고
# 기본 apt 에는 postgresql-17 만 있음 → Dify 1.13.3 공식 지원 버전 (15) 와
# 불일치하므로 snapshot 휴대가 불가능. pgdg 공식 repo 를 추가해 15 사용.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg lsb-release && \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list

# -- OS 패키지 (supervisord + PG 15 + redis + nginx + Python + Playwright 의존성) --
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    postgresql-15 postgresql-client-15 \
    redis-server \
    nginx \
    python3 python3-pip python3-venv python3-dev \
    build-essential libssl-dev zlib1g-dev \
    jq tini sudo \
    libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 \
    poppler-utils libreoffice-impress \
    fonts-noto-cjk \
    && (apt-get install -y --no-install-recommends libasound2t64 2>/dev/null \
        || apt-get install -y --no-install-recommends libasound2) \
    && rm -rf /var/lib/apt/lists/*

# -- Ollama 바이너리 / 모델 seed: 제거됨 (호스트 하이브리드 설계) --
# 컨테이너 내부에서 Ollama 를 실행하지 않으므로 바이너리도 모델도 포함하지 않는다.
# 호스트 Ollama 데몬 (Mac: Metal, Windows: CUDA) 을 OLLAMA_BASE_URL env 로 가리키는 것이 전제.
# 디버깅 목적의 `docker exec dscore.ttc.playwright ollama list` 같은 명령도 **동작하지 않는다**.
# 호스트에서 `ollama list` / `ollama pull` 을 직접 사용.

# -- Qdrant 바이너리 (공식 릴리스 tar) --
# TARGETARCH 는 buildx 가 `--platform linux/<arch>` 에 맞춰 자동 주입.
# Qdrant release matrix (2026-04-19 확인):
#   amd64 → x86_64-unknown-linux-gnu  (동적 링크 glibc 빌드)
#   arm64 → aarch64-unknown-linux-musl (static musl 빌드 — arm64 gnu 는 미발매.
#           static 이므로 Debian(glibc) 런타임에도 그대로 실행됨)
ARG QDRANT_VERSION=v1.8.3
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) QDRANT_ARCH=x86_64-unknown-linux-gnu ;; \
      arm64) QDRANT_ARCH=aarch64-unknown-linux-musl ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL -o /tmp/qdrant.tar.gz \
      "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-${QDRANT_ARCH}.tar.gz"; \
    tar xzf /tmp/qdrant.tar.gz -C /usr/local/bin; \
    chmod +x /usr/local/bin/qdrant; \
    rm /tmp/qdrant.tar.gz; \
    /usr/local/bin/qdrant --version

# -- Dify 서비스 바이너리/애플리케이션 --
# api/worker 는 동일 이미지(dify-api)에서 MODE 로 동작을 전환한다.
COPY --from=dify-api-src    /app              /opt/dify-api
COPY --from=dify-web-src    /app              /opt/dify-web
COPY --from=dify-plugin-src /app              /opt/dify-plugin-daemon

# -- Python 플러그인 런타임용 uv 바이너리 --
# plugin-daemon 이 Dify 플러그인 (Ollama 등) 을 실행할 때 각 플러그인의 Python
# 가상환경을 uv 로 생성한다. allinone base 에는 uv 가 없어 플러그인 install 이
# "failed to find uv path" 에러로 실패하므로, plugin-daemon 이미지에서 직접 복사.
COPY --from=dify-plugin-src /usr/local/bin/uv  /usr/local/bin/uv
COPY --from=dify-plugin-src /usr/local/bin/uvx /usr/local/bin/uvx

# -- dify-api plugin.py 패치: plugin-daemon 0.5.3-local 은 query param binding 이
# PascalCase(`PluginUniqueIdentifier`) 만 받는 Go struct tag 누락이 있다. upstream
# dify-api 는 snake_case 로 보내 decode/from_identifier 가 400 이 되는 이슈를 우회.
RUN sed -i 's|"plugin_unique_identifier": plugin_unique_identifier|"PluginUniqueIdentifier": plugin_unique_identifier|g' \
    /opt/dify-api/api/core/plugin/impl/plugin.py && \
    grep -c 'PluginUniqueIdentifier' /opt/dify-api/api/core/plugin/impl/plugin.py

# -- Python 의존성 (Jenkins pipeline + 문서 처리 + Playwright) --
# PoC 2026-04-19: Debian trixie base 는 python3-typing-extensions 같은 일부
# Python 패키지를 apt 로 설치한다. 이들은 RECORD 파일이 없어 pip 가 uninstall
# 단계에서 실패한다 (`uninstall-no-record-file`). --ignore-installed 를 붙여
# pip 이 기존 설치를 건드리지 않고 /usr/local 에 덮어쓰게 한다 (apt 관리 파일은
# /usr/lib/python3/dist-packages 에 그대로 남고, pip 버전이 sys.path 우선순위).
COPY requirements.txt /tmp/requirements-allinone.txt
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        -r /tmp/requirements-allinone.txt && \
    pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        deepeval==1.3.5 --no-deps

# -- Dify api 의 의존성은 그 이미지 안에 이미 설치돼 있음 (/opt/dify-api/.venv 또는 system site-packages) --
# dify-api 이미지는 Python 3.12 + poetry 기반이라 가상환경이 /app 내부에 있을 수 있다.
# 런타임에 PYTHONPATH 및 entrypoint 를 그대로 호출하면 되므로 추가 pip 는 생략.

# -- Playwright 브라우저 엔진 (Chromium) — base 의 python3(=3.13) 에 설치 --
RUN python3 -m playwright install --with-deps chromium

# -- Zero-Touch QA 전용 venv (Jenkins Pipeline 이 참조) --
# Jenkins Pipeline(DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline Stage 1) 은
# ${WORKSPACE}/.qa_home/venv/bin/activate 를 요구한다. 워크스페이스는 런타임에만
# 생기므로, 이미지 빌드 시에는 전역 venv 를 /opt/qa-venv 에 만들고 provision.sh
# 가 워크스페이스의 .qa_home/venv 를 이 경로로 심볼릭 링크한다.
#
# --system-site-packages 로 위 pip install 결과(requests/playwright/pillow 등)를
# 그대로 상속. 반드시 /usr/bin/python3 (=3.13) 명시 — /usr/local 은 곧 3.12 로
# 재연결되므로 venv 가 3.12 를 바라보면 deps 를 못 찾는다.
RUN /usr/bin/python3 -m venv --system-site-packages /opt/qa-venv && \
    /opt/qa-venv/bin/python -c "import requests, playwright, PIL; print('qa-venv deps OK')"

# -- Jenkins Pipeline 용 SCRIPTS_HOME 디렉토리 --
# Pipeline 의 PYTHONPATH=SCRIPTS_HOME 로 동작. 이 경로 아래에 zero_touch_qa/ 만
# 노출해 Pipeline 의 `find ... __pycache__ -exec rm -rf` 가 다른 /opt 자산을 건드리지 않게 한다.
RUN mkdir -p /opt/scripts-home && \
    ln -sfn /opt/zero_touch_qa /opt/scripts-home/zero_touch_qa

# ─────────────────────────────────────────────────────────────────────────────
# Dify 서비스 런타임 보강 — pip / playwright 설치 후에 수행해야 한다.
# (아래 단계가 pip 가 사용하는 /usr/bin/python3.13 과 별도로 /usr/local/bin/python3.12
#  를 끌어오고 /usr/local/bin/python3 심볼릭을 python3.12 로 덮어쓰기 때문에 순서가 중요.)
# ─────────────────────────────────────────────────────────────────────────────

# -- dify-api 이미지에서 Python 3.12 런타임 끌어오기 --
# dify-api 의 .venv 는 /usr/local/bin/python3 (=python3.12) 심볼릭에 의존한다.
# Debian trixie 기본 python3 은 3.13 이라 venv 가 broken. 필요 바이너리·동적 라이브러리·
# stdlib 를 dify-api-src 에서 그대로 가져온다.
COPY --from=dify-api-src /usr/local/bin/python3.12         /usr/local/bin/python3.12
COPY --from=dify-api-src /usr/local/bin/python3.12-config  /usr/local/bin/python3.12-config
COPY --from=dify-api-src /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=dify-api-src /usr/local/lib/libpython3.so       /usr/local/lib/libpython3.so
COPY --from=dify-api-src /usr/local/lib/python3.12          /usr/local/lib/python3.12
RUN ln -sf python3.12 /usr/local/bin/python3 && \
    ln -sf python3    /usr/local/bin/python && \
    ln -sf python3.12-config /usr/local/bin/python3-config && \
    ln -sf libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so && \
    ldconfig

# -- Node.js 22 LTS (glibc 빌드) — dify-web 실행용 --
# dify-web 이미지의 node 는 Alpine(musl) 빌드라 Debian(glibc) 기반 allinone 에서
# 실행 불가. nodejs.org 공식 prebuilt glibc tar 를 받아 /usr/local 에 설치.
# TARGETARCH (buildx 자동 주입) 에 따라 x64/arm64 중 맞는 tar 를 선택한다.
ARG NODE_VERSION=22.22.1
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) NODE_ARCH=x64 ;; \
      arm64) NODE_ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
      --no-same-owner --wildcards '*/bin/node' '*/include/node'; \
    rm /tmp/node.tar.xz; \
    node --version

# -- dify 앱 경로 호환 심볼릭 링크 --
# dify-api / dify-web 의 entrypoint.sh 가 /app/api, /app/web 을 하드코딩하므로
# /app/{api,web} 를 /opt/dify-*/ 로 연결해 호환성 확보.
RUN mkdir -p /app && \
    ln -sfn /opt/dify-api/api       /app/api && \
    ln -sfn /opt/dify-web/web       /app/web && \
    ln -sfn /opt/dify-plugin-daemon /app/plugin_daemon

# -- Jenkins 플러그인 hpi 사전 배치 (build-allinone.sh 가 채움) --
COPY jenkins-plugins/ /opt/seed/jenkins-plugins/

# -- Dify 플러그인 .difypkg 사전 배치 --
COPY dify-plugins/ /opt/seed/dify-plugins/

# -- PostgreSQL initdb + Dify DB 생성 스냅샷 --
COPY pg-init.sh /opt/pg-init.sh
RUN bash /opt/pg-init.sh

# -- supervisord 설정 --
COPY supervisord.conf /etc/supervisor/supervisord.conf

# -- nginx 설정 (localhost upstream 버전) --
COPY nginx.conf /etc/nginx/nginx.conf

# -- entrypoint + 오프라인 전용 프로비저닝 스크립트 + 파이프라인 소스 --
# 주의: 기존 setup.sh 는 이미지에 넣지 않는다. provision.sh 가
#       모든 오프라인 앱 프로비저닝 (Phase 4/5 해당) 을 독립적으로 수행한다.
COPY entrypoint.sh  /entrypoint.sh
COPY provision.sh       /opt/provision.sh
COPY zero_touch_qa                   /opt/zero_touch_qa
COPY dify-chatflow.yaml              /opt/dify-chatflow.yaml
COPY DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline /opt/DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline
COPY jenkins-init                    /opt/jenkins-init

# -- Jenkins 관리자 계정 그루비 스크립트를 seed 디렉토리에 배치 --
RUN mkdir -p /opt/seed/jenkins-home/init.groovy.d && \
    cp /opt/jenkins-init/*.groovy /opt/seed/jenkins-home/init.groovy.d/ && \
    chmod +x /entrypoint.sh /opt/provision.sh

# -- 포트 --
EXPOSE 18080 18081 50001

# -- 볼륨 --
VOLUME ["/data"]

# -- PID 1 은 tini (좀비 수거 + 시그널 포워딩) --
ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
