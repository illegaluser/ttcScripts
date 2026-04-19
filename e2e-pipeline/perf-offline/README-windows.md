# 🪟 Windows 사용자용 — JMeter Performance All-in-One 설치 및 운영

> Windows 10/11 (64-bit) + Docker Desktop + WSL2 백엔드 환경 기준.
> **호스트 Ollama 의 NVIDIA CUDA 가속**을 활용해 jmeter.ai (Feather Wand) 추론 속도가 빠릅니다.

## ⚡ TL;DR — 자동 설치

번들 USB 를 받았다면 (전형 케이스):

1. USB 의 `perf-allinone-bundle-*` 폴더를 `C:\` 또는 사용자 폴더로 **복사** (USB에서 직접 실행 비권장)
2. PowerShell 우클릭 → **'관리자 권한으로 실행'**
3. 폴더 진입 후 실행:

```powershell
cd C:\perf-allinone-bundle-20260420-103000
.\install-windows.ps1
```

> 첫 실행 시 "스크립트 실행이 비활성화" 에러가 나면:
> ```powershell
> Set-ExecutionPolicy -Scope Process Bypass
> .\install-windows.ps1
> ```

스크립트가 자동으로:
1. **관리자 권한 + Docker Desktop 가동** 확인
2. assets/ 무결성 검증
3. **Ollama standalone ZIP 압축 해제** (`C:\ollama\`)
4. **PATH + OLLAMA 환경변수** 등록 (시스템 영구)
5. **NSSM 으로 Ollama 서비스 등록** (Windows 부팅 시 자동 기동)
6. **모델 archive 복원** (`%UserProfile%\.ollama\models`)
7. **JMeter 컨테이너 로드 + 기동**
8. **호스트 ↔ 컨테이너 도달 검증 + 브라우저 자동 열기**

총 소요: 2-5분.

**관리 명령**:
```powershell
.\install-windows.ps1 -Check        # 현재 상태 점검
.\install-windows.ps1 -Uninstall    # 완전 제거 (모델/볼륨 삭제 여부 대화형 확인)
```

> 사전 요구: **Docker Desktop + WSL2** 만 미리 설치해 두세요. 그 외 모든 것
> (Ollama, NSSM 서비스, 환경변수, 모델, 컨테이너)은 스크립트가 처리합니다.
> Docker Desktop 미설치 시 [§2 WSL2 + Docker Desktop 설치](#2-wsl2--docker-desktop-설치) 참조.

자동 스크립트가 실패하거나 수동으로 단계별 진행하고 싶다면 아래 §1 부터 따라가세요.

---

## 목차 (수동 설치 / 트러블슈팅)

0. [내 Windows 확인](#0-내-windows-확인)
1. [Ollama 호스트 설치 (필수)](#1-ollama-호스트-설치-필수)
2. [WSL2 + Docker Desktop 설치](#2-wsl2--docker-desktop-설치)
3. [JMeter 컨테이너 이미지 받기 / 빌드](#3-jmeter-컨테이너-이미지-받기--빌드)
4. [컨테이너 기동](#4-컨테이너-기동)
5. [브라우저로 접속](#5-브라우저로-접속)
6. [첫 시나리오 실행](#6-첫-시나리오-실행)
7. [정식 부하시험 (CLI)](#7-정식-부하시험-cli)
8. [편의: PowerShell 함수](#8-편의-powershell-함수)
9. [트러블슈팅](#9-트러블슈팅)
10. [정리 (제거)](#10-정리-제거)

---

## 0. 내 Windows 확인

PowerShell (관리자 권한 불필요):

```powershell
# Windows 버전
[System.Environment]::OSVersion.Version
# Major 10 / Build 19041 이상 필요

# 64-bit 확인
[Environment]::Is64BitOperatingSystem

# GPU 정보
Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion
```

| GPU | LLM 가속 | 받을 컨테이너 이미지 |
|---|---|---|
| **NVIDIA RTX/GTX (CUDA)** | ✅ Ollama 자동 | `dscore-qa-perf-allinone-amd64-*.tar.gz` |
| **AMD Radeon RX/PRO** | ⚠️ ROCm 별도 | `dscore-qa-perf-allinone-amd64-*.tar.gz` |
| **Intel iGPU / Ryzen AI APU** | ❌ CPU only | `dscore-qa-perf-allinone-amd64-*.tar.gz` |
| ARM64 Snapdragon (Surface Pro X / Copilot+) | CPU only | `dscore-qa-perf-allinone-arm64-*.tar.gz` |

| GPU VRAM | 권장 모델 |
|---|---|
| 6 GB | gemma4:e2b (기본) |
| 8 GB | gemma4:e2b 또는 qwen2.5:7b (4-bit) |
| 12 GB | qwen2.5:7b, deepseek-r1:7b |
| 16 GB+ | qwen2.5:14b, deepseek-r1:14b |
| **CPU only** | gemma4:e2b 만 (느림 주의) |

---

## 1. Ollama 호스트 설치 (필수)

### 1.1 인터넷 가능 환경 — 직접 설치

#### 옵션 A: EXE 인스톨러 (간편, 자동 업데이트 포함)

1. https://ollama.com/download/windows → **"Download for Windows"** 클릭 → `OllamaSetup.exe`
2. 실행 → 설치 (관리자 권한 불필요)
3. 트레이에 🦙 아이콘 표시되면 설치 완료
4. PowerShell 확인:
   ```powershell
   ollama --version
   ```

#### 옵션 B: standalone ZIP (폐쇄망 권장, 자동 업데이트 없음)

1. https://ollama.com/download/ollama-windows-amd64.zip 다운로드
2. 압축 해제 → 예: `C:\ollama\`
3. 환경변수 `Path` 에 `C:\ollama\` 추가 (시스템 환경변수)
4. PowerShell:
   ```powershell
   ollama --version
   ollama serve   # 포어그라운드 실행 (다음 절에서 서비스화)
   ```

#### 모델 다운로드

```powershell
ollama pull gemma4:e2b           # 7.2GB, 1-5분
ollama list                       # 설치된 모델 목록
ollama run gemma4:e2b "ping"     # 동작 확인 (Ctrl+D 종료)
```

### 1.2 폐쇄망 환경 — USB 반입 설치

#### A) 인터넷 가능 머신에서 자산 수집

```powershell
# 1) Ollama standalone ZIP 다운로드
Invoke-WebRequest -Uri "https://ollama.com/download/ollama-windows-amd64.zip" `
                  -OutFile "$env:UserProfile\Downloads\ollama-windows-amd64.zip"

# (NVIDIA 사용 시 드라이버 인스톨러도 같이 받기)
# https://www.nvidia.com/Download/index.aspx 에서 GPU/OS에 맞는 .exe 다운로드

# 2) Ollama 임시 설치 → 모델 풀링 → 모델 디렉토리 백업
# (옵션 A로 임시 설치 후)
ollama pull gemma4:e2b

# 모델 디렉토리 통째로 archive
$src = "$env:UserProfile\.ollama\models"
$dst = "$env:UserProfile\Downloads\ollama-models-gemma4-e2b.zip"
Compress-Archive -Path $src -DestinationPath $dst -Force
```

#### B) 폐쇄망 Windows에 반입 + 설치

```powershell
# 1) USB의 파일들을 Downloads로
Copy-Item E:\ollama-windows-amd64.zip $env:UserProfile\Downloads\
Copy-Item E:\ollama-models-gemma4-e2b.zip $env:UserProfile\Downloads\

# 2) NVIDIA 드라이버 설치 (해당 시) — .exe 더블클릭

# 3) Ollama 압축 해제
Expand-Archive -Path "$env:UserProfile\Downloads\ollama-windows-amd64.zip" `
               -DestinationPath "C:\ollama\" -Force

# 4) PATH 추가 (시스템 영구)
[Environment]::SetEnvironmentVariable(
  "Path",
  ([Environment]::GetEnvironmentVariable("Path","Machine") + ";C:\ollama"),
  "Machine"
)
# 새 PowerShell 창 열어 적용 확인
ollama --version

# 5) 모델 디렉토리 복원
$dst = "$env:UserProfile\.ollama"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Expand-Archive -Path "$env:UserProfile\Downloads\ollama-models-gemma4-e2b.zip" `
               -DestinationPath $dst -Force

ollama list
# → gemma4:e2b 가 보여야 함
```

### 1.3 Ollama 외부 바인드 설정 (Docker 컨테이너에서 호출하려면 필수)

기본값은 `127.0.0.1:11434`. 컨테이너에서 호출하려면 `0.0.0.0:11434` 로 변경.

#### 방법 1 — Ollama.app GUI (옵션 A 인스톨러 사용 시)
1. 트레이 🦙 → **Settings**
2. **Allow connections from network devices** 체크 → 자동 재시작

#### 방법 2 — 시스템 환경변수 (영구, 권장)

PowerShell **관리자 권한** 으로:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NOPRUNE", "1", "Machine")    # 폐쇄망: 모델 자동 정리 방지
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "24h", "Machine") # 모델 메모리 유지
[Environment]::SetEnvironmentVariable("OLLAMA_NO_CLOUD", "1", "Machine")    # 클라우드 호출 비활성

# Ollama 재시작
Stop-Process -Name "ollama","ollama app" -Force -ErrorAction SilentlyContinue
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
# (또는 트레이에서 Quit → 다시 실행)
```

확인:
```powershell
# 호스트 자기 자신
Invoke-WebRequest http://localhost:11434/api/tags | Select-Object -ExpandProperty Content

# Windows IP
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.PrefixOrigin -eq "Dhcp"} | Select IPAddress
# 그 IP 로
Invoke-WebRequest http://<IP>:11434/api/tags
```

### 1.4 Ollama 를 Windows 서비스로 등록 (standalone ZIP 사용 시)

NSSM (Non-Sucking Service Manager) 으로 백그라운드 서비스화 — Windows 부팅 시 자동 기동.

```powershell
# 1) NSSM 다운로드 (관리자 권한 PowerShell)
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" `
                  -OutFile "$env:Temp\nssm.zip"
Expand-Archive -Path "$env:Temp\nssm.zip" -DestinationPath "C:\nssm\" -Force

# 2) Ollama 서비스 등록
& "C:\nssm\nssm-2.24\win64\nssm.exe" install Ollama "C:\ollama\ollama.exe" "serve"
& "C:\nssm\nssm-2.24\win64\nssm.exe" set Ollama AppEnvironmentExtra `
  "OLLAMA_HOST=0.0.0.0:11434" `
  "OLLAMA_NOPRUNE=1" `
  "OLLAMA_KEEP_ALIVE=24h" `
  "OLLAMA_NO_CLOUD=1"
& "C:\nssm\nssm-2.24\win64\nssm.exe" set Ollama Start SERVICE_AUTO_START

# 3) 서비스 시작
Start-Service Ollama
Get-Service Ollama
# Status: Running 이어야 함

# 4) 동작 확인
Invoke-WebRequest http://localhost:11434/api/tags
```

### 1.5 폐쇄망에서 자동 업데이트 차단 (선택)

EXE 인스톨러는 자동 업데이트를 시도합니다. 폐쇄망이면 호출 자체가 실패하지만,
완전 차단하려면 호스트 파일 또는 사내 DNS:

```powershell
# 관리자 권한 메모장으로 hosts 편집
notepad "$env:WinDir\System32\drivers\etc\hosts"
# 다음 줄 추가:
# 127.0.0.1 ollama.com registry.ollama.ai
```

---

## 2. WSL2 + Docker Desktop 설치

### 2.1 WSL2 활성화

PowerShell **관리자 권한**:

```powershell
# WSL + Virtual Machine Platform 활성화
wsl --install
# 또는 명시적으로:
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

Restart-Computer
```

재부팅 후:
```powershell
wsl --status
# Default Version: 2

wsl --update
wsl --set-default-version 2
```

### 2.2 Docker Desktop for Windows 설치

1. https://www.docker.com/products/docker-desktop/ → **"Download for Windows - AMD64"**
2. `Docker Desktop Installer.exe` 실행
3. 옵션:
   - ✅ **Use WSL 2 instead of Hyper-V (recommended)**
   - ✅ **Add shortcut to desktop**
4. 설치 완료 후 자동 재부팅
5. Docker Desktop 첫 실행 → 라이선스 동의 → 백그라운드 기동
6. PowerShell 확인:
   ```powershell
   docker --version
   docker buildx version
   ```

### 2.3 리소스 할당 (중요)

`%UserProfile%\.wslconfig` 편집:

```powershell
notepad "$env:UserProfile\.wslconfig"
```

내용:
```ini
[wsl2]
memory=6GB
processors=4
swap=2GB
localhostForwarding=true
```

| 항목 | 권장 |
|---|---|
| **memory** | 4-6 GB (**컨테이너용만**, Ollama 모델 메모리는 호스트 VRAM/RAM이 담당) |
| **processors** | 4 이상 |
| **swap** | 2 GB |

> 호스트 시스템 RAM에서 Ollama 모델(VRAM 부족 시 RAM 오프로드) + WSL2(4-6GB) +
> Windows(4-6GB) + 여유가 모두 들어가야 합니다. 16GB Windows라면 .wslconfig
> `memory=4GB` 로 제한.

적용:
```powershell
wsl --shutdown
# Docker Desktop 트레이 → Quit → 다시 실행
```

### 2.4 Windows Defender 백신 예외 (선택)

대량 결과 파일 I/O 시 백신이 매번 스캔하면 측정 결과 왜곡 가능.

**Windows 보안 → 바이러스 및 위협 방지 → 설정 관리 → 제외 항목**:
```
%LocalAppData%\Docker
%UserProfile%\Downloads\dscore-qa-perf-allinone-*.tar.gz
%UserProfile%\perf-reports
%UserProfile%\.ollama
C:\ollama
```

---

## 3. JMeter 컨테이너 이미지 받기 / 빌드

### 3.1 사내 빌드 머신에서 받은 경우

```powershell
cd $env:UserProfile\Downloads
Get-ChildItem -Filter "dscore-qa-perf-allinone-*.tar.gz"

# 무결성 검증
$expected = (Get-Content "dscore-qa-perf-allinone-amd64-*.tar.gz.sha256" -Raw).Split(" ")[0]
$actual = (Get-FileHash "dscore-qa-perf-allinone-amd64-*.tar.gz" -Algorithm SHA256).Hash.ToLower()
if ($expected -eq $actual) { "✅ OK" } else { "❌ 해시 불일치" }
```

### 3.2 Windows에서 직접 빌드 (WSL2 안에서)

```powershell
wsl
# (이하 WSL Ubuntu 안에서)
git clone <repo-url> ttcScripts
cd ttcScripts/e2e-pipeline

TARGET_PLATFORMS=linux/amd64 ./perf-offline/build-allinone.sh
# 또는 양 아키텍처 (Mac 사용자에게도 배포)
TARGET_PLATFORMS=linux/amd64,linux/arm64 ./perf-offline/build-allinone.sh
```

산출물 위치 (Windows에서):
```powershell
explorer "\\wsl$\Ubuntu\home\<user>\ttcScripts\e2e-pipeline\"
```

---

## 4. 컨테이너 기동

### 4.1 사전 점검 — 호스트 Ollama 가 컨테이너에서 보이는가?

```powershell
# 호스트에서 Ollama 응답 확인
Invoke-WebRequest http://localhost:11434/api/tags

# Docker Desktop의 host.docker.internal 도달 확인
docker run --rm alpine sh -c "wget -q -O - http://host.docker.internal:11434/api/tags"
# → JSON 응답이 나와야 함. 안 나오면 §9.5 트러블슈팅
```

### 4.2 이미지 로드

```powershell
cd $env:UserProfile\Downloads
docker load -i .\dscore-qa-perf-allinone-amd64-20260419-143000.tar.gz
docker images | Select-String "dscore-qa-perf"
# → dscore-qa-perf  allinone-amd64  <ID>  ...  ~2.5GB
```

### 4.3 컨테이너 기동

PowerShell (백틱 ` 으로 줄바꿈):

```powershell
docker run -d `
  --name perf-allinone `
  -p 18090:18090 `
  -v perf-data:/data `
  -e JMETER_GUI_AUTOSTART=true `
  -e TZ=Asia/Seoul `
  --add-host host.docker.internal:host-gateway `
  --restart unless-stopped `
  dscore-qa-perf:allinone-amd64
```

> CMD 에서는 `^` 줄바꿈:
> ```cmd
> docker run -d ^
>   --name perf-allinone ^
>   -p 18090:18090 ^
>   -v perf-data:/data ^
>   -e JMETER_GUI_AUTOSTART=true ^
>   --add-host host.docker.internal:host-gateway ^
>   --restart unless-stopped ^
>   dscore-qa-perf:allinone-amd64
> ```

각 옵션 설명:
| 옵션 | 의미 |
|---|---|
| `-d` | 백그라운드 실행 |
| `--name perf-allinone` | 컨테이너 이름 |
| `-p 18090:18090` | 호스트 18090 → 컨테이너 18090 (브라우저 GUI) |
| `-v perf-data:/data` | named volume — 시나리오/결과 영속 |
| `-e JMETER_GUI_AUTOSTART=true` | 기동 시 JMeter GUI 자동 시작 |
| `-e TZ=Asia/Seoul` | 컨테이너 시간대 |
| `--add-host ...` | (Windows Docker Desktop은 자동이지만 명시해도 무해) |
| `--restart unless-stopped` | Windows 재부팅 후 자동 재기동 |

> ⚠️ **포트 11434는 매핑하지 않음**. Ollama는 호스트가 직접 들고 있고, 컨테이너는
> `host.docker.internal:11434` 로 호출합니다.

### 4.4 기동 진행 관찰

```powershell
docker logs -f perf-allinone
# 다음이 보이면 준비 완료:
#   [entrypoint-perf]   ✓ 호스트 Ollama 응답 OK. 사용 가능 모델: gemma4:e2b
#   [entrypoint-perf] 준비 완료.
#   [entrypoint-perf]   GUI:    http://<host>:18090
# Ctrl+C 로 종료 (컨테이너는 계속 실행)
```

> `✗ 호스트 Ollama 도달 불가` 가 보이면 §1.3 외부 바인드 + §9.5 트러블슈팅.

---

## 5. 브라우저로 접속

### 5.1 GUI 접속

Edge / Chrome / Firefox 등에서:

```
http://localhost:18090
```

다음 화면이 나타나면 성공:
1. noVNC 페이지 (검은 배경 + JMeter 로고)
2. 잠시 후 JMeter Swing GUI 자동 표시
3. (안 보이면 [9.1 GUI 안 뜸](#91-gui가-검은-화면이거나-회색-화면-그대로) 참조)

### 5.2 한국어 입력

JMeter Swing GUI는 호스트 IME 직접 받지 못함:
1. Windows 메모장 등에서 한글 타이핑 → `Ctrl+C`
2. JMeter 입력 칸에서 우클릭 → **Paste** 또는 `Ctrl+V`

### 5.3 브라우저 단축키 충돌

| Windows 단축키 | 회피 |
|---|---|
| `Ctrl+W` | 브라우저 탭 닫힘 (JMeter 미사용) |
| `F11` | 전체 화면 — noVNC 사용 시 권장 |

---

## 6. 첫 시나리오 실행

샘플 [sample-http-get.jmx](scenarios-seed/sample-http-get.jmx) 가 첫 기동 시
자동 복사됩니다.

### 6.1 GUI에서 열기

JMeter GUI:
1. **File → Open** → `/data/jmeter/scenarios/sample-http-get.jmx`
2. Test Plan 펼쳐 구조 확인
3. User Defined Variables의 `HOST`, `PORT`, `PATH` 변경
4. ▶ 클릭 → 실행
5. **Summary Report** 에서 결과 확인

### 6.2 jmeter.ai (Feather Wand) 첫 사용 — 호스트 Ollama 호출

1. 우측 패널 또는 메뉴에서 **Feather Wand** 클릭
2. 한국어로 요청:
   ```
   localhost:18090 의 /vnc.html 경로에 30초 동안 10명이 동시 GET 요청하는
   시나리오를 만들어줘. 응답 코드 200 검증 포함.
   ```
3. Feather Wand → **호스트 Ollama** → `gemma4:e2b` 호출
4. Windows 작업관리자에서 NVIDIA GPU 사용률 치솟는 것 확인 가능
5. 시나리오 트리에 자동 추가 → ▶ 실행

> NVIDIA RTX 4070 기준 `gemma4:e2b` 약 100 토큰/초.

---

## 7. 정식 부하시험 (CLI)

### 7.1 GUI 내리기

```powershell
docker exec perf-allinone supervisorctl stop jmeter-gui
```

### 7.2 CLI 실행

```powershell
$TS = Get-Date -Format "yyyyMMdd-HHmmss"

docker exec perf-allinone jmeter -n `
  -t /data/jmeter/scenarios/sample-http-get.jmx `
  -l "/data/jmeter/results/sample-$TS.jtl" `
  -e -o "/data/jmeter/reports/sample-$TS/" `
  -Jhost=httpbin.org -Jport=443 -Jpath=/get `
  -Jthreads=20 -Jramp=10 -Jloops=50
```

### 7.3 결과 가져오기

```powershell
$reportRoot = "$env:UserProfile\perf-reports"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null

docker cp "perf-allinone:/data/jmeter/reports/sample-$TS/" "$reportRoot\"
docker cp "perf-allinone:/data/jmeter/results/sample-$TS.jtl" "$reportRoot\"

Start-Process "$reportRoot\sample-$TS\index.html"
```

### 7.4 GUI 다시 켜기

```powershell
docker exec perf-allinone supervisorctl start jmeter-gui
```

---

## 8. 편의: PowerShell 함수

`$PROFILE` 에 추가:

```powershell
if (!(Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force }
notepad $PROFILE
```

다음 함수를 붙여넣고 저장:

```powershell
function Perf {
    param(
        [Parameter(Mandatory=$true)]
        [ValidateSet('start','stop','restart','logs','sh','gui-on','gui-off','open','ollama','model','run')]
        [string]$Cmd,
        [string]$Arg,
        [string[]]$JArgs
    )

    switch ($Cmd) {
        'start'   { docker start perf-allinone }
        'stop'    { docker stop perf-allinone }
        'restart' { docker restart perf-allinone }
        'logs'    { docker logs -f perf-allinone }
        'sh'      { docker exec -it perf-allinone bash }
        'gui-on'  { docker exec perf-allinone supervisorctl start jmeter-gui }
        'gui-off' { docker exec perf-allinone supervisorctl stop jmeter-gui }
        'open'    { Start-Process "http://localhost:18090" }
        'ollama'  {
            Write-Host "─── 호스트 Ollama ───"
            (Invoke-WebRequest http://localhost:11434/api/tags -UseBasicParsing).Content
            Write-Host "─── 컨테이너에서 본 호스트 Ollama ───"
            docker exec perf-allinone curl -sf http://host.docker.internal:11434/api/tags
        }
        'model'   {
            if (-not $Arg) { Write-Host "사용: Perf model <model-name>"; return }
            docker exec perf-allinone sed -i "s|^ollama.default.model=.*|ollama.default.model=$Arg|" `
                /opt/apache-jmeter/bin/user.properties
            docker exec perf-allinone supervisorctl restart jmeter-gui
            Write-Host "→ 모델 변경: $Arg"
        }
        'run'     {
            if (-not $Arg) { Write-Host "사용: Perf run <jmx파일명> -JArgs '-Jhost=...','-Jthreads=10'"; return }
            $ts = Get-Date -Format "yyyyMMdd-HHmmss"
            $base = $Arg.Replace('.jmx','')
            $argList = @(
                'exec','perf-allinone','jmeter','-n',
                '-t',"/data/jmeter/scenarios/$Arg",
                '-l',"/data/jmeter/results/$base-$ts.jtl",
                '-e','-o',"/data/jmeter/reports/$base-$ts/"
            ) + $JArgs
            & docker @argList
            $reportRoot = "$env:UserProfile\perf-reports"
            New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
            docker cp "perf-allinone:/data/jmeter/reports/$base-$ts/" "$reportRoot\"
            Start-Process "$reportRoot\$base-$ts\index.html"
        }
    }
}
```

PowerShell 재시작 후:
```powershell
Perf open                                                # 브라우저로 GUI 열기
Perf ollama                                              # Ollama 상태
Perf model qwen2.5:7b                                   # 모델 변경
Perf run sample-http-get.jmx -JArgs '-Jhost=example.com','-Jthreads=100'
Perf logs
Perf gui-off
```

> "스크립트 실행 비활성화" 에러:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

---

## 9. 트러블슈팅

### 9.1 GUI가 검은 화면이거나 회색 화면 그대로

```powershell
docker exec perf-allinone supervisorctl status
docker exec perf-allinone supervisorctl restart jmeter-gui
# 브라우저 새로고침 (Ctrl+F5)
```

### 9.2 `docker run` 시 "port is already allocated"

```powershell
Get-NetTCPConnection -LocalPort 18090 | Select-Object OwningProcess
Get-Process -Id <PID>

# 다른 포트로:
docker run -d --name perf-allinone -p 28090:18090 ...
# 접속: http://localhost:28090
```

### 9.3 Docker Desktop 시작 안 됨

```powershell
wsl --status
wsl --shutdown
# Docker Desktop 트레이 → Quit → 다시 실행
```

### 9.4 Ollama 응답 느림 (10초+)

```powershell
# 1) 작업관리자 → 성능 → GPU — 추론 중 GPU 사용률 확인
# 2) NVIDIA: nvidia-smi 로 VRAM 사용량
nvidia-smi

# 3) Ollama 환경변수 확인
[Environment]::GetEnvironmentVariable("OLLAMA_KEEP_ALIVE","Machine")

# 4) Ollama 재시작
Stop-Service Ollama; Start-Service Ollama
# 또는 EXE 인스톨러: 트레이 → Quit → 재실행
```

### 9.5 컨테이너에서 호스트 Ollama 도달 불가

```powershell
# 호스트에서 LISTEN 확인
Get-NetTCPConnection -LocalPort 11434 -State Listen
# LocalAddress 가 0.0.0.0 이어야 함 (127.0.0.1 이면 외부 바인드 안 된 상태)

# Ollama 환경변수
[Environment]::GetEnvironmentVariable("OLLAMA_HOST","Machine")
# → 0.0.0.0:11434 이어야 함. 아니면 §1.3 다시.

# 컨테이너에서 호스트 도달
docker exec perf-allinone getent hosts host.docker.internal
docker exec perf-allinone curl -v http://host.docker.internal:11434/api/tags
```

### 9.6 Windows 방화벽이 11434 차단

```powershell
# 관리자 PowerShell
New-NetFirewallRule -DisplayName "Ollama Local" `
  -Direction Inbound -Protocol TCP -LocalPort 11434 `
  -Action Allow -Profile Private,Domain
```

### 9.7 결과 파일 경로 길이 초과

```powershell
# Long Paths 활성화 (관리자)
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force

# 또는 짧은 경로 사용
docker cp "perf-allinone:/data/jmeter/reports/sample-$TS/" "C:\perf\"
```

### 9.8 WSL2 디스크 무한 증가

```powershell
# 정리 (perf-data 보존하려면 백업)
docker system prune -a --volumes
wsl --shutdown

# vhdx 압축 (관리자)
diskpart
# > select vdisk file="C:\Users\<user>\AppData\Local\Docker\wsl\data\ext4.vhdx"
# > compact vdisk
# > exit
```

### 9.9 한글 깨짐

```powershell
chcp 65001
# 영구:
Add-Content $PROFILE '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8'
```

### 9.10 Ollama 자동 업데이트 트리거 (폐쇄망)

§1.5 hosts 파일 차단 또는 standalone ZIP + NSSM 사용 (자동 업데이트 없음).

### 9.11 NVIDIA 드라이버 충돌 / WSL2 GPU 미인식

```powershell
# 호스트 Ollama 는 직접 CUDA 사용 → WSL2 GPU 패스스루 무관
# 호스트에서 동작 확인
nvidia-smi
ollama run gemma4:e2b "test"
# 위가 잘 되면 컨테이너 안의 jmeter.ai 도 호스트 GPU 가속을 받게 됨 (HTTP 호출)
```

---

## 10. 정리 (제거)

```powershell
# 1) 컨테이너 중지/제거
docker stop perf-allinone
docker rm perf-allinone

# 2) 이미지 제거
docker rmi dscore-qa-perf:allinone-amd64

# 3) 볼륨까지 완전 제거
docker volume rm perf-data

# 4) Ollama 까지 모두 제거
Stop-Service Ollama -ErrorAction SilentlyContinue
& "C:\nssm\nssm-2.24\win64\nssm.exe" remove Ollama confirm   # NSSM 사용 시
Remove-Item -Recurse -Force "C:\ollama"
Remove-Item -Recurse -Force "$env:UserProfile\.ollama"        # 모델까지
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NOPRUNE", $null, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $null, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NO_CLOUD", $null, "Machine")
```

---

## 다음 단계

- 더 깊은 JMeter 사용법 → **[README.md 5장 — JMeter 사용법 (심화)](README.md#5-jmeter-사용법-심화)**
- 모델 권장 표 / 모델 변경 절차 → **[README.md 2장 — LLM 모델 권장 가이드](README.md#2-llm-모델-권장-가이드)**
- 빌드 옵션 / 외부 자산 출처 → **[README.md 4장 — 빌드](README.md#4-빌드-온라인-머신-전용)**
- Mac 호스트 운영자에게 같은 환경 전달 → **[README-mac.md](README-mac.md)**
