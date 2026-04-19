# 🍎 Mac 사용자용 — JMeter Performance All-in-One 설치 및 운영

> Apple Silicon (M1/M2/M3/M4/M5) 와 Intel Mac 모두 지원.
> **호스트 Ollama 의 GPU 가속**을 활용해 jmeter.ai (Feather Wand) 추론 속도가 빠릅니다.

## ⚡ TL;DR — 자동 설치

번들 USB 를 받았다면 (전형 케이스):

```bash
cd /Volumes/USB/perf-allinone-bundle-*
bash install-mac.sh
```

스크립트가 자동으로:
1. Docker Desktop 가동 확인 (없으면 자동 실행 시도)
2. assets/ 무결성 검증
3. **Ollama.app 설치** (Applications 폴더로 복사)
4. **OLLAMA 환경변수 설정** (launchctl + ~/.zshrc 영구 등록)
5. **Ollama 기동 + 모델 archive 복원**
6. **JMeter 컨테이너 로드 + 기동**
7. **호스트 ↔ 컨테이너 도달 검증 + 브라우저 자동 열기**

총 소요: 1-3분 (모델 archive 복원 시간 의존).

**관리 명령**:
```bash
bash install-mac.sh --check       # 현재 상태 점검
bash install-mac.sh --uninstall   # 완전 제거 (모델/볼륨 삭제 여부 대화형 확인)
```

> 사전 요구: **Docker Desktop** 만 미리 설치해 두세요. 그 외 모든 것(Ollama,
> 환경변수, 모델, 컨테이너)은 스크립트가 처리합니다.
> Docker Desktop 미설치 시 [§2 Docker Desktop 설치](#2-docker-desktop-설치) 참조.

자동 스크립트가 실패하거나 수동으로 단계별 진행하고 싶다면 아래 §1 부터 따라가세요.

---

## 목차 (수동 설치 / 트러블슈팅)

0. [내 Mac 확인](#0-내-mac-확인)
1. [Ollama 호스트 설치 (필수)](#1-ollama-호스트-설치-필수)
2. [Docker Desktop 설치](#2-docker-desktop-설치)
3. [JMeter 컨테이너 이미지 받기 / 빌드](#3-jmeter-컨테이너-이미지-받기--빌드)
4. [컨테이너 기동](#4-컨테이너-기동)
5. [브라우저로 접속](#5-브라우저로-접속)
6. [첫 시나리오 실행](#6-첫-시나리오-실행)
7. [정식 부하시험 (CLI)](#7-정식-부하시험-cli)
8. [편의: 도우미 셸 함수](#8-편의-도우미-셸-함수)
9. [트러블슈팅](#9-트러블슈팅)
10. [정리 (제거)](#10-정리-제거)

---

## 0. 내 Mac 확인

```bash
uname -m
sysctl -n machdep.cpu.brand_string
sysctl hw.memsize | awk '{print $2/1024/1024/1024 " GB"}'
```

| `uname -m` | Mac 종류 | LLM 가속 | 받을 컨테이너 이미지 |
|---|---|---|---|
| `arm64` | Apple Silicon (M1~M5) | ✅ Metal/MLX 자동 | `dscore-qa-perf-allinone-arm64-*.tar.gz` |
| `x86_64` | Intel Mac | ❌ CPU only | `dscore-qa-perf-allinone-amd64-*.tar.gz` |

> Intel Mac에서도 동작하지만 LLM 추론이 매우 느립니다 (10-20배). 가능하면 Apple Silicon 사용 권장.

| RAM | 권장 모델 |
|---|---|
| 8 GB | gemma4:e2b (기본) |
| 16 GB | gemma4:e2b 또는 qwen2.5:7b |
| 32 GB+ | qwen2.5:7b, qwen2.5:14b, deepseek-r1:7b |

---

## 1. Ollama 호스트 설치 (필수)

### 1.1 인터넷 가능 환경 — 직접 설치

1. https://ollama.com/download/mac → **"Download for macOS"** 클릭 → `Ollama.dmg` 다운로드
2. DMG 마운트 → **Ollama.app** 을 Applications 폴더로 드래그
3. **Ollama.app** 실행 → 메뉴바에 🦙 아이콘 표시되면 설치 완료
4. 터미널에서 확인:
   ```bash
   ollama --version
   ollama serve --help     # CLI 도움말 (이미 자동 기동 중이라 여기서 또 serve 하지 말 것)
   ```
5. 모델 다운로드:
   ```bash
   ollama pull gemma4:e2b          # 7.2GB, 1-5분
   ollama list                      # 설치된 모델 목록
   ollama run gemma4:e2b "ping"    # 동작 확인 (Ctrl+D 종료)
   ```

### 1.2 폐쇄망 환경 — USB 반입 설치

#### A) 인터넷 가능 머신에서 자산 수집

```bash
# 1) Ollama DMG 다운로드
curl -L -o ~/Downloads/Ollama.dmg https://ollama.com/download/Ollama.dmg

# 2) Ollama 임시 설치 → 모델 풀링 → 모델 디렉토리 백업
# (Ollama.app 설치 후)
ollama pull gemma4:e2b

# 모델 디렉토리 통째로 archive
cd ~/.ollama
tar czf ~/Downloads/ollama-models-gemma4-e2b.tgz models/
```

#### B) 폐쇄망 Mac에 반입 + 설치

```bash
# 1) USB의 파일을 다운로드 폴더로
cp /Volumes/USB/Ollama.dmg ~/Downloads/
cp /Volumes/USB/ollama-models-gemma4-e2b.tgz ~/Downloads/

# 2) Ollama 설치
open ~/Downloads/Ollama.dmg
# DMG 마운트 후 Applications 로 드래그

# 3) Ollama.app 1회 실행 → 종료 (모델 디렉토리 ~/.ollama/ 가 생성됨)
open -a Ollama
sleep 5
osascript -e 'quit app "Ollama"'

# 4) 모델 디렉토리 복원
mkdir -p ~/.ollama
cd ~/.ollama
tar xzf ~/Downloads/ollama-models-gemma4-e2b.tgz

# 5) Ollama 재시작
open -a Ollama
sleep 5
ollama list
# → gemma4:e2b 가 보여야 함
```

> ⚠️ **macOS Gatekeeper 함정**: 폐쇄망 Mac에서 `Ollama.app` 첫 실행 시 코드 서명
> OCSP 호출이 timeout 될 수 있습니다 (앱이 5-10초 멈춘 후 정상 기동). 이를 회피
> 하려면 인터넷 연결된 환경에서 1회 실행 후 폐쇄망에 반입하면 더 부드럽습니다.

### 1.3 Ollama 외부 바인드 설정 (Docker 컨테이너에서 호출하려면 필수)

기본값은 `127.0.0.1:11434` (호스트만 접근 가능). Docker 컨테이너에서 호출하려면
`0.0.0.0:11434` 로 변경해야 합니다.

**방법 1 — Ollama.app GUI** (Ollama 0.4+):
1. 메뉴바 🦙 → **Settings** → **General**
2. **Allow external connections** 체크
3. 자동 재시작

**방법 2 — 환경변수 (영구)**:
```bash
# zsh
echo 'export OLLAMA_HOST=0.0.0.0:11434' >> ~/.zshrc
echo 'export OLLAMA_NOPRUNE=1' >> ~/.zshrc       # 폐쇄망: 모델 자동 정리 방지
echo 'export OLLAMA_KEEP_ALIVE=24h' >> ~/.zshrc  # 모델 메모리 유지
source ~/.zshrc

# Ollama.app 환경변수 적용 (재시작)
launchctl setenv OLLAMA_HOST 0.0.0.0:11434
launchctl setenv OLLAMA_NOPRUNE 1
launchctl setenv OLLAMA_KEEP_ALIVE 24h
osascript -e 'quit app "Ollama"' && sleep 2 && open -a Ollama
```

확인:
```bash
# 호스트 자기 자신
curl -sf http://localhost:11434/api/tags

# Mac 자신의 IP (다른 머신에서 호출 가능한지)
ifconfig | grep "inet " | grep -v 127.0.0.1
# 위에서 본 IP 로
curl -sf http://<Mac-IP>:11434/api/tags
```

### 1.4 폐쇄망에서 자동 업데이트 차단 (선택)

Ollama 는 기본적으로 자동 업데이트를 시도합니다. 폐쇄망이면 호출 자체가 실패하므로
실질 무력화되지만, 완전히 차단하려면 macOS 방화벽이나 `pf` (또는 사내 DNS) 로
`ollama.com` outbound 차단.

```bash
# 간단 호스트 파일 차단 (관리자 권한)
echo "127.0.0.1 ollama.com registry.ollama.ai" | sudo tee -a /etc/hosts
sudo dscacheutil -flushcache
```

---

## 2. Docker Desktop 설치

### 2.1 다운로드 / 설치

1. https://www.docker.com/products/docker-desktop/ 접속
2. Apple Silicon: **"Download for Mac - Apple Chip"**
   Intel Mac: **"Download for Mac - Intel Chip"**
3. `Docker.dmg` 마운트 → Applications 폴더로 드래그
4. **Docker.app** 실행 → 라이선스 동의 → 백그라운드 기동 대기 (메뉴바에 🐳 아이콘)
5. 터미널 확인:
   ```bash
   docker --version          # Docker version 26.x.x 이상
   docker buildx version     # github.com/docker/buildx v0.x.x
   ```

### 2.2 리소스 할당 (중요)

메뉴바 🐳 → **Settings** → **Resources**:

| 항목 | 권장 |
|---|---|
| **CPUs** | 4 이상 |
| **Memory** | 4-6 GB (**컨테이너용만**, Ollama 모델 메모리는 별도) |
| **Swap** | 1 GB |
| **Disk image size** | 30 GB 이상 |

> 호스트의 총 RAM 중 Ollama 모델(7-15GB) + Docker(4-6GB) + macOS(4GB) + 여유 가
> 모두 들어가야 합니다. 16GB Mac 이라면 Docker Memory를 4GB로 제한.

설정 후 **Apply & Restart**.

### 2.3 호스트 명령 도구 (선택)

```bash
brew install jq tree
brew install --cask visual-studio-code
```

---

## 3. JMeter 컨테이너 이미지 받기 / 빌드

### 3.1 사내 빌드 머신에서 받은 경우

```bash
ls -lh ~/Downloads/dscore-qa-perf-allinone-*.tar.gz

# 무결성 검증
cd ~/Downloads
shasum -a 256 -c dscore-qa-perf-allinone-arm64-*.tar.gz.sha256
```

### 3.2 Mac 에서 직접 빌드

```bash
git clone <repo-url> ttcScripts
cd ttcScripts/e2e-pipeline

# Apple Silicon
TARGET_PLATFORMS=linux/arm64 ./perf-offline/build-allinone.sh

# Intel Mac
TARGET_PLATFORMS=linux/amd64 ./perf-offline/build-allinone.sh
```

빌드 시간: 5-15분/아키텍처 (Ollama 모델 미포함이라 짧음).

---

## 4. 컨테이너 기동

### 4.1 사전 점검 — 호스트 Ollama 가 컨테이너에서 보이는가?

```bash
# 호스트에서 Ollama 응답 확인 (이미 §1.3 에서 했지만 다시)
curl -sf http://localhost:11434/api/tags

# Docker Desktop 의 host.docker.internal 도달 확인 (테스트 컨테이너로)
docker run --rm alpine sh -c "wget -q -O - http://host.docker.internal:11434/api/tags"
# → JSON 응답이 나와야 함. 안 나오면 §9.5 트러블슈팅 참조
```

### 4.2 이미지 로드

```bash
cd ~/Downloads
docker load -i dscore-qa-perf-allinone-arm64-*.tar.gz
docker images | grep dscore-qa-perf
# → dscore-qa-perf   allinone-arm64   <ID>   ...   ~2.5GB
```

### 4.3 컨테이너 기동

```bash
docker run -d \
  --name perf-allinone \
  -p 18090:18090 \
  -v perf-data:/data \
  -e JMETER_GUI_AUTOSTART=true \
  -e TZ=Asia/Seoul \
  --add-host host.docker.internal:host-gateway \
  --restart unless-stopped \
  dscore-qa-perf:allinone-arm64        # ← Intel Mac이면 -amd64
```

각 옵션:
| 옵션 | 의미 |
|---|---|
| `-d` | 백그라운드 실행 |
| `--name perf-allinone` | 컨테이너 이름 |
| `-p 18090:18090` | 호스트 18090 → 컨테이너 18090 (브라우저 GUI) |
| `-v perf-data:/data` | named volume — 시나리오/결과 영속 |
| `-e JMETER_GUI_AUTOSTART=true` | 기동 시 JMeter GUI 자동 시작 |
| `-e TZ=Asia/Seoul` | 컨테이너 시간대 |
| `--add-host ...` | (Mac/Win Docker Desktop은 자동이지만 명시해도 무해) |
| `--restart unless-stopped` | Mac 재부팅 후 자동 재기동 |

> ⚠️ **포트 11434는 매핑하지 않음**. Ollama는 호스트가 직접 들고 있고, 컨테이너는
> `host.docker.internal:11434` 로 호출합니다.

### 4.4 기동 진행 관찰

```bash
docker logs -f perf-allinone
# 다음 메시지가 보이면 준비 완료:
#   [entrypoint-perf]   ✓ 호스트 Ollama 응답 OK. 사용 가능 모델: gemma4:e2b
#   [entrypoint-perf] 준비 완료.
#   [entrypoint-perf]   GUI:    http://<host>:18090
# Ctrl+C 로 종료 (컨테이너는 계속 실행)
```

> 만약 `✗ 호스트 Ollama 도달 불가` 가 보이면 §1.3 의 외부 바인드 설정과
> §9.5 트러블슈팅 확인.

---

## 5. 브라우저로 접속

### 5.1 GUI 접속

Safari / Chrome / Firefox 등에서:

```
http://localhost:18090
```

다음과 같은 화면이 나타나면 성공:
1. noVNC 페이지 (검은 배경 + JMeter 로고)
2. 잠시 후 JMeter Swing GUI 자동 표시
3. (안 보이면 [9.1 GUI가 안 뜸](#91-gui가-검은-화면이거나-회색-화면-그대로) 참조)

### 5.2 한국어 입력 (필요 시)

JMeter Swing GUI는 호스트의 IME를 직접 받지 못하므로:

1. Mac 호스트에서 한글 타이핑 → 복사 (`Cmd+C`)
2. JMeter 입력 칸에서 우클릭 → **Paste** (또는 `Ctrl+V`)
   - noVNC 안에서는 `Cmd+V` 가 아니라 `Ctrl+V`

---

## 6. 첫 시나리오 실행

샘플 시나리오가 이미 동봉되어 있습니다 ([sample-http-get.jmx](scenarios-seed/sample-http-get.jmx)).
첫 기동 시 자동으로 `/data/jmeter/scenarios/` 에 복사됩니다.

### 6.1 GUI에서 열기

JMeter GUI에서:
1. **File → Open** → `/data/jmeter/scenarios/sample-http-get.jmx`
2. Test Plan 펼쳐 구조 확인 (Thread Group → HTTP Request → Response Assertion)
3. User Defined Variables의 `HOST`, `PORT`, `PATH` 를 시험 대상으로 변경
4. 상단 ▶ (녹색 화살표) 클릭 → 즉시 실행
5. **Summary Report** 리스너에서 평균/최대 응답시간 확인

### 6.2 jmeter.ai (Feather Wand) 첫 사용 — 호스트 Ollama 호출

1. 우측 패널 또는 메뉴에서 **Feather Wand** 클릭
2. 채팅창에 한국어로 요청:
   ```
   localhost:18090 의 /vnc.html 경로에 30초 동안 10명이 동시 GET 요청하는
   시나리오를 만들어줘. 응답 코드 200 검증 포함.
   ```
3. Feather Wand가 → **호스트 Ollama (host.docker.internal:11434)** → `gemma4:e2b` 호출
4. Mac 호스트의 Activity Monitor에서 `ollama` 프로세스의 GPU% 가 치솟는 것 확인 가능
5. 응답이 시나리오 트리에 자동 추가됨 → ▶ 실행

> 첫 호출은 모델 로딩에 5-15초. 이후는 1-3초.
> Apple Silicon M2 Pro 16GB 기준 `gemma4:e2b` 가 대략 50 토큰/초.

---

## 7. 정식 부하시험 (CLI)

### 7.1 GUI 내리기

```bash
docker exec perf-allinone supervisorctl stop jmeter-gui
```

### 7.2 CLI 실행

```bash
TS=$(date +%Y%m%d-%H%M%S)

docker exec perf-allinone jmeter -n \
  -t /data/jmeter/scenarios/sample-http-get.jmx \
  -l /data/jmeter/results/sample-${TS}.jtl \
  -e -o /data/jmeter/reports/sample-${TS}/ \
  -Jhost=httpbin.org -Jport=443 -Jpath=/get \
  -Jthreads=20 -Jramp=10 -Jloops=50
```

### 7.3 결과 가져오기

```bash
mkdir -p ~/perf-reports
docker cp perf-allinone:/data/jmeter/reports/sample-${TS}/ ~/perf-reports/
docker cp perf-allinone:/data/jmeter/results/sample-${TS}.jtl ~/perf-reports/

open ~/perf-reports/sample-${TS}/index.html
```

### 7.4 GUI 다시 켜기

```bash
docker exec perf-allinone supervisorctl start jmeter-gui
```

---

## 8. 편의: 도우미 셸 함수

`~/.zshrc` 또는 `~/.bash_profile` 에 추가:

```bash
# JMeter perf-allinone 도우미
perf() {
  case "$1" in
    start)   docker start perf-allinone ;;
    stop)    docker stop perf-allinone ;;
    restart) docker restart perf-allinone ;;
    logs)    docker logs -f perf-allinone ;;
    sh)      docker exec -it perf-allinone bash ;;
    gui-on)  docker exec perf-allinone supervisorctl start jmeter-gui ;;
    gui-off) docker exec perf-allinone supervisorctl stop jmeter-gui ;;
    open)    open "http://localhost:18090" ;;
    ollama)
      echo "─── 호스트 Ollama ───"
      curl -sf http://localhost:11434/api/tags | python3 -m json.tool
      echo "─── 컨테이너에서 본 호스트 Ollama ───"
      docker exec perf-allinone curl -sf http://host.docker.internal:11434/api/tags | python3 -m json.tool
      ;;
    model)
      # 사용: perf model <new-model>
      shift
      [ -z "$1" ] && { echo "사용: perf model <model-name>"; return 1; }
      docker exec perf-allinone sed -i "s|^ollama.default.model=.*|ollama.default.model=$1|" \
        /opt/apache-jmeter/bin/user.properties
      docker exec perf-allinone supervisorctl restart jmeter-gui
      echo "→ 모델 변경 완료: $1 (jmeter-gui 재시작됨)"
      ;;
    run)
      # 사용: perf run <jmx파일명> [-Jkey=val ...]
      shift
      local jmx="$1"; shift
      local ts; ts=$(date +%Y%m%d-%H%M%S)
      docker exec perf-allinone jmeter -n \
        -t "/data/jmeter/scenarios/${jmx}" \
        -l "/data/jmeter/results/${jmx%.jmx}-${ts}.jtl" \
        -e -o "/data/jmeter/reports/${jmx%.jmx}-${ts}/" \
        "$@"
      docker cp "perf-allinone:/data/jmeter/reports/${jmx%.jmx}-${ts}/" ~/perf-reports/
      open "$HOME/perf-reports/${jmx%.jmx}-${ts}/index.html"
      ;;
    *)
      echo "사용법: perf {start|stop|restart|logs|sh|gui-on|gui-off|open|ollama|model <name>|run <jmx> [-J...]}"
      ;;
  esac
}
```

```bash
source ~/.zshrc

# 사용 예
perf open                                       # 브라우저로 GUI 열기
perf ollama                                     # 호스트/컨테이너 양쪽 Ollama 상태
perf model qwen2.5:7b                          # jmeter.ai 모델 변경
perf run sample-http-get.jmx -Jhost=example.com -Jthreads=100
perf logs
perf gui-off
```

---

## 9. 트러블슈팅

### 9.1 GUI가 검은 화면이거나 회색 화면 그대로

```bash
docker exec perf-allinone supervisorctl status
docker exec perf-allinone supervisorctl restart jmeter-gui
# 브라우저 새로고침 (Cmd+R)
```

### 9.2 `docker run` 시 "port is already allocated"

```bash
sudo lsof -i :18090
# 다른 포트로:
docker run -d --name perf-allinone -p 28090:18090 ...
# 접속 URL: http://localhost:28090
```

### 9.3 Apple Silicon 빌드 시 "no matching manifest for linux/arm64"

거의 발생 안 함. 발생 시 Docker Desktop 재시작:
```bash
osascript -e 'quit app "Docker"' && open -a Docker
```

### 9.4 Ollama 응답이 느림 (10초+)

```bash
# 1) Activity Monitor 에서 ollama 프로세스 확인 — GPU% 가 0이면 Metal 가속 안 됨
# 2) Ollama 버전 확인
ollama --version
# 3) 모델 메모리 유지 (KEEP_ALIVE)
launchctl setenv OLLAMA_KEEP_ALIVE 24h
osascript -e 'quit app "Ollama"' && sleep 2 && open -a Ollama
```

### 9.5 컨테이너에서 호스트 Ollama 도달 불가 (`✗ 호스트 Ollama 도달 불가`)

원인 후보:
1. **Ollama 가 127.0.0.1 에만 바인드** — `OLLAMA_HOST=0.0.0.0:11434` 설정 필요 (§1.3)
2. **Ollama 가 안 떠 있음** — 메뉴바 🦙 아이콘 확인, 없으면 `open -a Ollama`
3. **Mac 방화벽 차단** — System Settings → Network → Firewall → Ollama 허용

검증:
```bash
# 호스트에서
lsof -iTCP:11434 -sTCP:LISTEN
# → ollama 프로세스가 *.11434 또는 localhost:11434

# 컨테이너에서
docker exec perf-allinone getent hosts host.docker.internal
# → IP 가 나와야 함 (Docker Desktop 자동 매핑)

docker exec perf-allinone curl -v http://host.docker.internal:11434/api/tags
# → connection refused 면 Ollama 바인드 문제
# → no route to host 면 Docker Desktop 네트워크 문제 (재시작)
```

### 9.6 jmeter.ai 호출 시 응답 없음 — 모델이 없음

```bash
# 호스트에서
ollama list
# 비어있으면:
ollama pull gemma4:e2b
```

### 9.7 노트북 슬립 후 Ollama 응답 안 함

```bash
# Ollama 재시작
osascript -e 'quit app "Ollama"' && sleep 2 && open -a Ollama
sleep 5
ollama list
```

### 9.8 디스크 용량 부족

```bash
# Docker Desktop 사용량
docker system df

# 정리 (perf-data 보존하려면 따로 백업)
docker system prune -a --volumes

# Ollama 모델 정리
ollama list
ollama rm <unused-model>
```

### 9.9 `docker exec` 명령에서 한글 깨짐

```bash
docker exec -it -e LANG=C.UTF-8 perf-allinone bash
```

### 9.10 Ollama 자동 업데이트 트리거됨 (폐쇄망에서 실패해도 동작은 함)

§1.4 의 호스트 파일 차단으로 완전 봉쇄.

---

## 10. 정리 (제거)

```bash
# 1) 컨테이너 중지/제거
docker stop perf-allinone
docker rm perf-allinone

# 2) 이미지 제거
docker rmi dscore-qa-perf:allinone-arm64

# 3) 볼륨까지 완전 제거 (시나리오/결과 모두 삭제됨 — 주의!)
docker volume rm perf-data

# 4) Ollama 까지 모두 제거하려면
osascript -e 'quit app "Ollama"'
mv /Applications/Ollama.app ~/.Trash/
rm -rf ~/.ollama          # 모델 (대용량) 까지 삭제
launchctl unsetenv OLLAMA_HOST OLLAMA_NOPRUNE OLLAMA_KEEP_ALIVE
```

> 시나리오만 보존하고 컨테이너만 다시 만들고 싶다면 위 1, 2 단계만 — `perf-data` 볼륨이 그대로 마운트됩니다.

---

## 다음 단계

- 더 깊은 JMeter 사용법 → **[README.md 5장 — JMeter 사용법 (심화)](README.md#5-jmeter-사용법-심화)**
- 모델 권장 표 / 모델 변경 절차 → **[README.md 2장 — LLM 모델 권장 가이드](README.md#2-llm-모델-권장-가이드)**
- 빌드 옵션 / 외부 자산 출처 → **[README.md 4장 — 빌드](README.md#4-빌드-온라인-머신-전용)**
- Windows 호스트 운영자에게 같은 환경 전달 → **[README-windows.md](README-windows.md)**
