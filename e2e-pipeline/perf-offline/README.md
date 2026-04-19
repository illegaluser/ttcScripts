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

Feather Wand 는 JMeter GUI 안에 내장되는 **LLM 기반 시나리오 어시스턴트** 입니다.
자연어로 시나리오를 생성하고, 선택한 요소를 설명받고, 실패 원인을 분석할 수 있습니다.
모든 요청은 **컨테이너 → host.docker.internal:11434 → 호스트 Ollama** 로 전달되며,
외부로 나가지 않으므로 **폐쇄망에서 그대로 사용 가능** 합니다.

#### 5.8.1 화면 찾기 — Feather Wand UI 위치

JMeter 설치 후 처음 기동하면 다음 두 곳에서 Feather Wand 에 접근할 수 있습니다.

| 위치 | 어떻게 열까 | 언제 쓰나 |
|---|---|---|
| **상단 툴바의 🪶 깃펜 아이콘** | JMeter 창 오른쪽 위, 도움말 아이콘 근처 클릭 | 챗 패널 토글 (채팅 시작) |
| **트리 요소 우클릭 → `@AI`** | Test Plan / Thread Group / HTTP Request 등 어떤 요소든 우클릭 | 선택 요소에 대한 설명·최적화·어설션 추천 |

> 아이콘이 보이지 않으면 JMeter 가 플러그인을 인식하지 못한 것입니다.
> `docker exec perf-allinone ls /opt/apache-jmeter/lib/ext/ | grep -i feather` 로
> `feather-wand-*.jar` 존재를 확인하세요.

#### 5.8.2 최초 사용 — 3단계 스모크 테스트

폐쇄망 반입 후 Feather Wand 가 제대로 LLM과 통신하는지 1분 만에 확인하는 절차.

**① 호스트 Ollama 살아있는지 확인** (호스트 터미널에서)
```bash
curl -s http://localhost:11434/api/tags | head
# → {"models":[{"name":"gemma4:e2b",...}]}  이면 OK
```

**② 컨테이너에서 호스트 Ollama 가 보이는지 확인**
```bash
docker exec perf-allinone curl -s http://host.docker.internal:11434/api/tags | head
# → 위와 동일한 JSON 이 나와야 함
```

**③ JMeter GUI 에서 챗 한 번 쏘기**
1. 브라우저 → `http://localhost:18090`
2. 상단 🪶 아이콘 클릭 → 챗 패널 열림
3. 입력창에 `안녕, 모델 이름이 뭐야?` 입력 → Enter
4. 2-5초 후 모델이 한국어로 응답하면 **정상**

> ③에서 응답이 30초 넘게 안 오면 §5.8.7 트러블슈팅 참조.

#### 5.8.3 5가지 핵심 사용 시나리오

실제 업무에서 가장 자주 쓰는 다섯 가지 패턴.

##### (A) 자연어로 시나리오 뼈대 생성

**상황**: 빈 Test Plan에서 시작, 대략적인 부하 시험을 빠르게 세우고 싶다.

**방법**:
1. Test Plan 우클릭 → `@AI` → 챗 패널 열림
2. 다음처럼 **대상/부하/시간**을 포함해 작성:
   ```
   https://api.example.com/v1/users 에 대해
   50명 동시 사용자로 5분간 GET 부하 시험.
   응답시간 95%ile 500ms 어설션 포함, TPS 그래프 리스너 붙여줘.
   ```
3. 응답에 나타난 **"Apply"** 또는 **"Insert"** 버튼 클릭 → 트리에 요소 자동 삽입
4. `Ctrl+S` 로 `/data/jmeter/scenarios/api-users-smoke.jmx` 저장

**팁**: 프롬프트에 들어가면 품질이 올라가는 정보 — 대상 URL, 메서드, 인증 방식,
동시 사용자 수, 총 실행 시간, ramp-up 패턴, 어설션 기준(코드/응답시간/본문).

##### (B) 선택한 요소 설명받기 (`Explain`)

**상황**: 기존 시나리오를 물려받았는데 어떤 요소가 왜 있는지 모르겠다.

**방법**:
- 트리에서 이해가 안 되는 요소(예: `Constant Throughput Timer`) 우클릭 → `@AI` → `Explain this element`
- 챗 패널에 **요소의 역할 / 주요 파라미터 / 주의점** 이 한국어로 설명됨

##### (C) 시나리오 최적화 제안 (`Optimize`)

**상황**: 테스트를 돌려봤는데 Heap OOM 이 나거나, 결과가 불안정하다.

**방법**:
- Thread Group 또는 Test Plan 우클릭 → `@AI` → `Optimize this`
- 프롬프트에 현상을 덧붙이면 질 좋은 답을 얻음:
  ```
  이 시나리오를 실행하면 10분쯤에 Heap OutOfMemoryError 가 난다.
  리스너 구성·샘플러 설정을 어떻게 조정해야 할까?
  ```

##### (D) 실패 원인 분석 (`Debug`)

**상황**: CLI 결과를 돌려봤는데 에러율이 이상하다.

**방법**: 최근 JTL 의 요약을 복사해 챗에 붙여 분석 요청.
```
아래 JTL 요약에서 401 이 전체의 40% 발생했다. 가장 가능성 높은 원인 3가지와
시나리오에서 확인해야 할 체크포인트를 알려줘.

samples=12500, errors=5004, mean=320ms, 90%=620ms, 99%=1.8s
error_codes: 401=5000, 500=4
```

##### (E) 어설션/코릴레이션 추천

**상황**: 응답 JSON 에서 토큰을 뽑아 다음 요청에 써야 한다.

**방법**: HTTP Request 우클릭 → `@AI` → 챗에 이어서:
```
이 응답 JSON 에서 $.data.access_token 값을 추출해 TOKEN 변수에 저장하고
다음 HTTP Request 의 Authorization 헤더에 Bearer 로 붙이는 설정을 만들어줘.
```
→ JSON Extractor + Header Manager 설정이 자동 제안됨.

#### 5.8.4 프롬프트 작성 팁 (답변 품질 올리는 법)

| 나쁜 예 | 좋은 예 | 왜 |
|---|---|---|
| "부하 시험 만들어줘" | "GET https://api.example.com/users, 동시 50, 5분, p95 500ms 어설션" | 대상·부하·SLO 가 명시됨 |
| "왜 에러나?" | "401이 40%, 나머지는 200. 헤더에 `Authorization: Bearer ${TOKEN}` 사용 중" | 현상·관련 설정이 같이 있음 |
| "최적화해줘" | "10분에 Heap OOM. 리스너는 View Results Tree + Summary Report 사용 중" | 증상·의심 영역이 명시됨 |

**한 가지 더**: Feather Wand 는 **대화 히스토리를 `ollama.max.history.size` (기본 10턴) 까지 기억** 합니다.
한 세션 안에서 "방금 만든 시나리오에 어설션 추가해줘" 같은 후속 지시가 통합니다.
새 시나리오로 넘어갈 때는 챗 패널의 **Clear** 버튼으로 맥락을 비우세요.

#### 5.8.5 설정 키 레퍼런스

`/opt/apache-jmeter/bin/user.properties` 에 사전 등록된 키 (모두 `ollama.*` 네임스페이스):

| 키 | 기본값 | 설명 | 언제 바꾸나 |
|---|---|---|---|
| `ollama.host` | `http://host.docker.internal` | 호스트 Ollama 주소 | 거의 바꿀 일 없음 (Docker Desktop 자동 매핑) |
| `ollama.port` | `11434` | Ollama 포트 | Ollama 를 다른 포트로 띄운 경우 |
| `ollama.default.model` | `gemma4:e2b` | 기본 모델 태그 | 모델 교체 시 (§2.4 참조) |
| `ollama.temperature` | `0.5` | 창의성 (0.0–1.0) | 정확성 우선 `0.2`, 다양한 제안 원할 때 `0.8` |
| `ollama.max.history.size` | `10` | 대화 히스토리 턴 수 | 긴 대화 문맥이 필요하면 늘림 (RAM 더 씀) |
| `ollama.thinking.mode` | `DISABLED` | `ENABLED` \| `DISABLED` | `deepseek-r1:*` 등 thinking 지원 모델 쓸 때만 `ENABLED` |
| `ollama.thinking.level` | `MEDIUM` | `LOW` \| `MEDIUM` \| `HIGH` | thinking 모드에서만 유효, HIGH 는 응답 시간 크게 늘어남 |
| `ollama.request.timeout.seconds` | `300` | 요청 타임아웃(초) | 큰 모델(14B+) + HIGH thinking 사용 시 `600` 권장 |
| `ollama.system.prompt` | (한국어 어시스턴트 프롬프트) | 시스템 프롬프트 | 팀 표준 가이드라인(명명 규칙, 필수 리스너 등) 주입 시 |

> 출처: [jmeter-ai-sample.properties](https://github.com/QAInsights/jmeter-ai/blob/main/jmeter-ai-sample.properties)

#### 5.8.6 설정 변경 절차

**일시적 확인 (재시작 없음 불가)** — jmeter.ai 는 기동 시 properties 를 읽으므로 반드시 재시작 필요.

```bash
# 1) 편집
docker exec -it perf-allinone vi /opt/apache-jmeter/bin/user.properties

# 2) JMeter GUI 재시작 (컨테이너 자체는 재시작 불필요)
docker exec perf-allinone supervisorctl restart jmeter-gui

# 3) 브라우저에서 noVNC 세션 새로고침 → 새 설정으로 기동된 JMeter 노출
```

**자주 쓰는 일괄 변경 예시**:
```bash
# 모델을 qwen2.5:7b 로, 타임아웃을 600초로
docker exec perf-allinone sh -c "
  sed -i 's|^ollama.default.model=.*|ollama.default.model=qwen2.5:7b|' /opt/apache-jmeter/bin/user.properties
  sed -i 's|^ollama.request.timeout.seconds=.*|ollama.request.timeout.seconds=600|' /opt/apache-jmeter/bin/user.properties
"
docker exec perf-allinone supervisorctl restart jmeter-gui
```

#### 5.8.7 트러블슈팅

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| 🪶 아이콘이 안 보임 | feather-wand JAR 이 `lib/ext/` 에 없음 | `docker exec perf-allinone ls /opt/apache-jmeter/lib/ext/ \| grep feather` 로 확인 → 없으면 이미지 재빌드 |
| 챗 입력해도 응답 없음 (무한 로딩) | 호스트 Ollama 미기동 / 포트 충돌 | §5.8.2 ① ② 스모크 테스트로 양방향 연결 확인 |
| `connection refused` 에러 | `host.docker.internal` 미해석 (Linux 호스트) | Docker Desktop 사용 (Mac/Win) 또는 `--add-host=host.docker.internal:host-gateway` 로 기동 |
| `model "xxx" not found` | 호스트 Ollama 에 해당 모델이 없음 | 호스트에서 `ollama pull xxx` 또는 `ollama.default.model` 을 설치된 모델로 변경 |
| 응답이 중간에 잘림 | `ollama.request.timeout.seconds` 초과 | 300 → 600 으로 증가 후 재시작 |
| 응답이 엉뚱한 언어 (영어/중국어 등) | 모델의 한국어 품질 한계 | `qwen2.5:7b` 이상 권장, 또는 `ollama.system.prompt` 에 "반드시 한국어로 응답" 추가 |
| thinking 모드에서 응답이 길어지기만 함 | 모델이 thinking 미지원 | `ollama.thinking.mode=DISABLED` 로 되돌림 (7B 미만 일반 모델은 대부분 미지원) |

#### 5.8.8 프라이버시 체크리스트 (폐쇄망 배포 시)

- [ ] `ollama.system.prompt` 에 **사내 민감 정보·고객 데이터 금지** (평문으로 user.properties 에 남음)
- [ ] 챗에서 **실제 프로덕션 인증 토큰 붙여 넣기 금지** — 더미 값으로 대체
- [ ] `ollama.com` outbound 차단 확인 — Ollama 가 자동 업데이트 시 모델 메타가 외부로 노출될 수 있음
- [ ] 대화 로그는 `/data/logs/` 에 저장되지 않지만, JMeter `jmeter.log` 에 에러가 남을 수 있음 — 공유 전 확인

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
