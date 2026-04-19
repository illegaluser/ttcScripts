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

## 📦 폐쇄망 반영 사전 준비물

폐쇄망 호스트에 반입하기 **전**, 온라인 빌드 머신에서 아래 자산들을 확보해야 합니다.
`prepare-bundle.sh` 가 자동으로 패키징해 주지만, 수동 반입 시 체크리스트로 활용하세요.

### A. 온라인 빌드 머신에서 준비할 것

| # | 품목 | 용도 | 획득 방법 | 대략 크기 |
|---|---|---|---|---|
| 1 | **컨테이너 이미지 tar.gz** (amd64 / arm64) | JMeter + Feather Wand 본체 | `./perf-offline/build-allinone.sh` | 1.0-1.5 GB/아키 |
| 2 | **Ollama 설치본 (Mac)** — `Ollama.dmg` | Mac 호스트용 LLM 런타임 | https://ollama.com/download/mac | ~200 MB |
| 3 | **Ollama 설치본 (Windows)** — `ollama-windows-amd64.zip` | Windows 호스트용 LLM 런타임 | https://ollama.com/download/windows | ~300 MB |
| 4 | **NSSM** — `nssm-2.24.zip` | Windows 서비스 등록 도구 | https://nssm.cc/download | ~400 KB |
| 5 | **LLM 모델 archive** — `ollama-models-*.tgz` | 폐쇄망 Ollama 가 로드할 모델 | `ollama pull <model>` → `~/.ollama/models/` 압축 | 4-20 GB/모델 |
| 6 | **install-mac.sh / install-windows.ps1** | 자동 설치 진입점 | 이 저장소 | 수 KB |
| 7 | **README*.md** | 사용 가이드 | 이 저장소 | 수 KB |
| 8 | **SHA256SUMS** | 무결성 검증 | `prepare-bundle.sh` 가 자동 생성 | 수 KB |

### B. 폐쇄망 호스트에 이미 있어야 할 것

| 품목 | 용도 | 비고 |
|---|---|---|
| **Docker Desktop** | 컨테이너 런타임 | 사전 설치 필수 — 반입 번들에 포함 안 됨 (라이선스/OS 이슈) |
| **호스트 OS** | Mac 12+ / Windows 11 + WSL2 | Docker Desktop 요구사항과 동일 |
| **디스크 여유** | 이미지 + 모델 + 결과 저장 | 20 GB 이상 권장 |
| **RAM** | JMeter + Ollama 동시 가동 | 16 GB 이상 권장 |
| **GPU (선택)** | LLM 추론 가속 | Apple Silicon (Metal/MLX) 또는 NVIDIA (CUDA) |

### C. 외부망 화이트리스트 (온라인 빌드 머신 전용)

방화벽이 있는 빌드 환경에서는 아래 도메인을 outbound 허용해야 합니다.

| 도메인 | 용도 |
|---|---|
| `registry-1.docker.io` | base 이미지 (eclipse-temurin) |
| `archive.apache.org` | Apache JMeter 바이너리 |
| `repo1.maven.org` | jmeter-plugins-manager.jar, cmdrunner.jar |
| `jmeter-plugins.org` | Plugins Manager 카탈로그 + JAR (jpgc-* + feather-wand-*) |
| `github.com` / `objects.githubusercontent.com` | noVNC release tarball |
| `*.ubuntu.com` | apt (xvfb, fluxbox, nginx 등) |
| `pypi.org` / `files.pythonhosted.org` | websockify |
| `ollama.com` / `registry.ollama.ai` | Ollama 설치본 + 모델 `pull` |

> 폐쇄망 호스트에서는 위 도메인이 **차단되어 있어도 정상 동작** 해야 합니다.
> 특히 `ollama.com` 은 자동 업데이트 체크를 유발하므로 **반입 후에도 차단 유지 권장**.

### D. USB 반입 번들 예시 구조

`PACK_TGZ=1 ./perf-offline/prepare-bundle.sh` 실행 결과:

```
perf-allinone-bundle-<TS>.tar.gz  (혹은 디렉토리)
├── install-mac.sh / install-windows.ps1   ← ⑥
├── README.md / README-mac.md / README-windows.md   ← ⑦
├── BUNDLE_INFO.txt                        ← 빌드 메타
└── assets/
    ├── dscore-qa-perf-allinone-amd64-*.tar.gz   ← ①
    ├── dscore-qa-perf-allinone-arm64-*.tar.gz   ← ①
    ├── Ollama.dmg                               ← ②
    ├── ollama-windows-amd64.zip                 ← ③
    ├── nssm-2.24.zip                            ← ④
    ├── ollama-models-<tag>-*.tgz                ← ⑤
    └── SHA256SUMS                               ← ⑧
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

문서 상단 [§C 외부망 화이트리스트](#c-외부망-화이트리스트-온라인-빌드-머신-전용) 참조.

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

상세 사용법은 [§5.8 jmeter.ai (Feather Wand) 심화 가이드](#58-jmeterai-feather-wand-심화-가이드) 참조.
**기본 LLM: 호스트 Ollama 의 `gemma4:e2b` (호스트 GPU 가속)**.

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

### 5.8 jmeter.ai (Feather Wand) 심화 가이드

> **출처 기반 문서화** — 이 절의 모든 기능·커맨드·설정 키는 공식 저장소
> [QAInsights/jmeter-ai](https://github.com/QAInsights/jmeter-ai) v2.0.4 (MIT)
> 와 공식 제품 페이지 [featherwand.qainsights.com](https://featherwand.qainsights.com/),
> 공식 소개 글 [QAInsights 블로그](https://qainsights.com/introducing-feather-wand-your-ai-powered-companion-for-jmeter/)
> · [dev.to 게시물](https://dev.to/qainsights/introducing-feather-wand-your-ai-powered-companion-for-jmeter-2774) 의
> 내용을 한글로 정리·폐쇄망 번들 맥락에 맞게 보완한 것입니다.

#### 5.8.1 Feather Wand 는 무엇인가

**공식 정의**: *"JMeter Agent for performance engineers w/ Claude Code integration"*
— JMeter GUI 안에 내장되는 LLM 기반 성능시험 어시스턴트. **Cursor 를 JMeter 에 이식한 것**
을 지향하며, 다음을 제공합니다 (공식 README 기준).

| 핵심 기능 | 설명 |
|---|---|
| **JMeter 내장 AI 챗** | GUI 옆에 챗 패널을 띄워 자연어로 대화 |
| **요소 추천 + JMeter 베스트 프랙티스** | 선택한 요소 기준으로 컨텍스트 인지 |
| **`@커맨드` intellisense (자동완성)** | `@` 를 입력하면 사용 가능한 커맨드 목록이 뜸 |
| **JSR223 에디터 우클릭 리팩토링** | Groovy/Java 코드 자동 포맷·리팩토링 |
| **Claude Code 터미널 내장** | `claude` CLI 를 JediTerm 기반 터미널로 JMeter 내부에서 실행 |
| **모델 드롭다운** | Anthropic / OpenAI / Ollama 중 전환 |
| **프로퍼티 기반 커스터마이징** | 시스템 프롬프트·temperature 등 조정 |

**이 번들에서는 Ollama 로 고정** (폐쇄망 환경이기 때문). 그러나 **플러그인 자체는
클라우드 3종을 모두 지원** 하므로, 이 문서는 Ollama 중심으로 기술하되 필요 시
다른 공급자로 전환하는 방법도 함께 설명합니다.

#### 5.8.2 지원 LLM 공급자 (공식)

| 공급자 | 유형 | API Key 필요 | 이 번들에서 | 비고 |
|---|---|---|---|---|
| **Anthropic (Claude)** | 클라우드 | ✅ 필요 | ❌ 차단 (폐쇄망) | `claude-3-*`, `claude-sonnet-4-*` 등 채팅 모델. 토큰당 과금 |
| **OpenAI** | 클라우드 | ✅ 필요 | ❌ 차단 (폐쇄망) | 기본 `gpt-4o`. 오디오/TTS 모델은 자동 필터링됨 |
| **Ollama** | 로컬 | ❌ 불필요 | ✅ **기본** | 호스트 `localhost:11434` → 컨테이너에서 `host.docker.internal:11434` |

> 플러그인 기본 모델은 `deepseek-r1:1.5b` (thinking 지원, 매우 작음) 이지만,
> 이 번들은 `ollama.default.model=gemma4:e2b` 로 덮어써 **한국어 품질과 속도를 균형
> 있게** 맞춰 두었습니다.

#### 5.8.3 설치 상태 확인 (이 번들은 사전 설치됨)

이 컨테이너 이미지는 빌드 시 **Plugins Manager** 를 통해 Feather Wand 가 자동
설치됩니다 ([jmeter-plugin-list.txt](jmeter-plugin-list.txt) 에 `jmeter-ai` 포함).
따라서 별도 설치는 필요 없지만, 설치 상태는 다음처럼 확인 가능:

```bash
# ① JAR 존재 확인
docker exec perf-allinone ls /opt/apache-jmeter/lib/ext/ | grep -i jmeter-ai
# → jmeter-ai-2.0.4.jar (또는 feather-wand-*.jar) 가 보여야 함

# ② 설정이 user.properties 에 들어가 있는지
docker exec perf-allinone grep "^ollama\." /opt/apache-jmeter/bin/user.properties | head
# → ollama.host / ollama.default.model 등이 나와야 함

# ③ Plugins Manager 카탈로그에서 버전 확인
docker exec perf-allinone /opt/apache-jmeter/bin/PluginsManagerCMD.sh status 2>/dev/null | grep -i ai
```

수동 설치(공식 가이드)는 다음과 같습니다 — 번들 외부에서 JMeter 를 별도 사용할 때 참고:

1. **권장 경로**: JMeter GUI → `Options → Plugins Manager` → **Available Plugins** 탭
   → "feather wand" 검색 → 체크 → **Apply Changes and Restart JMeter**
2. **수동 경로**: [GitHub Releases](https://github.com/QAInsights/jmeter-ai/releases)
   에서 JAR 다운로드 → `$JMETER_HOME/lib/ext/` 에 배치 → `jmeter-ai-sample.properties`
   내용을 `jmeter.properties` 또는 `user.properties` 에 추가 → 재시작

#### 5.8.4 챗 패널 열기 — 공식 메뉴 경로

Feather Wand 챗 패널은 **JMeter 의 Non-Test Element** 로 등록됩니다
(공식 README 원문: *"Access via: Right-click menu → 'Add' → 'Non-Test Elements' → 'Feather Wand'"*).

**정확한 절차**:
1. 브라우저로 `http://localhost:18090` → noVNC 자동연결 → JMeter Swing GUI
2. 좌측 트리의 **Test Plan** 루트 노드 우클릭
3. **Add → Non-Test Elements → Feather Wand** 선택
4. Test Plan 아래에 **Feather Wand** 노드가 생기고, 우측 패널에 챗 UI 가 뜸

챗 패널의 주요 구성요소 (공식 문서 기준):

| 요소 | 위치 | 동작 |
|---|---|---|
| **메시지 입력창** | 패널 하단 | 자연어 또는 `@커맨드` 입력 |
| **Model Dropdown** | 패널 상단 | 구성된 공급자 중 즉시 전환 |
| **Undo / Redo 버튼** | 패널 상단 | `@lint`, `@wrap` 같은 **트리 변경 커맨드 실행 이후** 활성화 — 원복/재실행 |
| **대화 스크롤 영역** | 패널 중앙 | 최근 `ollama.max.history.size` 턴 표시 |

> 💡 **한 번 추가하면 저장됨** — Feather Wand 노드가 있는 .jmx 를 저장해 두면
> 다음에 다시 열 때 트리에 그대로 남습니다. 매번 Add 할 필요 없음.

#### 5.8.5 최초 사용 — 3단계 스모크 테스트 (1분)

폐쇄망 반입 후 Feather Wand 가 **호스트 Ollama 와 HTTP 통신이 되는지** 1분 안에
확인하는 절차.

**① 호스트에서 Ollama 가 살아 있는가?** (호스트 터미널)
```bash
curl -s http://localhost:11434/api/tags | head
# → {"models":[{"name":"gemma4:e2b","modified_at":...}]}  이면 OK
```

**② 컨테이너가 호스트 Ollama 를 볼 수 있는가?**
```bash
docker exec perf-allinone curl -s http://host.docker.internal:11434/api/tags | head
# → ①과 동일한 JSON 이 나와야 함
# 실패 시: §5.8.12 트러블슈팅의 "connection refused" 항목
```

**③ Feather Wand 챗에서 핑 쏘기**
1. 브라우저 → `http://localhost:18090` → JMeter GUI
2. Test Plan 우클릭 → **Add → Non-Test Elements → Feather Wand**
3. Model Dropdown 에서 `gemma4:e2b` (또는 현재 `ollama.default.model`) 선택 확인
4. 입력창에 `Hello, what model are you?` 입력 → Enter
5. 2-5초 후 모델 이름을 언급하는 응답이 오면 **정상**

> ③에서 30초 넘게 응답이 없으면 §5.8.12 트러블슈팅의 "챗 입력해도 응답 없음" 참조.

#### 5.8.6 @커맨드 완전 레퍼런스 (공식 6종)

공식 README 기준, Feather Wand 는 **6개의 `@커맨드`** 를 제공합니다.
입력창에 `@` 를 입력하면 자동완성 드롭다운이 뜹니다.

##### (1) `@this` — 선택 요소에 대한 컨텍스트 질의

**공식 설명**: *"Highlight an element in your test plan and type `@this` to get
detailed, context-specific insights about it."*

**사용법**:
1. 트리에서 질의 대상 요소 선택 (예: `bzm - Concurrency Thread Group`)
2. 챗 입력창에:
   ```
   What does @this do?
   ```
   또는
   ```
   Best practices for @this?
   ```
3. 응답에는 요소의 역할·주요 파라미터·주의점이 포함됨

**실전 예시 프롬프트**:
- `Explain @this in Korean` — 한국어로 설명 요청
- `What are failure modes for @this under high load?` — 부하 시 장애 시나리오
- `Suggest 3 assertions I should add alongside @this` — 보완 어설션 제안
- `Why would @this cause memory pressure?` — 메모리 이슈 원인 분석

##### (2) `@optimize` — 선택 요소 최적화 제안

**공식 설명**: *"Get optimization recommendations for selected element."*

**사용 장면**: 테스트가 불안정하거나, 측정 정확도에 의문이 들 때.

**실전 예시**:
```
@optimize
This thread group runs for 30 minutes and we see memory pressure around the 15-minute mark.
Listeners currently attached: View Results Tree, Summary Report, Aggregate Report.
```
→ 응답에는 리스너 제거/교체 권고, `Simple Data Writer` 로 대체, `-JforceHeapDump=true`
  같은 진단 플래그 등이 포함됨.

##### (3) `@lint` — 요소 이름 자동 정리

**공식 설명**: *"Automatically rename elements for consistency (e.g.,
`@lint rename based on URL`)."*

**사용 장면**: 녹화(record)나 임포트로 `HTTP Request 1`, `HTTP Request 2` … 같이
의미 없는 이름만 가득한 시나리오를 빠르게 정리할 때.

**실전 예시**:
```
@lint rename based on URL
```
→ `HTTP Request 1` 이 `GET /api/v1/users` 로, `HTTP Request 2` 가
  `POST /api/v1/login` 등 URL 기반 이름으로 바뀜.

**주의**: 트리 구조를 변경하는 **파괴적 커맨드** 이므로 실행 직후 챗 패널 상단의
**Undo** 버튼으로 원복 가능. 실행 전에 **File → Save** 로 .jmx 백업 권장.

##### (4) `@wrap` — Transaction Controller 자동 그룹핑

**공식 설명**: *"Group HTTP samplers under Transaction Controllers intelligently.
This feature is especially useful for imported or recorded test plans that contain
many individual HTTP samplers without proper organization."*

**사용 장면**: 20-50개 HTTP 샘플러가 평평하게 나열된 녹화 시나리오를 **로그인 →
검색 → 결제** 같은 논리 단위로 묶어 Aggregate Report 를 보기 쉽게 만들 때.

**실전 예시**:
```
@wrap
Group these samplers into logical transactions based on the user journey:
login flow, product search, add-to-cart, and checkout.
```
→ 해당 샘플러 묶음 위에 `Transaction Controller - LoginFlow` 등이 자동 삽입됨.
  이후 HTML Dashboard 에서 Transaction 단위의 95%ile 을 볼 수 있음.

**주의**: `@lint` 와 마찬가지로 파괴적 — Undo 버튼으로 원복 가능.

##### (5) `@code` — 응답 코드 블록을 JSR223 에 자동 삽입

**공식 설명**: *"Extract code blocks from AI responses into JSR223 editor."*

**사용 장면**: AI 가 Groovy/Java 스니펫을 제안했을 때, 복사·붙여넣기 없이
JSR223 PreProcessor / Sampler / PostProcessor 에 바로 꽂아 넣고 싶을 때.

**워크플로우**:
1. 먼저 트리에 `JSR223 PreProcessor` (또는 Sampler) 추가
2. 챗에서 생성 요청:
   ```
   Generate a Groovy snippet that creates an 8-digit random user id and stores
   it in a JMeter variable called USER_ID.
   ```
3. 응답에 Groovy 코드 블록이 포함되면, 해당 JSR223 요소 선택 후:
   ```
   @code
   ```
4. 방금 생성된 코드가 JSR223 Script 칸에 자동 주입됨

**공식 예시 스니펫** (dev.to 게시물):
```groovy
vars.put("userId", String.valueOf((int)(Math.random() * 100000000)))
```

##### (6) `@usage` — 토큰 사용량·대화 기록 확인

**공식 설명**: *"View token usage statistics and conversation history."*

**사용 장면**: Anthropic/OpenAI 과금 모니터링, 긴 세션의 히스토리 리뷰.
Ollama 환경(이 번들)에서는 **토큰 수는 표시되지만 비용은 0** — 주로 히스토리 확인용.

**예시**:
```
@usage
```
→ 챗 영역에 이번 세션의 메시지 수·입력/출력 토큰·공급자별 집계가 표시됨.

#### 5.8.7 실전 워크플로우 5선

공식 예시 프롬프트 + 이 번들 환경을 기준으로 한 실전 사용 패턴.

##### 워크플로우 A. 빈 Test Plan 에서 시나리오 뼈대 생성

**목표**: e-커머스 체크아웃 흐름을 1분 안에 세우기.

1. 새 Test Plan → Feather Wand 추가 → 챗에:
   ```
   How should I structure a test plan for an e-commerce checkout flow?
   Target: 100 concurrent users, 10-minute steady load. Endpoints:
   POST /login, GET /products, POST /cart, POST /checkout.
   Include assertions for 200 responses and p95 under 800ms.
   ```
   (공식 예시 프롬프트 *"How should I structure a test plan for an e-commerce checkout flow?"* 를 구체화한 것)
2. 응답에 구조(Thread Group → Transaction Controller → HTTP Request × 4 → Assertion)가
   자연어로 제시됨 → 해당 요소들을 수동으로 추가하거나, 세부 요청을 이어서:
   ```
   Add an HTTP Request Sampler for a login endpoint at POST /login
   with body {"user":"${USER}","pass":"${PASS}"}.
   ```
3. 마지막으로 `File → Save` → `/data/jmeter/scenarios/checkout-smoke.jmx`

##### 워크플로우 B. 상속받은 시나리오 정리 (`@lint` + `@wrap`)

**상황**: 이전 담당자가 남긴 .jmx 에 40개 HTTP Request 가 평평하게 있고 이름이
`HTTP Request 1..40`.

1. .jmx 를 JMeter 로 열기
2. 챗에 `@lint rename based on URL` → 40개 이름이 URL 기반으로 재명명 →
   오류 없으면 Undo 건너뛰고 저장
3. 이어서:
   ```
   @wrap group these into transactions by functional area: auth, catalog, order.
   ```
4. Transaction Controller 3개가 생기면 저장

##### 워크플로우 C. 측정 정확도 올리기 (`@this` + `@optimize`)

1. Thread Group 선택 → 챗에:
   ```
   @this
   How can I reduce the measurement noise introduced by GUI listeners?
   ```
   → View Results Tree 같은 GUI 리스너를 CLI 에서 제거/치환하라는 답변
2. Test Plan 선택 → 챗에:
   ```
   @optimize for a non-GUI CLI run. Current RAM is 16GB and we're seeing GC pauses.
   ```
   → Heap 조정(`-Xmx`), 리스너 교체, `jmeter.save.saveservice.*` 튜닝 제안

##### 워크플로우 D. 응답 상관관계 처리 (`@code`)

1. 첫 요청의 응답에서 토큰을 뽑아야 하는 상황
2. HTTP Request 아래에 `JSR223 PostProcessor` 추가
3. 챗에:
   ```
   Generate a Groovy snippet that extracts $.data.access_token from the previous
   response and stores it as JMeter variable TOKEN. Handle the case where the
   field is missing.
   ```
4. JSR223 PostProcessor 선택 상태에서 `@code` → Script 칸에 자동 주입

##### 워크플로우 E. 디버깅 대화 (다중 턴)

Feather Wand 는 대화 히스토리를 **`ollama.max.history.size`(기본 10턴)** 까지 기억
하므로, 한 세션 안에서 "방금 것 고쳐줘" 같은 후속 지시가 통합니다.

```
(1턴) Why would @this Concurrency Thread Group report unstable TPS?
(2턴) What about ramping — is 30s ramp too aggressive for 100 users?
(3턴) Apply that fix to the element.
```

새 주제로 넘어갈 때는 Feather Wand 노드를 **Remove → Add** 하거나, 신규 세션을 위해
다른 .jmx 로 전환하세요 (플러그인 자체에 "Clear conversation" 버튼은 공식 문서에
명시되어 있지 않음 — 모델 전환이나 재시작으로 리셋).

#### 5.8.8 JSR223 에디터 우클릭 리팩토링

공식 README 기준, **JSR223 에디터에서 우클릭** 하면 리팩토링/포맷팅/함수 삽입
메뉴가 나타납니다 (`jmeter.ai.refactoring.enabled=true` 일 때).

| 항목 | 설명 |
|---|---|
| **Code Refactoring** | 선택한 코드 블록을 깔끔하게 재작성 |
| **Formatting** | 들여쓰기·줄바꿈 정리 |
| **Function Insertion** | JMeter 내장 함수(`${__UUID()}` 등) 자동 삽입 |

이 기능은 내부적으로 **다른 공급자를 쓸 수 있음** — 설정 키
`jmeter.ai.service.type` 이 `openai` 또는 `anthropic` 일 때만 활성화됩니다.
**이 폐쇄망 번들에서는 기본 비활성** 상태이며, 사용하려면 사내에 OpenAI 호환
엔드포인트가 있어야 합니다 (§5.8.10 참조).

#### 5.8.9 Claude Code 터미널 내장

공식 README 원문: *"Feather Wand features a fully embedded interactive Claude Code
Terminal using JediTerm."*

JMeter 내부에서 `claude` CLI 를 직접 호출해 멀티스텝 에이전트 워크플로우를 돌릴 수
있는 기능. 요구사항:

```bash
# 호스트에 Claude Code CLI 가 설치되어 있어야 함 (Node.js 18+ 필요)
npm install -g @anthropic-ai/claude-code
```

설정 키 (v2.0.4 기준):

| 키 | 기본값 | 설명 |
|---|---|---|
| `jmeter.ai.terminal.claudecode.enabled` | `true` | 터미널 패널 활성화 |
| `jmeter.ai.terminal.claudecode.path` | (자동 탐지) | `claude` 실행 파일 경로 |
| `jmeter.ai.terminal.claudecode.prompt` | (기본 프롬프트) | 터미널에서 사용할 시스템 프롬프트 |

**⚠️ 이 폐쇄망 번들에서는 비활성** — 이유:
- Claude Code CLI 는 `api.anthropic.com` outbound 가 필요 (폐쇄망에서 차단됨)
- 컨테이너에 Node.js / `claude` CLI 가 포함되어 있지 않음

사용하려면 컨테이너 밖(호스트 또는 사내 다른 머신)에서 별도 설치하고, JMeter 는
챗 기능만 활용하는 구성이 현실적입니다.

#### 5.8.10 모델/공급자 전환

**방법 A. Model Dropdown (세션 범위)**: 챗 패널 상단 드롭다운에서 설정된 공급자·모델
중 하나 선택 → 즉시 전환. JMeter 재시작 불필요.

**방법 B. `user.properties` 수정 (영구)**:

```bash
# Ollama 모델만 바꾸기
docker exec perf-allinone sed -i \
  's|^ollama.default.model=.*|ollama.default.model=qwen2.5:7b|' \
  /opt/apache-jmeter/bin/user.properties
docker exec perf-allinone supervisorctl restart jmeter-gui
```

**방법 C. 공급자 자체를 OpenAI/Anthropic 으로 전환** (폐쇄망에서는 사내 프록시 필요):

```bash
docker exec -it perf-allinone bash -c "cat >> /opt/apache-jmeter/bin/user.properties <<EOF
# 예: 사내 OpenAI 호환 프록시
openai.api.key=sk-internal-...
openai.default.model=gpt-4o
jmeter.ai.service.type=openai
EOF"
docker exec perf-allinone supervisorctl restart jmeter-gui
```

#### 5.8.11 설정 키 전체 레퍼런스 (v2.0.4 공식)

공식 [`jmeter-ai-sample.properties`](https://github.com/QAInsights/jmeter-ai/blob/main/jmeter-ai-sample.properties) 기준 전체 키.

##### Anthropic (Claude)

| 키 | 기본값 | 설명 |
|---|---|---|
| `anthropic.api.key` | (비어 있음) | **필수** — Claude API 키 |
| `claude.default.model` | `claude-3-sonnet-20240229` | 기본 모델 |
| `claude.temperature` | `0.7` | 창의성 (0.0–1.0) |
| `claude.max.tokens` | `1024` | 응답 최대 토큰 |
| `claude.max.history.size` | `10` | 대화 히스토리 턴 수 |
| `claude.system.prompt` | (기본 어시스턴트 프롬프트) | 시스템 프롬프트 |
| `anthropic.log.level` | `info` | 로깅 레벨 (`info` / `debug`) |

##### OpenAI

| 키 | 기본값 | 설명 |
|---|---|---|
| `openai.api.key` | (비어 있음) | **필수** — OpenAI API 키 |
| `openai.default.model` | `gpt-4o` | 기본 모델 (TTS/오디오 모델은 자동 필터) |
| `openai.temperature` | `0.5` | 창의성 |
| `openai.max.tokens` | `1024` | 응답 최대 토큰 |
| `openai.max.history.size` | `10` | 대화 히스토리 |
| `openai.system.prompt` | (기본 프롬프트) | 시스템 프롬프트 |
| `openai.log.level` | `INFO` | 로깅 (`INFO` / `DEBUG`) |

##### Ollama (이 번들의 기본 공급자)

| 키 | 플러그인 기본 | **이 번들 값** | 설명 |
|---|---|---|---|
| `ollama.host` | `http://localhost` | `http://host.docker.internal` | Ollama 호스트 (컨테이너 → 호스트 매핑) |
| `ollama.port` | `11434` | `11434` | Ollama 포트 |
| `ollama.default.model` | `deepseek-r1:1.5b` | `gemma4:e2b` | 기본 모델 (한국어 품질 고려) |
| `ollama.temperature` | `0.5` | `0.5` | 창의성 |
| `ollama.max.history.size` | `10` | `10` | 대화 히스토리 턴 수 |
| `ollama.thinking.mode` | `DISABLED` | `DISABLED` | thinking 모드 (`ENABLED` / `DISABLED`) |
| `ollama.thinking.level` | `MEDIUM` | `MEDIUM` | thinking 강도 (`LOW` / `MEDIUM` / `HIGH`) |
| `ollama.request.timeout.seconds` | `120` | `300` | 요청 타임아웃(초) — 폐쇄망 큰 모델 고려해 늘림 |
| `ollama.system.prompt` | (기본 프롬프트) | (한국어 프롬프트) | 시스템 프롬프트 |

> 공식 문서는 *"increase to 300+ for thinking models"* 라고 권고 — 이 번들은 선제적으로 300 설정.

##### JSR223 리팩토링

| 키 | 기본값 | 설명 |
|---|---|---|
| `jmeter.ai.refactoring.enabled` | `true` | 우클릭 리팩토링 메뉴 활성화 |
| `jmeter.ai.service.type` | (미설정) | `openai` 또는 `anthropic` — 리팩토링에 쓸 공급자 |

##### Claude Code 터미널

| 키 | 기본값 | 설명 |
|---|---|---|
| `jmeter.ai.terminal.claudecode.enabled` | `true` | 터미널 패널 활성화 |
| `jmeter.ai.terminal.claudecode.path` | (자동 탐지) | `claude` 바이너리 경로 |
| `jmeter.ai.terminal.claudecode.prompt` | (기본 프롬프트) | 시스템 프롬프트 |

#### 5.8.12 설정 변경 절차

properties 는 **JMeter 기동 시 한 번만 읽히므로** 변경 후 반드시 GUI 재시작 필요.

```bash
# 1) 편집 (컨테이너 내부 vi 또는 호스트에서 docker cp 로 왕복)
docker exec -it perf-allinone vi /opt/apache-jmeter/bin/user.properties

# 2) JMeter GUI 만 재시작 (다른 프로세스 영향 없음)
docker exec perf-allinone supervisorctl restart jmeter-gui

# 3) 브라우저에서 noVNC 세션 새로고침 (Ctrl+R / Cmd+R)
```

**자주 쓰는 일괄 변경 예시**:

```bash
# 더 큰 모델 + 긴 타임아웃 + thinking 모드
docker exec perf-allinone sh -c "
  sed -i 's|^ollama.default.model=.*|ollama.default.model=deepseek-r1:7b|' /opt/apache-jmeter/bin/user.properties
  sed -i 's|^ollama.request.timeout.seconds=.*|ollama.request.timeout.seconds=600|' /opt/apache-jmeter/bin/user.properties
  sed -i 's|^ollama.thinking.mode=.*|ollama.thinking.mode=ENABLED|' /opt/apache-jmeter/bin/user.properties
"
docker exec perf-allinone supervisorctl restart jmeter-gui
```

#### 5.8.13 트러블슈팅

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| 메뉴에 **Feather Wand** 항목이 없음 | `jmeter-ai` JAR 이 `lib/ext/` 에 없음 | `docker exec perf-allinone ls /opt/apache-jmeter/lib/ext/ \| grep -i ai` 로 확인. 없으면 이미지 재빌드 또는 Plugins Manager 재설치 |
| 챗 입력해도 응답 없음 (무한 로딩) | 호스트 Ollama 미기동 | §5.8.5 ①② 스모크 테스트로 양방향 확인 |
| `Connection refused` | `host.docker.internal` 미해석 | Docker Desktop 사용(Mac/Win) 또는 `--add-host=host.docker.internal:host-gateway` 로 기동 |
| `model "xxx" not found` | 호스트 Ollama 에 해당 모델 없음 | 호스트에서 `ollama pull xxx` 또는 `ollama.default.model` 을 설치된 모델로 변경 |
| 응답이 중간에 잘림 | `ollama.request.timeout.seconds` 초과 | 300 → 600 으로 증가 후 재시작 |
| 응답이 영어/중국어로만 옴 | 모델 한국어 능력 한계 | `qwen2.5:7b` 이상으로 교체, 또는 `ollama.system.prompt` 에 "반드시 한국어로 응답" 명시 |
| thinking 모드에서 응답이 수십 초 | HIGH thinking 은 느림 | `ollama.thinking.level=LOW` 또는 `DISABLED` 로 되돌림 |
| `@lint` / `@wrap` 실행 결과가 마음에 안 듦 | 공식 동작 — 파괴적 커맨드 | 챗 패널 상단 **Undo** 버튼으로 즉시 원복 |
| `@code` 가 JSR223 에 안 꽂힘 | 트리에서 JSR223 요소를 선택하지 않음 | JSR223 PreProcessor/Sampler/PostProcessor 선택 후 `@code` 재실행 |
| Model Dropdown 에 Anthropic/OpenAI 가 안 보임 | API 키 미설정 | 폐쇄망에서는 정상 (의도된 동작). 클라우드 사용 필요 시 §5.8.10 방법 C |
| Claude Code 터미널 탭이 작동 안 함 | `claude` CLI 미설치 또는 outbound 차단 | 폐쇄망에서는 §5.8.9 설명대로 비활성 — 정상 |

#### 5.8.14 보안·프라이버시 체크리스트 (폐쇄망)

공식 README 경고: *"Do not share credentials or proprietary code in chat."*

- [ ] **API 키 평문 노출** — `anthropic.api.key` / `openai.api.key` 는
      `user.properties` 에 평문 저장됨. 이 번들은 Ollama 만 쓰므로 비어 있어야 정상
- [ ] **`ollama.system.prompt` 검토** — 시스템 프롬프트에 고객명·사내 URL 등
      포함하지 않음
- [ ] **챗에서 실제 토큰/비밀번호 붙여넣기 금지** — 더미 값으로 치환
- [ ] **`ollama.com` outbound 차단 유지** — Ollama 자동 업데이트·원격 모델 메타
      조회가 외부로 나가지 않도록 방화벽 확인
- [ ] **.jmx 파일에 Feather Wand 노드가 포함되어 유출 시** — 노드 자체는 코드가
      없지만, 챗 히스토리가 별도 저장되지는 않음 (세션 휘발). JMeter `jmeter.log`
      는 남을 수 있으니 공유 전 `grep -i "feather\|ollama" jmeter.log` 로 확인
- [ ] **공식 권고**: *"Always backup test plans before implementing AI suggestions"*
      — `@lint` / `@wrap` / `@code` 적용 전 `File → Save` 한 번

#### 5.8.15 더 알아보기 — 공식 자료

| 자료 | URL | 용도 |
|---|---|---|
| **공식 저장소 (소스·Issue·Release)** | https://github.com/QAInsights/jmeter-ai | 버전 확인·버그 신고 |
| **공식 제품 페이지** | https://featherwand.qainsights.com/ | 기능 소개 |
| **공식 소개 글 (QAInsights 블로그)** | https://qainsights.com/introducing-feather-wand-your-ai-powered-companion-for-jmeter/ | 설계 배경·설치 안내 |
| **dev.to 튜토리얼** | https://dev.to/qainsights/introducing-feather-wand-your-ai-powered-companion-for-jmeter-2774 | 예제 프롬프트 모음 |
| **샘플 properties 원본** | https://github.com/QAInsights/jmeter-ai/blob/main/jmeter-ai-sample.properties | 전체 설정 키 최신 레퍼런스 |
| **관련 프로젝트 (feather_wand_agent)** | https://github.com/QAInsights/feather_wand_agent | JMeter/k6/Gatling/Locust 통합 에이전트 (별도 프로젝트) |

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
