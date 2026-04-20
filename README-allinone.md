# TTC 4-Pipeline All-in-One Integrated Image

4개 파이프라인을 **단일 컨테이너**에서 실행하는 오프라인/폐쇄망 배포판.

| # | 파이프라인 | 엔진 |
|---|-----------|------|
| 1 | 코드 사전학습 | `repo_context_builder.py` → Dify KB |
| 2 | 코드 정적분석 | SonarQube Community + SonarScanner CLI |
| 3 | 정적분석 결과분석 + 이슈등록 | `sonar_issue_exporter` → `dify_sonar_issue_analyzer` → `gitlab_issue_creator` |
| 4 | AI 평가 | `eval_runner` + `zero_touch_qa` (Playwright) |

## 기존 스택과의 관계

| 스택 | 포트 | 건드리지 않음 |
|------|------|---------------|
| 루트 `docker-compose.yaml` | 8080 / 9000 / 8929 / 3000 / 50000 | ✅ |
| `e2e-pipeline/docker-compose.yaml` (Playwright 포함) | 18080 / 18081 / 50001 | ✅ |
| **본 통합 이미지** | **28080 / 28081 / 29000 / 50002** | (신규) |

세 스택을 동시에 기동해도 포트/네트워크 충돌이 없다.

## 호스트 전제

- **Docker Desktop** (Mac) 또는 **Docker Desktop + WSL2 백엔드** (Windows)
- 호스트에 **Ollama** 데몬 실행 (Mac: Metal GPU / Windows: NVIDIA CUDA)
  - `ollama pull gemma4:e4b`
- 외부 **GitLab** 은 API 호출만 (이미지에 포함하지 않음)
- Jenkins **agent** 는 호스트 JNLP 로 연결 (Playwright 가 호스트 데스크탑에 Chromium 창 표시)

## 빌드

### WSL2 (Windows)
```bash
# WSL2 Ubuntu 셸에서, 레포 루트
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
온라인 머신에서 이미지를 만든 뒤 tarball 로 말아 옮긴다.
```bash
bash scripts/offline-prefetch.sh --arch amd64   # 또는 --arch arm64
# → offline-assets/<arch>/ttc-allinone-*.tar.gz
```
오프라인 머신:
```bash
docker load -i offline-assets/amd64/ttc-allinone-amd64-dev.tar.gz
```

## 기동

### WSL2
```bash
bash scripts/run-wsl2.sh
# 또는: docker compose -f docker-compose.allinone.wsl2.yaml up -d
```

### Mac
```bash
bash scripts/run-mac.sh
```

데이터 볼륨은 `${HOME}/ttc-allinone-data` 에 생성된다.

## 접속

| 서비스 | URL | 초기 자격 |
|--------|-----|-----------|
| Jenkins | http://localhost:28080 | admin / password |
| Dify | http://localhost:28081 | `/install` 에서 최초 관리자 생성 |
| SonarQube | http://localhost:29000 | admin / admin (최초 로그인 시 변경 강제) |
| Jenkins Agent (JNLP) | localhost:50002 | 호스트 agent.jar 로 연결 |
| Ollama | http://host.docker.internal:11434 | 호스트 데몬 |

## 최초 설정 (컨테이너 기동 후)

프로비저닝 스크립트(`provision.allinone.sh`)는 Jenkins Job 4개를 자동 등록한다. 나머지는 수동:

1. **Dify** — `/install` 관리자 계정 생성 → Chatflow import (`/opt/dify-chatflow.yaml`) → API key 발급
2. **SonarQube** — 로그인 후 비밀번호 변경 → User → My Account → Security → 토큰 발급
3. **Jenkins Credentials** — Manage Jenkins → Credentials → 다음 항목 등록
   - `dify-api-key` (Secret text)
   - `sonarqube-token` (Secret text)
   - `gitlab-token` (Secret text, Personal Access Token)
4. **Ollama** (호스트) — `ollama serve` + `ollama pull gemma4:e4b`

## 파일 구성

```
Dockerfile.allinone                         # 통합 이미지 정의
docker-compose.allinone.wsl2.yaml           # WSL2 기동
docker-compose.allinone.mac.yaml            # Mac 기동
scripts/
  supervisord.allinone.conf                 # 11개 프로세스
  nginx.allinone.conf                       # Dify gateway (28081)
  pg-init.allinone.sh                       # Postgres initdb (dify/dify_plugin/sonar)
  entrypoint.allinone.sh                    # 컨테이너 진입점
  provision.allinone.sh                     # Jenkins Job 자동 등록
  requirements-pipelines.txt                # 파이프라인 4 Python deps
  offline-prefetch.sh                       # 온라인→오프라인 이미지 tarball
  build-wsl2.sh / build-mac.sh              # 플랫폼별 빌드 헬퍼
  run-wsl2.sh / run-mac.sh                  # 플랫폼별 기동 헬퍼
  jenkins-seed/                             # (future) JCasC seed
README-allinone.md                          # 본 문서
```

## 기존 자산 재사용

통합 이미지는 `e2e-pipeline/offline/` 의 자산을 많이 재사용한다:
- `requirements-allinone.txt` — Python 기본 의존성
- `jenkins-plugins/` — Jenkins .hpi 바이너리 seed
- `dify-plugins/` — Dify .difypkg seed
- `jenkins-init/*.groovy` — 관리자 계정 초기화

기존 `e2e-pipeline/offline/Dockerfile.allinone` 및 `docker-compose.yaml` 은 **수정되지 않는다**. 두 스택은 병렬로 공존 가능.

## 트러블슈팅

- **SonarQube 가 안 뜸** — `docker exec ttc-allinone cat /data/logs/sonarqube.log`. Elasticsearch 관련 "max virtual memory areas" 에러라면 호스트에서 `sysctl -w vm.max_map_count=262144` (WSL2 는 `/etc/sysctl.conf` 에 영구화).
- **Dify 가 Ollama 에 도달 못함** — `docker exec ttc-allinone curl http://host.docker.internal:11434/api/tags` 로 확인. WSL2 에서 `extra_hosts` 가 필요.
- **Jenkins Job 이 등록 안 됨** — `docker exec ttc-allinone bash /opt/provision.allinone.sh` 수동 재실행.
