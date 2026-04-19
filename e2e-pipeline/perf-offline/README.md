# JMeter Performance All-in-One

> 폐쇄망에서 **JMeter + jmeter.ai (Feather Wand)** 를 한 번에 기동하는
> 성능시험 컨테이너 번들. **Mac / Windows 양쪽 호스트** 지원.
> LLM(Ollama) 은 **호스트에서 실행** 되어 GPU 가속(Apple Metal/MLX, NVIDIA CUDA)을
> 그대로 활용한다.

```
┌──────── 호스트 (Mac M-series / Windows + NVIDIA / Linux) ──────────┐
│                                                                    │
│  🧠 Ollama (호스트 직접 설치)                                       │
│      ├─ 0.0.0.0:11434                                              │
│      ├─ GPU 가속 (Metal/MLX / CUDA / ROCm)                         │
│      └─ 모델: gemma4:e2b · qwen2.5:7b 등                           │
│                                                                    │
│  🌐 브라우저 → http://localhost:18090                               │
│       └─ noVNC (브라우저용 VNC 클라이언트)                          │
│            ↓ WebSocket                                             │
│  ┌──────── 단일 Docker 컨테이너 (perf-allinone) ───────────────┐   │
│  │   Xvfb :1  ← fluxbox  ← JMeter Swing GUI                  │   │
│  │     ↑                       ↓ HTTP                         │   │
│  │   x11vnc :5900           Feather Wand (jmeter.ai)         │   │
│  │     ↑                       ↓ HTTP                         │   │
│  │   websockify :6080      host.docker.internal:11434        │   │
│  │                              ↓ (Docker Desktop 자동 매핑)  │   │
│  │   /data 볼륨 (시나리오·결과 영속)                          │   │
│  └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ TL;DR — 폐쇄망 자동 설치

**번들 디렉토리 (USB)** 를 받았다면, 호스트에서:

```bash
# 🍎 Mac
bash install-mac.sh

# 🪟 Windows (관리자 PowerShell)
.\install-windows.ps1
```

스크립트가 **Ollama 설치 → 환경변수 → 모델 복원 → 컨테이너 기동 → 브라우저 열기** 까지
한 번에 처리합니다. (Docker Desktop 만 사전 설치 필요)

자세한 OS별 절차/트러블슈팅:

| 사용 호스트 | 가이드 |
|---|---|
| 🍎 **Mac** (Apple Silicon / Intel) | **[README-mac.md](README-mac.md)** |
| 🪟 **Windows** (Docker Desktop + WSL2) | **[README-windows.md](README-windows.md)** |

---

## 📚 문서 구조

이 README는 **공통 개요 + 번들 제작 + JMeter 심화 사용법**을 다룹니다.

---

## 1. 무엇이 들어있나

### 1.1 컨테이너 이미지에 포함된 것
| 컴포넌트 | 버전 | 역할 |
|---|---|---|
| **Apache JMeter** | 5.6.3 | 성능시험 엔진 (GUI + CLI) |
| **JMeter Plugins Manager** | 1.12 | 플러그인 자동 설치/관리 |
| **JMeter 일반 부하시험 플러그인** | (jmeter-plugins.org) | Custom Thread Groups, Throughput Shaping Timer, 그래프 4종, PerfMon, FIFO, Auto-Stop 등 (총 ~20종) |
| **WebSocket Samplers** | Peter Doornbosch | WebSocket 부하시험용 |
| **Feather Wand (jmeter.ai)** | 2.0.4 | LLM 기반 시나리오 자동 생성/분석 어시스턴트 |
| **Xvfb / fluxbox / x11vnc / noVNC 1.6.0** | — | 브라우저에서 JMeter Swing GUI 사용 |
| **nginx / supervisord / tini** | — | 프로세스 관리 + 게이트웨이 |

### 1.2 호스트에 별도 설치 필요한 것
| 컴포넌트 | 역할 | 설치 가이드 |
|---|---|---|
| **Docker Desktop** | 컨테이너 런타임 | [README-mac](README-mac.md#22) / [README-windows](README-windows.md#22) |
| **Ollama** | LLM 서버 (호스트 GPU 활용) | [README-mac §1](README-mac.md#1-ollama-호스트-설치-필수) / [README-windows §1](README-windows.md#1-ollama-호스트-설치-필수) |
| **LLM 모델** | jmeter.ai가 호출할 모델 | 위 가이드의 "모델 반입" 절 |

### 1.3 두 가지 운영 모드
| 모드 | 사용 시점 | 접근 방법 |
|---|---|---|
| 🖱️ **GUI (Web)** | 평상시 시나리오 작성·디버깅 | 브라우저 → `http://localhost:18090` |
| ⚡ **CLI (non-GUI)** | 정식 부하시험 측정 | `docker exec ... jmeter -n -t ... -l ...` |

> 정식 측정 시에는 GUI를 내려두는 것을 **강력 권장**.
> Swing UI의 GC/이벤트 처리 오버헤드로 측정 정확도가 5-15% 저하될 수 있습니다.

### 1.4 외부 노출 포트
| 포트 | 용도 | 외부 노출 권장? |
|---|---|---|
| **18090** | nginx 통합 게이트웨이 (`/` noVNC + `/ollama/` 호스트 Ollama proxy) | ✅ 사내망 한정 |
| **11434** | (호스트 직접) Ollama API | ⚠️ 호스트가 직접 들고 있음, 같은 호스트의 컨테이너만 사용 |

> 컨테이너는 **포트 18090 만 매핑**합니다. 11434는 호스트 Ollama가 직접 들고 있고,
> 컨테이너는 `host.docker.internal:11434` 로 호출합니다.

### 1.5 트레이드오프
| 항목 | 결과 |
|---|---|
| 이미지 크기 | 2-3GB (압축 전) / 1-1.5GB (gzip 압축) — 모델 미포함 |
| `docker run` 횟수 | 1회 |
| 첫 기동 시간 | 30초 ~ 1분 (seed 복사) |
| 이후 기동 시간 | 5-10초 |
| 호스트 RAM 권장 | 16GB+ (호스트 Ollama 추론 + JMeter 컨테이너) |
| 호스트 디스크 권장 | 20GB+ (이미지 + Ollama 모델 + 결과 데이터) |

---

## 2. LLM 모델 권장 가이드

호스트 GPU/메모리에 따라 권장 모델이 다릅니다. **gemma4:e2b** 가 기본값(균형) 입니다.

### 2.1 Apple Silicon (Metal/MLX 가속)

통합 메모리 (unified memory) 기준:

| 호스트 RAM | 권장 모델 | 모델 크기 | 응답 속도 (대략) |
|---|---|---|---|
| **8 GB** | `gemma4:e2b` (기본) | 7.2 GB | 30-50 토큰/초 |
| **16 GB** | `gemma4:e2b` 또는 `qwen2.5:7b` | 4-7 GB | 40-80 토큰/초 |
| **24-32 GB** | `qwen2.5:7b`, `llama3.1:8b`, `deepseek-r1:7b` | 5-8 GB | 60-100 토큰/초 |
| **64 GB+** | `qwen2.5:14b`, `gemma2:27b`, `deepseek-r1:14b` | 10-20 GB | 30-60 토큰/초 |

> M5/M5 Pro/Max는 MLX 프레임워크로 GPU Neural Accelerator 활용 → 같은 모델에서
> 1.5-2배 더 빠름.

### 2.2 Windows + NVIDIA (CUDA 가속)

VRAM 기준 (GPU 메모리, RAM 아님):

| GPU VRAM | 권장 모델 | 모델 크기 | 응답 속도 (대략) |
|---|---|---|---|
| **6 GB** (RTX 3060 / 4060) | `gemma4:e2b` | 7.2 GB (일부 RAM 오프로드) | 50-80 토큰/초 |
| **8 GB** (RTX 4060 Ti / 4070) | `gemma4:e2b`, `qwen2.5:7b` (4-bit) | 4-7 GB | 80-150 토큰/초 |
| **12 GB** (RTX 4070 Ti / 4070 S) | `qwen2.5:7b`, `deepseek-r1:7b` | 5-8 GB | 100-180 토큰/초 |
| **16 GB+** (RTX 4080 / 4090 / 5090) | `qwen2.5:14b`, `deepseek-r1:14b`, `gemma2:27b` | 10-20 GB | 60-120 토큰/초 |

### 2.3 모델 선택 팁
- **jmeter.ai 시나리오 자동 생성**: 7B급 instruct 모델이 균형 좋음 (`qwen2.5:7b` 추천)
- **빠른 설명/디버깅**: `gemma4:e2b` 가 충분히 빠르고 정확
- **복잡한 다중 단계 추론**: 14B+ 모델 (`deepseek-r1:14b` 의 thinking 모드 활용)
- **한국어 응답 품질**: `qwen2.5:*` 가 우수, `gemma4:*` 는 보통

### 2.4 모델 변경 절차

호스트에서:
```bash
ollama pull qwen2.5:7b      # 모델 다운로드
ollama list                  # 사용 가능 모델 확인
```

컨테이너의 jmeter.ai 설정 변경:
```bash
docker exec perf-allinone sed -i \
  's|^ollama.default.model=.*|ollama.default.model=qwen2.5:7b|' \
  /opt/apache-jmeter/bin/user.properties
docker exec perf-allinone supervisorctl restart jmeter-gui
```

---

## 3. 폐쇄망 배포 흐름 (전체 그림)

```
┌──────── 온라인 빌드 머신 ────────┐
│  1) prepare-bundle.sh 실행        │
│     ├─ 컨테이너 이미지 빌드        │
│     ├─ Ollama DMG/ZIP 다운로드    │
│     ├─ 모델 archive 제작          │
│     └─ 단일 번들 디렉토리 산출    │
│                                   │
│  📁 perf-allinone-bundle-<TS>/    │
│       ├─ install-mac.sh           │
│       ├─ install-windows.ps1      │
│       ├─ assets/ (모든 자산)      │
│       └─ README*.md               │
└─────────────┬─────────────────────┘
              │ USB 반입
              ↓
┌──────── 폐쇄망 호스트 ───────────┐
│  2) install-mac.sh 또는           │
│     install-windows.ps1 실행      │
│                                   │
│  자동 설치 단계:                  │
│   ① Docker Desktop 검증           │
│   ② Ollama 설치                   │
│   ③ 환경변수 + 서비스 등록        │
│   ④ 모델 복원                     │
│   ⑤ 컨테이너 로드/기동            │
│   ⑥ 브라우저 자동 열기            │
│                                   │
│  → http://localhost:18090         │
└───────────────────────────────────┘
```

### 3.1 빠른 시작 (이미 번들이 있을 때)

```bash
# Mac
bash install-mac.sh

# Windows (관리자 PowerShell)
.\install-windows.ps1
```

### 3.2 수동 기동 (스크립트 없이)

번들 자동화를 우회하려면 [README-mac §4](README-mac.md#4-컨테이너-기동) /
[README-windows §4](README-windows.md#4-컨테이너-기동) 의 docker run 절차 참조.

---

## 4. 빌드 / 번들 제작 (온라인 머신 전용)

### 4.1 빌드 호스트 요구사항
| 항목 | 최소 | 권장 |
|---|---|---|
| Docker | 26.0 | 26.x 이상 |
| Docker buildx | 활성 | 활성 |
| 디스크 여유 | 15GB (단일 아키) | 30GB (양 아키텍처) |
| 네트워크 | 외부망 가능 | — |
| 빌드 시간 | 5-15분/아키 | — |

### 4.2 외부 네트워크 도메인 (방화벽 화이트리스트)

| 도메인 | 용도 |
|---|---|
| `registry-1.docker.io` | base 이미지 (eclipse-temurin) |
| `archive.apache.org` | Apache JMeter 바이너리 |
| `repo1.maven.org` | jmeter-plugins-manager.jar, cmdrunner.jar |
| `jmeter-plugins.org` | Plugins Manager 카탈로그 + JAR (jpgc-* + feather-wand-*) |
| `github.com` / `objects.githubusercontent.com` | noVNC release tarball |
| `*.ubuntu.com` | apt (xvfb, fluxbox, nginx 등) |
| `pypi.org` / `files.pythonhosted.org` | websockify |

### 4.3 번들 제작 (권장 — 자동 설치 스크립트와 함께 패키징)

```bash
cd e2e-pipeline

# 양 아키텍처 + Ollama 자산 + 모델 archive 모두 포함한 단일 번들
./perf-offline/prepare-bundle.sh
# → perf-allinone-bundle-<TS>/  생성

# 단일 .tar.gz 로도 묶기 (USB 운반에 편리)
PACK_TGZ=1 ./perf-offline/prepare-bundle.sh
# → perf-allinone-bundle-<TS>.tar.gz
```

prepare-bundle.sh 환경변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TARGET_PLATFORMS` | `linux/amd64,linux/arm64` | 컨테이너 이미지 아키텍처 |
| `OLLAMA_MODELS` | `gemma4:e2b` | 콤마 구분 다중 모델 (`gemma4:e2b,qwen2.5:7b`) |
| `SKIP_IMAGE_BUILD` | `0` | 이미 빌드된 이미지 tar.gz 재사용 |
| `SKIP_OLLAMA_DMG` | `0` | Mac 배포 안 할 때 |
| `SKIP_OLLAMA_WIN` | `0` | Windows 배포 안 할 때 |
| `SKIP_MODEL_PULL` | `0` | 모델 archive 별도 제공 시 |
| `PACK_TGZ` | `0` | 마지막에 단일 tar.gz 로 묶기 |

산출 번들 디렉토리:
```
perf-allinone-bundle-20260420-103000/
├── install-mac.sh
├── install-windows.ps1
├── README.md / README-mac.md / README-windows.md
├── BUNDLE_INFO.txt
└── assets/
    ├── dscore-qa-perf-allinone-amd64-*.tar.gz
    ├── dscore-qa-perf-allinone-arm64-*.tar.gz
    ├── Ollama.dmg
    ├── ollama-windows-amd64.zip
    ├── nssm-2.24.zip
    ├── ollama-models-gemma4_e2b-*.tgz
    └── SHA256SUMS
```

### 4.4 컨테이너 이미지만 빌드 (번들 없이)

```bash
# AMD64 단독
./perf-offline/build-allinone.sh

# ARM64 단독
TARGET_PLATFORMS=linux/arm64 ./perf-offline/build-allinone.sh

# 양쪽
TARGET_PLATFORMS=linux/amd64,linux/arm64 ./perf-offline/build-allinone.sh
```

### 4.5 build-allinone.sh 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `IMAGE_TAG` | `dscore-qa-perf:allinone` | base tag (실제 태그는 `-amd64`/`-arm64` 자동 부착) |
| `TARGET_PLATFORMS` | `linux/amd64` | 콤마 구분 다중 지정 가능 |
| `JMETER_VERSION` | `5.6.3` | Apache JMeter 버전 |
| `JMETER_PM_VERSION` | `1.12` | jmeter-plugins-manager 버전 |
| `NOVNC_VERSION` | `1.6.0` | noVNC release 태그 |
| `OUTPUT_DIR` | `e2e-pipeline/` | tar.gz 산출 디렉토리 |

---

## 5. JMeter 사용법 (심화)

> 이 절은 OS와 무관하게 컨테이너 안의 JMeter를 어떻게 활용하는가에 대한 가이드입니다.
> 호스트별 접속·기동 절차는 [README-mac.md](README-mac.md) / [README-windows.md](README-windows.md) 를 먼저 참조.

### 5.1 GUI에서 시나리오 작성 (Feather Wand 활용)

1. 브라우저로 `http://localhost:18090` → noVNC 자동연결 → JMeter GUI
2. **Test Plan** (좌측 트리) 우클릭 → **Add → Threads (Users) → bzm - Concurrency Thread Group**
3. Concurrency Thread Group에서:
   - **Target Concurrency**: 동시 사용자 수 (예: 50)
   - **Ramp Up Time**: 도달까지 걸릴 초 (예: 30)
   - **Hold Target Rate Time**: 유지 시간 (예: 300 = 5분)
4. 우클릭 → **Add → Sampler → HTTP Request**
   - **Server Name**: 대상 서버 (예: `target.example.com`)
   - **Port**: `80` 또는 `443`
   - **Path**: `/api/...`
   - **Method**: `GET` / `POST` 등
5. 우클릭 → **Add → Listener → bzm - Transactions per Second** (등 그래프)
6. **`Ctrl+S`** 로 `/data/jmeter/scenarios/<name>.jmx` 저장

#### 🪶 Feather Wand (jmeter.ai) 활용

JMeter GUI의 우측 패널 또는 우클릭 메뉴에서 **Feather Wand** 호출:

| 기능 | 설명 | 예시 프롬프트 |
|---|---|---|
| **Generate** | 자연어로 시나리오 요청 | "5분 동안 100명 사용자가 https://example.com/api/login 에 1초마다 POST" |
| **Explain** | 선택한 요소 설명 | (HTTP Request 우클릭 → Explain) |
| **Optimize** | 시나리오 최적화 제안 | "이 테스트에서 메모리 사용을 줄여줘" |
| **Debug** | 실패 원인 분석 | "왜 401 에러가 50% 나는지 분석해줘" |

기본 LLM은 **호스트 Ollama** 의 `gemma4:e2b` — **호스트 GPU 가속** 사용.
다른 모델로 교체는 [§2.4 모델 변경 절차](#24-모델-변경-절차) 참조.

### 5.2 변수와 파라미터화

#### A. User Defined Variables (정적 변수)
Test Plan 직속에 추가:
```
HOST = api.example.com
TOKEN = abc123
```
Sampler 안에서 `${HOST}`, `${TOKEN}` 으로 참조.

#### B. Property 함수 (CLI에서 주입)
시나리오 안에 `${__P(threads,10)}` 처럼 작성하면, CLI 실행 시 `-Jthreads=50` 으로 덮어쓰기 가능.
샘플 시나리오 [`scenarios-seed/sample-http-get.jmx`](scenarios-seed/sample-http-get.jmx) 가 이 패턴 예시.

#### C. CSV Data Set Config (대량 입력 데이터)
1. 컨테이너에 CSV 업로드:
   ```bash
   docker cp users.csv perf-allinone:/data/jmeter/scenarios/users.csv
   ```
2. Thread Group 우클릭 → **Add → Config Element → CSV Data Set Config**
   - **Filename**: `/data/jmeter/scenarios/users.csv`
   - **Variable Names**: `username,password`
3. Sampler 안에서 `${username}`, `${password}` 사용 (각 스레드/반복마다 다음 행)

### 5.3 어설션 (응답 검증)

대표 3가지:

| 어설션 | 용도 | 위치 |
|---|---|---|
| **Response Assertion** | HTTP 코드 / 응답 본문 문자열 매치 | Sampler 직속 |
| **JSON Assertion** | JSON 응답의 특정 path 값 검증 | Sampler 직속 |
| **Duration Assertion** | 응답 시간 SLO (예: 500ms 초과 시 fail) | Sampler 직속 |

샘플:
```
Response Assertion
  Field to Test:    Response Code
  Pattern Matching: Equals
  Pattern:          200
```

### 5.4 정식 부하시험 (CLI 실행)

```bash
# (선택) GUI 내리기 — 측정 정확도 향상
docker exec perf-allinone supervisorctl stop jmeter-gui

# CLI 실행 (Summary Report + HTML Dashboard 동시 생성)
TS=$(date +%Y%m%d-%H%M%S)
docker exec perf-allinone jmeter -n \
  -t /data/jmeter/scenarios/<name>.jmx \
  -l /data/jmeter/results/<name>-${TS}.jtl \
  -e -o /data/jmeter/reports/<name>-${TS}/ \
  -Jhost=target.example.com -Jport=443 -Jpath=/api/health \
  -Jthreads=50 -Jramp=30 -Jloops=200

# 결과 호스트로 가져오기
docker cp perf-allinone:/data/jmeter/reports/<name>-${TS}/ ./
docker cp perf-allinone:/data/jmeter/results/<name>-${TS}.jtl ./
```

#### CLI 옵션 핵심
| 옵션 | 설명 |
|---|---|
| `-n` | non-GUI (CLI) 모드 (필수) |
| `-t <file>.jmx` | 실행할 시나리오 |
| `-l <file>.jtl` | 원시 결과 (CSV) |
| `-e` | 실행 후 HTML Dashboard 자동 생성 |
| `-o <dir>` | Dashboard 출력 경로 (반드시 비어 있어야 함) |
| `-J<key>=<value>` | 시나리오의 `${__P(key,...)}` 주입 |
| `-Gkey=value` | 분산 모드에서 원격 노드까지 전파 |
| `-l ... -j <log>` | 별도 jmeter.log 위치 지정 |

### 5.5 HTML Dashboard 활용

`-e -o <dir>` 로 생성된 HTML 리포트를 호스트로 가져와 브라우저로 열기:

```bash
docker cp perf-allinone:/data/jmeter/reports/<name>-${TS}/ ./reports/
open ./reports/<name>-${TS}/index.html        # Mac
start .\reports\<name>-${TS}\index.html       # Windows PowerShell
```

핵심 화면:
- **Statistics** — Sampler별 평균/90%ile/95%ile/99%ile 응답시간, 에러율
- **Errors** — 실패 응답 분포
- **Throughput** — 시간대별 TPS / 응답 시간 추이
- **Response Time Percentiles Over Time** — SLO 위반 구간 식별

#### Dashboard 커스터마이징
`/opt/apache-jmeter/bin/reportgenerator.properties` 일부 키:
```
jmeter.reportgenerator.overall_granularity=60000   # 그래프 granularity (ms)
jmeter.reportgenerator.apdex_satisfied_threshold=500
jmeter.reportgenerator.apdex_tolerated_threshold=1500
```

### 5.6 추천 플러그인 사용 가이드

#### Custom Thread Groups (`jpgc-casutg`)
| Thread Group 종류 | 사용 시점 |
|---|---|
| **Concurrency Thread Group** | 동시 사용자 수를 시간에 따라 변화 (가장 자주 사용) |
| **Stepping Thread Group** | 사용자를 N명씩 단계적으로 추가 (계단형 부하) |
| **Ultimate Thread Group** | 복잡한 부하 패턴 (Spike, Soak 등 multi-stage) |

#### Throughput Shaping Timer (`jpgc-tst`)
원하는 RPS(Requests Per Second) 를 시간대별로 정확히 제어. 예: 0-60초 100rps → 60-300초 500rps.

#### PerfMon (`jpgc-perfmon`)
대상 서버의 CPU/Memory/Disk/Network를 부하시험과 동시 수집. 대상 서버에 `ServerAgent` 설치 필요.

#### Auto-Stop Listener (`jpgc-autostop`)
조건 만족 시 테스트 자동 종료 (예: 에러율 5% 초과, 평균 응답시간 5초 초과).

### 5.7 jmeter.ai (Feather Wand) 설정 키

`/opt/apache-jmeter/bin/user.properties` 에 사전 등록된 키 (모두 `ollama.*` 네임스페이스):

| 키 | 기본값 | 설명 |
|---|---|---|
| `ollama.host` | `http://host.docker.internal` | **호스트 Ollama** (Docker Desktop 자동 매핑) |
| `ollama.port` | `11434` | Ollama 포트 |
| `ollama.default.model` | `gemma4:e2b` | 기본 모델 태그 |
| `ollama.temperature` | `0.5` | 창의성 (0.0–1.0) |
| `ollama.max.history.size` | `10` | 대화 히스토리 길이 |
| `ollama.thinking.mode` | `DISABLED` | `ENABLED` \| `DISABLED` (지원 모델만) |
| `ollama.thinking.level` | `MEDIUM` | `LOW` \| `MEDIUM` \| `HIGH` |
| `ollama.request.timeout.seconds` | `300` | 요청 타임아웃 |
| `ollama.system.prompt` | (한국어 어시스턴트 프롬프트) | 시스템 프롬프트 |

> 출처: [jmeter-ai-sample.properties](https://github.com/QAInsights/jmeter-ai/blob/main/jmeter-ai-sample.properties)

운영 중 변경:
```bash
docker exec -it perf-allinone vi /opt/apache-jmeter/bin/user.properties
docker exec perf-allinone supervisorctl restart jmeter-gui
```

---

## 6. 디렉토리 구조

### 6.1 빌드 디렉토리 (이 저장소)
```
e2e-pipeline/perf-offline/
├── README.md                    # 이 문서 — 공통 개요 + 번들/빌드 + JMeter 심화
├── README-mac.md                # 🍎 Mac 사용자용 설치/운영 가이드
├── README-windows.md            # 🪟 Windows 사용자용 설치/운영 가이드
│
├── prepare-bundle.sh            # 🛠️ 온라인 머신 — 폐쇄망 배포 번들 자동 제작
├── install-mac.sh               # 🍎 폐쇄망 Mac — 자동 설치 진입점
├── install-windows.ps1          # 🪟 폐쇄망 Windows — 자동 설치 진입점
│
├── Dockerfile.allinone          # 단일 스테이지 빌드 정의 (Ollama 미포함)
├── build-allinone.sh            # 컨테이너 이미지만 빌드 (양 아키텍처 지원)
├── entrypoint-allinone.sh       # /data seed + 호스트 Ollama 점검 + supervisord
├── supervisord.conf             # 6개 프로세스 매니페스트 (Ollama 제외)
├── nginx-perf.conf              # :18090 통합 게이트웨이 (호스트 Ollama proxy)
├── jmeter-plugin-list.txt       # PluginsManagerCMD 사전 설치 목록
└── scenarios-seed/              # 첫 기동 시 /data/jmeter/scenarios 로 복사
    └── sample-http-get.jmx
```

### 6.2 컨테이너 내부 (`/data` 볼륨)
| 경로 | 내용 | 사용자 변경 |
|---|---|---|
| `/data/jmeter/scenarios/` | `.jmx` 시나리오 | ✅ `docker cp` / GUI 저장 |
| `/data/jmeter/results/` | `.jtl` 원시 결과 | (CLI 결과물) |
| `/data/jmeter/reports/` | HTML Dashboard | (CLI 결과물) |
| `/data/jmeter/lib-ext/` | 사용자 추가 `.jar` | ✅ 재기동 시 자동 인식 |
| `/data/logs/` | 모든 서비스 로그 | (관찰용) |

> Ollama 모델 디렉토리는 **호스트** 에 있습니다 (`~/.ollama/models/` 등).

---

## 7. 보안 권고

이 번들의 기본 설정은 **사내 신뢰망 한정** 사용을 전제로 합니다.

| 위험 | 완화 |
|---|---|
| noVNC 인증 없음 (`x11vnc -nopw`) | localhost 또는 사내망 한정 노출. 외부 노출 시 nginx에 basic auth 추가 또는 VPN 뒤로 |
| 호스트 Ollama API 인증 없음 | `OLLAMA_HOST=0.0.0.0:11434` 시 사내망 한정. 외부 노출 시 reverse proxy + 인증 필수 |
| Ollama 자동 업데이트 트리거 | 폐쇄망 방화벽에서 `ollama.com` outbound 차단 (5단계 권장 절차의 마지막) |
| Feather Wand 시스템 프롬프트 평문 저장 | 민감 정보 포함 금지 |
| 컨테이너 내부 root 권한 | 호스트 보안 영향 없음 (명시적 mount 외에는 차단) |

---

## 8. 라이선스 / 출처

| 컴포넌트 | 라이선스 | 공식 |
|---|---|---|
| Apache JMeter | Apache 2.0 | https://jmeter.apache.org/ |
| JMeter Plugins | Apache 2.0 / 개별 | https://jmeter-plugins.org/ |
| Feather Wand (jmeter.ai) | Apache 2.0 | https://github.com/QAInsights/jmeter-ai |
| Ollama | MIT | https://ollama.com/ |
| Gemma 4 | Apache 2.0 | https://ollama.com/library/gemma4 |
| Qwen 2.5 | Apache 2.0 | https://ollama.com/library/qwen2.5 |
| DeepSeek R1 | MIT (distill) | https://ollama.com/library/deepseek-r1 |
| noVNC | MPL 2.0 | https://github.com/novnc/noVNC |
