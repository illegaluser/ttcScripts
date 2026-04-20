# TTC 4-Pipeline All-in-One Integrated Image

4개 파이프라인을 **오프라인/폐쇄망**에서 독립 동작시키는 `docker compose` 단일 스택.

| # | 파이프라인 | 엔진 | Dify 사용 |
|---|-----------|------|-----------|
| 1 | 코드 사전학습 | `repo_context_builder.py` → Dify Knowledge Base | ✅ |
| 2 | 코드 정적분석 | SonarQube Community + SonarScanner CLI | ❌ |
| 3 | 정적분석 결과분석 + 이슈등록 | `sonar_issue_exporter` → `dify_sonar_issue_analyzer` (Dify Workflow) → `gitlab_issue_creator` | ✅ |
| 4 | AI 평가 | `eval_runner` (DeepEval + Ollama judge + Playwright) | ❌ |

---

## 1. 스택 구성

```
docker-compose.allinone.<wsl2|mac>.yaml
├── ttc-allinone  (통합 이미지: Jenkins + Dify + SonarQube + PG/Redis/Qdrant)
└── gitlab        (gitlab/gitlab-ce:17.4.2-ce.0)
```

기존 스택과 **완전 격리 · 공존 가능**:

| 스택 | 호스트 포트 |
|------|------|
| 루트 `docker-compose.yaml` | 8080 / 9000 / 8929 / 3000 / 50000 |
| `e2e-pipeline/docker-compose.yaml` (Playwright) | 18080 / 18081 / 50001 |
| **본 통합 스택** | **28080 / 28081 / 29000 / 28090 / 28022 / 50002** |

## 2. 호스트 전제

- **Docker Desktop** (macOS) 또는 **Docker Desktop + WSL2 백엔드** (Windows)
- 호스트에 **Ollama** 데몬 (Mac: Metal / Windows: NVIDIA CUDA)
  - `ollama serve`
  - `ollama pull gemma4:e4b`            (Dify Workflow 판단용)
  - `ollama pull qwen3-coder:30b`       (AI평가 judge, 선택)
- Docker 에 할당된 메모리 **≥ 12GB** (ttc-allinone 6GB + gitlab 4GB + 호스트)

---

## 3. 빌드

### WSL2 (Windows)
```bash
# WSL2 Ubuntu 셸, 레포 루트에서
bash scripts/build-wsl2.sh
# → 이미지: ttc-allinone:wsl2-dev
```

### macOS (Apple Silicon)
```bash
bash scripts/build-mac.sh
# Intel Mac 또는 amd64 강제:
bash scripts/build-mac.sh --amd64
```

### 오프라인 반출 (tarball)
온라인 머신에서:
```bash
bash scripts/offline-prefetch.sh --arch amd64   # 또는 --arch arm64
# → offline-assets/<arch>/ttc-allinone-*.tar.gz
```
오프라인 머신:
```bash
docker load -i offline-assets/amd64/ttc-allinone-amd64-dev.tar.gz
docker pull gitlab/gitlab-ce:17.4.2-ce.0   # 미리 save/load 해두거나 폐쇄망 내부 레지스트리
```

---

## 4. 기동

### 기본 (self-contained)
```bash
# WSL2
bash scripts/run-wsl2.sh
# Mac
bash scripts/run-mac.sh

# 수동:
docker compose -f docker-compose.allinone.wsl2.yaml up -d
```

### e2e-pipeline(Playwright) 연동 모드
```bash
# 1. e2e-pipeline 스택이 먼저 떠 있어야 함 (네트워크 e2e-net 생성)
cd e2e-pipeline && docker compose up -d && cd ..

# 2. 통합 스택을 e2e-net 에 조인
docker compose \
  -f docker-compose.allinone.wsl2.yaml \
  -f docker-compose.allinone.e2e-bridge.yaml \
  up -d
```

데이터 볼륨은 `${HOME}/ttc-allinone-data/{allinone,gitlab}` 에 생성됩니다.

첫 기동 시 **자동 프로비저닝**이 실행되며 15–20분 소요:
- GitLab 초기화(reconfigure) 5–10분
- Dify admin/Provider/Dataset/Workflow/API keys 생성 2분
- Jenkins Credentials 주입 + Job 4개 등록 1분

진행 상황: `docker logs -f ttc-allinone`

---

## 5. 접속

| 서비스 | URL | 초기 자격 | 용도 |
|--------|-----|-----------|------|
| Jenkins | http://localhost:28080 | `admin` / `password` | 4개 Pipeline Job 진입점 |
| Dify | http://localhost:28081 | `admin@ttc.local` / `TtcAdmin!2026` | Workflow/Dataset 확인·편집 |
| SonarQube | http://localhost:29000 | `admin` / `admin` (최초 로그인 시 변경) | 정적분석 대시보드 |
| GitLab | http://localhost:28090 | `root` / `ChangeMe!Pass` (env 로 override) | 소스 호스팅 + Issue |
| Ollama | http://host.docker.internal:11434 | (호스트 데몬) | LLM 추론 |

## 6. 완전 자동 프로비저닝 범위

`provision.allinone.sh` 가 최초 기동 시 자동 수행 (`/data/.provision/` 상태 캐시로 멱등):

| 대상 | 자동 작업 |
|------|-----------|
| **Dify** | 관리자 setup, 로그인, Ollama provider 등록, `code-context-kb` Dataset 생성, `Sonar Issue Analyzer` Workflow import, API key 2종 발급 |
| **GitLab** | oauth password grant → `users/1/personal_access_tokens` 로 root PAT 발급 |
| **Jenkins Credentials** | `dify-dataset-id`, `dify-knowledge-key`, `dify-workflow-key`, `gitlab-pat` 주입 |
| **Jenkins Jobs** | 4개 파이프라인 Job 등록 |
| **Jenkinsfile patch** | `GITLAB_PAT = ''` → `credentials('gitlab-pat')` 런타임 치환 |

### 자동화되지 않는 잔존 수동 작업

- **SonarQube 토큰**: 최초 로그인 시 비밀번호 변경 강제 + 토큰 발급은 브라우저 UI 기준. Jenkins Credential `sonarqube-token` 은 수동 등록.
- **GitLab 프로젝트 생성**: 파이프라인 1/2/3 이 분석할 대상 프로젝트는 팀 정책에 따라 수동 생성 또는 기존 소스 push.

---

## 7. 파이프라인 4 AI평가 — Playwright 아키텍처 옵션

`TARGET_TYPE=ui_chat` 일 때 UI 자동화가 필요합니다. 두 가지 경로 지원:

### (a) 로컬 Playwright — 기본, 추가 설정 불필요
통합 이미지에 Chromium + Playwright 설치본이 포함되어 있어 `browser_adapter` 가 컨테이너 내부 headless 모드로 즉시 동작합니다. e2e-pipeline 스택이 없어도 무관.

### (b) 원격 Playwright 위임 — e2e-pipeline 스택 활용
기존 e2e-pipeline 의 호스트 Jenkins agent (Mac Terminal / WSL2 Ubuntu) 는 WSLg/Metal 경유로 호스트 데스크탑에 **headed Chromium 창**을 띄울 수 있습니다. 시각 검증이 필요한 경우:

1. `docker-compose.allinone.e2e-bridge.yaml` 로 통합 스택을 `e2e-net` 에 조인
2. AI평가 Jenkinsfile 의 `TARGET_URL` 을 e2e Jenkins(18080) 의 downstream Pipeline 트리거로 변경
3. 또는 eval_runner `browser_adapter` 에 `REMOTE_BROWSER_URL=http://ttc-e2e-playwright:<port>` env 를 넘겨 CDP 연결 (별도 Playwright server 구성 필요)

권장: **(a) 기본** 사용. 시각 회귀가 필요할 때만 (b) 로 전환.

---

## 8. 파일 구성

```
Dockerfile.allinone                          # 통합 이미지 정의
docker-compose.allinone.wsl2.yaml            # WSL2 + gitlab
docker-compose.allinone.mac.yaml             # Mac + gitlab
docker-compose.allinone.e2e-bridge.yaml      # e2e-pipeline 네트워크 브리지 (override)
scripts/
  supervisord.allinone.conf                  # 11개 프로세스 관리
  nginx.allinone.conf                        # Dify gateway (28081)
  pg-init.allinone.sh                        # Postgres initdb (dify/dify_plugin/sonar)
  entrypoint.allinone.sh                     # 컨테이너 진입점
  provision.allinone.sh                      # 완전 자동 프로비저닝
  requirements-pipelines.txt                 # 파이프라인 4 Python deps
  offline-prefetch.sh                        # 온라인→오프라인 이미지 tarball
  build-wsl2.sh / build-mac.sh               # 빌드 헬퍼
  run-wsl2.sh / run-mac.sh                   # 기동 헬퍼
  dify-assets/
    sonar-analyzer-workflow.yaml             # 파이프라인 3 Workflow DSL
    code-context-dataset.json                # 파이프라인 1 Dataset 스펙
  jenkins-seed/                              # (future) JCasC seed
README-allinone.md                           # 본 문서
```

## 9. 기존 자산 재사용 관계

통합 이미지는 `e2e-pipeline/offline/` 의 자산을 직접 참조 (복사만, 수정 없음):

- `requirements-allinone.txt` — Python 기본 deps (playwright/deepeval 등)
- `jenkins-plugins/` — Jenkins .hpi seed
- `dify-plugins/` — Dify .difypkg seed
- `jenkins-init/basic-security.groovy` — admin 계정 초기화

기존 **`e2e-pipeline/offline/Dockerfile.allinone`** 과 **두 `docker-compose.yaml`** 은 **수정되지 않습니다**. 세 스택 병렬 공존 가능.

---

## 10. 트러블슈팅

| 증상 | 확인 | 대응 |
|------|------|------|
| SonarQube 기동 실패 | `docker exec ttc-allinone cat /data/logs/sonarqube.log` | ES mmap 관련이면 호스트 `sysctl -w vm.max_map_count=262144` (WSL2 는 `/etc/sysctl.conf` 영구화) |
| GitLab 계속 unhealthy | `docker logs ttc-gitlab` | reconfigure 는 5-10분 소요. `docker exec ttc-gitlab gitlab-ctl status` 로 각 서비스 확인 |
| Dify 자동 Workflow import 실패 | `/data/logs/provision.log` + `/data/.provision/` | 수동 import: Dify Studio → DSL 에서 가져오기 → `/opt/dify-assets/sonar-analyzer-workflow.yaml` |
| Jenkins Job 이 Ollama 에 도달 못함 | `docker exec ttc-allinone curl host.docker.internal:11434/api/tags` | Mac/Windows Docker Desktop 은 자동 해석. Linux 는 compose `extra_hosts` 로 매핑됨 |
| GitLab PAT 발급 실패 | `docker exec ttc-allinone bash /opt/provision.allinone.sh` 재실행 | 첫 실행 시 GitLab reconfigure 완료 전이면 발급 실패 가능. 15분 뒤 재시도 |
| Jenkins Credential 주입 반복 실패 | `docker logs ttc-allinone | grep Credential` | `rm /data/.provision/*` 후 provision 재실행 |

## 11. 재프로비저닝 (완전 초기화)

```bash
docker compose -f docker-compose.allinone.wsl2.yaml down
rm -rf ~/ttc-allinone-data   # 모든 상태 초기화
docker compose -f docker-compose.allinone.wsl2.yaml up -d
```

## 12. 스택 관계도

```
┌─────────────────────────────────────────────────────────┐
│  docker compose (ttc-net)                               │
│                                                          │
│  ┌──────────────────────┐        ┌──────────────────┐  │
│  │ ttc-allinone          │◄──────│ gitlab           │  │
│  │  - Jenkins  :28080   │        │  :28090 / :28022 │  │
│  │  - Dify     :28081   │        │                  │  │
│  │  - Sonar    :29000   │        └──────────────────┘  │
│  │  - PG/Redis/Qdrant    │                              │
│  │  - Chromium(Playwright)│                             │
│  └──────────┬────────────┘                              │
│             │                                            │
│             ▼ host.docker.internal:11434                │
└─────────────┼────────────────────────────────────────────┘
              │
              ▼
     ┌─────────────────┐
     │ 호스트 Ollama    │
     │ (Metal / CUDA)   │
     └─────────────────┘

옵션: docker-compose.allinone.e2e-bridge.yaml 시
  ttc-allinone 이 추가로 e2e-net 에 조인 →
  기존 e2e-pipeline Playwright Jenkins(18080) 와 직접 통신 가능
```
