# ============================================================================
# perf-allinone — Windows 폐쇄망 자동 설치 스크립트
#
# 동작 (모두 자동, 사용자 입력 거의 없음):
#   1) 사전 점검 (관리자 권한, Docker Desktop 기동, assets/ 무결성)
#   2) Ollama standalone ZIP 압축 해제 (C:\ollama\)
#   3) PATH 환경변수 + Ollama 운영 환경변수 등록 (시스템)
#   4) NSSM 으로 Ollama 서비스 등록 + 시작
#   5) 모델 archive 복원 (~/.ollama/models)
#   6) JMeter 컨테이너 이미지 로드 + 기동
#   7) 호스트 Ollama ↔ 컨테이너 도달 검증
#   8) 브라우저로 GUI 자동 열기
#
# 사용법 (관리자 PowerShell 또는 우클릭 → '관리자 권한으로 실행'):
#   .\install-windows.ps1                    # 전체 자동 설치
#   .\install-windows.ps1 -Uninstall         # 모두 제거
#   .\install-windows.ps1 -Check             # 현재 상태만 점검
#
# 매개변수 (선택):
#   -InstallRoot       기본 C:\ollama
#   -PerfDataVolume    기본 perf-data
#   -ContainerName     기본 perf-allinone
#   -OllamaBindHost    기본 0.0.0.0:11434
#   -AssetsDir         기본 .\assets
#   -SkipBrowser       지정 시 마지막 브라우저 자동 열기 건너뜀
# ============================================================================
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Check,
    [string]$InstallRoot   = "C:\ollama",
    [string]$PerfDataVolume = "perf-data",
    [string]$ContainerName  = "perf-allinone",
    [string]$OllamaBindHost = "0.0.0.0:11434",
    [string]$AssetsDir,
    [switch]$SkipBrowser
)

$ErrorActionPreference = 'Stop'

# ── 경로/상수 ────────────────────────────────────────────────────────────
if (-not $AssetsDir) { $AssetsDir = Join-Path $PSScriptRoot "assets" }
$NssmRoot   = "C:\nssm"
$NssmExe    = Join-Path $NssmRoot "nssm-2.24\win64\nssm.exe"
$OllamaExe  = Join-Path $InstallRoot "ollama.exe"
$ServiceName = "Ollama"

# ── 컬러 출력 헬퍼 ───────────────────────────────────────────────────────
function Write-Step($msg) { Write-Host ""; Write-Host "▶ $msg" -ForegroundColor Cyan -BackgroundColor Black }
function Write-Log($msg)  { Write-Host "   $msg" }
function Write-Ok($msg)   { Write-Host "   ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   ! $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host ""; Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }

# ── 헬퍼 ──────────────────────────────────────────────────────────────────
function Test-Admin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    return ([System.Security.Principal.WindowsPrincipal]::new($id)).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ArchSuffix {
    if ([System.Environment]::Is64BitOperatingSystem) {
        $arch = (Get-WmiObject Win32_Processor).Architecture
        # 0=x86, 5=ARM, 9=x64, 12=ARM64
        if ($arch -eq 12) { return "arm64" }
        return "amd64"
    }
    Write-Err "32-bit Windows 미지원"
}

function Find-ImageTar {
    $arch = Get-ArchSuffix
    $f = Get-ChildItem -Path $AssetsDir -Filter "dscore-qa-perf-allinone-$arch-*.tar.gz" `
         -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { Write-Err "이미지 tar.gz 없음: $AssetsDir\dscore-qa-perf-allinone-$arch-*.tar.gz" }
    return $f.FullName
}

function Find-ModelArchive {
    $f = Get-ChildItem -Path $AssetsDir -Filter "ollama-models-*.tgz" `
         -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { return $f.FullName } else { return $null }
}

function Verify-Sha256 {
    param([string]$FilePath)
    $sumFile = Join-Path $AssetsDir "SHA256SUMS"
    if (-not (Test-Path $sumFile)) { Write-Warn "SHA256SUMS 누락 — 무결성 검증 건너뜀"; return }
    $fname = Split-Path $FilePath -Leaf
    $line = Get-Content $sumFile | Where-Object { $_ -match "  $([regex]::Escape($fname))$" } | Select-Object -First 1
    if (-not $line) { Write-Warn "$fname 의 sha256 항목 없음 — 건너뜀"; return }
    $expected = ($line -split '\s+')[0].ToLower()
    $actual = (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) {
        Write-Err "$fname 무결성 검증 실패`n  expected: $expected`n  actual:   $actual"
    }
    Write-Ok "  무결성 OK: $fname"
}

function Wait-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 30)
    for ($i = 1; $i -le $TimeoutSec; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

# ────────────────────────────────────────────────────────────────────────────
# Mode: -Uninstall
# ────────────────────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step "perf-allinone 제거"
    Write-Log "컨테이너 중지/제거..."
    docker stop $ContainerName 2>$null | Out-Null
    docker rm   $ContainerName 2>$null | Out-Null
    docker rmi "dscore-qa-perf:allinone-$(Get-ArchSuffix)" 2>$null | Out-Null

    $ans = Read-Host "perf-data 볼륨(시나리오/결과)도 삭제할까요? [y/N]"
    if ($ans -eq 'y' -or $ans -eq 'Y') {
        docker volume rm $PerfDataVolume 2>$null | Out-Null
        Write-Ok "  perf-data 볼륨 제거"
    }

    $ans = Read-Host "Ollama 와 모델(~\.ollama)도 제거할까요? [y/N]"
    if ($ans -eq 'y' -or $ans -eq 'Y') {
        if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
            Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
            if (Test-Path $NssmExe) {
                & $NssmExe remove $ServiceName confirm | Out-Null
            }
        }
        Remove-Item -Recurse -Force $InstallRoot -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force "$env:UserProfile\.ollama" -ErrorAction SilentlyContinue
        @('OLLAMA_HOST','OLLAMA_NOPRUNE','OLLAMA_KEEP_ALIVE','OLLAMA_NO_CLOUD') | ForEach-Object {
            [Environment]::SetEnvironmentVariable($_, $null, 'Machine')
        }
        Write-Ok "  Ollama / 모델 제거 완료"
    }

    Write-Ok "제거 완료"
    exit 0
}

# ────────────────────────────────────────────────────────────────────────────
# Mode: -Check
# ────────────────────────────────────────────────────────────────────────────
if ($Check) {
    Write-Step "perf-allinone 상태 점검"

    Write-Host ("{0,-30} " -f "Docker Desktop:") -NoNewline
    try { docker info 2>$null | Out-Null; Write-Host "가동 중" -ForegroundColor Green }
    catch { Write-Host "미가동" -ForegroundColor Red }

    Write-Host ("{0,-30} " -f "Ollama 설치:") -NoNewline
    if (Test-Path $OllamaExe) { Write-Host "설치됨" -ForegroundColor Green }
    else { Write-Host "미설치" -ForegroundColor Red }

    Write-Host ("{0,-30} " -f "Ollama 서비스:") -NoNewline
    $svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
    if ($svc) { Write-Host $svc.Status -ForegroundColor (@{Running='Green';Stopped='Red'}[$svc.Status]) }
    else { Write-Host "없음" -ForegroundColor Red }

    Write-Host ("{0,-30} " -f "Ollama 응답 (11434):") -NoNewline
    try {
        $r = Invoke-WebRequest "http://localhost:11434/api/tags" -TimeoutSec 3 -UseBasicParsing
        Write-Host "OK" -ForegroundColor Green
        $models = ($r.Content | ConvertFrom-Json).models | ForEach-Object { $_.name }
        Write-Log "  모델: $($models -join ', ')"
    } catch { Write-Host "응답 없음" -ForegroundColor Red }

    Write-Host ("{0,-30} " -f "perf-allinone 컨테이너:") -NoNewline
    $running = docker ps --format '{{.Names}}' 2>$null
    $all = docker ps -a --format '{{.Names}}' 2>$null
    if ($running -contains $ContainerName) { Write-Host "가동 중" -ForegroundColor Green }
    elseif ($all -contains $ContainerName) { Write-Host "중지됨" -ForegroundColor Yellow }
    else { Write-Host "없음" -ForegroundColor Red }

    Write-Host ("{0,-30} " -f "GUI (http://localhost:18090):") -NoNewline
    try { Invoke-WebRequest "http://localhost:18090" -TimeoutSec 3 -UseBasicParsing | Out-Null; Write-Host "OK" -ForegroundColor Green }
    catch { Write-Host "응답 없음" -ForegroundColor Red }

    exit 0
}

# ────────────────────────────────────────────────────────────────────────────
# Mode: install (기본)
# ────────────────────────────────────────────────────────────────────────────
Clear-Host
@"
╔════════════════════════════════════════════════════════════════╗
║  perf-allinone — JMeter Performance All-in-One                 ║
║  🪟 Windows 폐쇄망 자동 설치 스크립트                            ║
╚════════════════════════════════════════════════════════════════╝
"@ | Write-Host -ForegroundColor Cyan

# ────────────────────────────────────────────────────────────────────────────
# 1. 사전 점검
# ────────────────────────────────────────────────────────────────────────────
Write-Step "1/8 사전 점검"

if (-not (Test-Admin)) {
    Write-Err "관리자 권한 필요. PowerShell 우클릭 → '관리자 권한으로 실행' 후 재시도."
}
Write-Ok "관리자 권한 OK"

if (-not (Test-Path $AssetsDir)) { Write-Err "assets/ 디렉토리 없음: $AssetsDir" }
$arch = Get-ArchSuffix
Write-Log "Windows 아키텍처: $arch / 사용자: $env:USERNAME"

# Docker Desktop
try { docker info 2>$null | Out-Null; Write-Ok "Docker Desktop OK ($((docker --version) -replace '.*version\s+([^\s,]+).*','$1'))" }
catch {
    Write-Warn "Docker Desktop 미가동 — 기동 시도..."
    $dockerExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Start-Process $dockerExe
        Write-Log "기동 대기 (최대 90초)..."
        for ($i = 1; $i -le 90; $i++) {
            try { docker info 2>$null | Out-Null; break } catch { Start-Sleep 1 }
        }
        try { docker info 2>$null | Out-Null; Write-Ok "Docker Desktop OK" }
        catch { Write-Err "Docker Desktop 기동 실패 — 수동으로 실행 후 재시도" }
    } else {
        Write-Err "Docker Desktop 미설치. https://www.docker.com/products/docker-desktop/ 에서 설치하세요."
    }
}

# 디스크 여유
$freeGB = [math]::Round(((Get-PSDrive C).Free / 1GB), 1)
if ($freeGB -lt 15) { Write-Warn "C: 디스크 여유 ${freeGB}GB — 15GB 이상 권장" }

# ────────────────────────────────────────────────────────────────────────────
# 2. 자산 무결성 검증
# ────────────────────────────────────────────────────────────────────────────
Write-Step "2/8 자산 무결성 검증"

$IMAGE_TAR = Find-ImageTar
Write-Ok "이미지: $(Split-Path $IMAGE_TAR -Leaf) ($([math]::Round((Get-Item $IMAGE_TAR).Length/1MB,1)) MB)"
Verify-Sha256 $IMAGE_TAR

$OLLAMA_ZIP = Join-Path $AssetsDir "ollama-windows-amd64.zip"
if (-not (Test-Path $OLLAMA_ZIP)) { Write-Err "Ollama ZIP 없음: $OLLAMA_ZIP" }
Write-Ok "Ollama 인스톨러: ollama-windows-amd64.zip ($([math]::Round((Get-Item $OLLAMA_ZIP).Length/1MB,1)) MB)"
Verify-Sha256 $OLLAMA_ZIP

$NSSM_ZIP = Join-Path $AssetsDir "nssm-2.24.zip"
if (-not (Test-Path $NSSM_ZIP)) { Write-Err "NSSM ZIP 없음: $NSSM_ZIP" }
Write-Ok "NSSM: nssm-2.24.zip"
Verify-Sha256 $NSSM_ZIP

$MODEL_TGZ = Find-ModelArchive
if ($MODEL_TGZ) {
    Write-Ok "모델 archive: $(Split-Path $MODEL_TGZ -Leaf) ($([math]::Round((Get-Item $MODEL_TGZ).Length/1MB,1)) MB)"
    Verify-Sha256 $MODEL_TGZ
} else {
    Write-Warn "모델 archive 없음 — Ollama 설치 후 호스트에서 'ollama pull' 직접 필요"
}

# ────────────────────────────────────────────────────────────────────────────
# 3. Ollama 설치 (standalone ZIP)
# ────────────────────────────────────────────────────────────────────────────
Write-Step "3/8 Ollama standalone ZIP 압축 해제"

if (Test-Path $OllamaExe) {
    Write-Ok "Ollama 이미 설치됨: $OllamaExe — 건너뜀"
} else {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    Expand-Archive -Path $OLLAMA_ZIP -DestinationPath $InstallRoot -Force
    Write-Ok "압축 해제: $OLLAMA_ZIP → $InstallRoot"
}

# NSSM
if (-not (Test-Path $NssmExe)) {
    New-Item -ItemType Directory -Force -Path $NssmRoot | Out-Null
    Expand-Archive -Path $NSSM_ZIP -DestinationPath $NssmRoot -Force
    Write-Ok "NSSM 설치: $NssmRoot"
} else {
    Write-Ok "NSSM 이미 설치됨"
}

# ────────────────────────────────────────────────────────────────────────────
# 4. PATH + Ollama 환경변수 등록 (시스템)
# ────────────────────────────────────────────────────────────────────────────
Write-Step "4/8 환경변수 등록 (시스템 영구)"

$path = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($path -notlike "*$InstallRoot*") {
    [Environment]::SetEnvironmentVariable("Path", "$path;$InstallRoot", "Machine")
    Write-Ok "PATH 에 $InstallRoot 추가"
} else {
    Write-Ok "PATH 에 이미 존재"
}

[Environment]::SetEnvironmentVariable("OLLAMA_HOST",       $OllamaBindHost, "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NOPRUNE",    "1",             "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "24h",           "Machine")
[Environment]::SetEnvironmentVariable("OLLAMA_NO_CLOUD",   "1",             "Machine")
Write-Ok "OLLAMA_HOST=$OllamaBindHost OLLAMA_NOPRUNE=1 OLLAMA_KEEP_ALIVE=24h OLLAMA_NO_CLOUD=1"

# ────────────────────────────────────────────────────────────────────────────
# 5. NSSM 으로 Ollama 서비스 등록 + 시작
# ────────────────────────────────────────────────────────────────────────────
Write-Step "5/8 Ollama 서비스 등록 + 시작"

$svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Log "기존 $ServiceName 서비스 정지/제거..."
    Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
    & $NssmExe remove $ServiceName confirm | Out-Null
}

& $NssmExe install $ServiceName $OllamaExe "serve" | Out-Null
& $NssmExe set $ServiceName AppEnvironmentExtra `
    "OLLAMA_HOST=$OllamaBindHost" `
    "OLLAMA_NOPRUNE=1" `
    "OLLAMA_KEEP_ALIVE=24h" `
    "OLLAMA_NO_CLOUD=1" | Out-Null
& $NssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmExe set $ServiceName AppStdout (Join-Path $InstallRoot "ollama-stdout.log") | Out-Null
& $NssmExe set $ServiceName AppStderr (Join-Path $InstallRoot "ollama-stderr.log") | Out-Null
Write-Ok "$ServiceName 서비스 등록"

Start-Service $ServiceName
Write-Log "Ollama 응답 대기 (최대 30초)..."
if (Wait-HttpOk "http://localhost:11434/api/tags" 30) {
    Write-Ok "Ollama 응답 OK (http://localhost:11434)"
} else {
    Write-Err "Ollama 응답 없음 — 'Get-Service $ServiceName' 및 $InstallRoot\ollama-stderr.log 확인"
}

# ────────────────────────────────────────────────────────────────────────────
# 6. 모델 archive 복원
# ────────────────────────────────────────────────────────────────────────────
Write-Step "6/8 Ollama 모델 복원"

if ($MODEL_TGZ) {
    $ollamaHome = Join-Path $env:UserProfile ".ollama"
    New-Item -ItemType Directory -Force -Path $ollamaHome | Out-Null
    Write-Log "archive 풀기 (tar 사용)..."
    # Windows 10 1803+ 의 내장 tar 사용
    & tar -xzf "$MODEL_TGZ" -C "$ollamaHome"
    if ($LASTEXITCODE -ne 0) { Write-Err "tar 압축 해제 실패" }

    # 서비스 재시작 (모델 인식)
    Restart-Service $ServiceName
    if (-not (Wait-HttpOk "http://localhost:11434/api/tags" 30)) {
        Write-Warn "재시작 후 응답 없음"
    } else {
        try {
            $r = Invoke-WebRequest "http://localhost:11434/api/tags" -UseBasicParsing
            $models = ($r.Content | ConvertFrom-Json).models | ForEach-Object { $_.name }
            if ($models) { Write-Ok "모델 인식: $($models -join ', ')" }
            else { Write-Warn "복원 후에도 모델 목록이 비어있음 — 매니페스트 경로 확인 필요" }
        } catch { Write-Warn "모델 목록 조회 실패" }
    }
} else {
    Write-Log "(모델 archive 미동봉 — 호스트에서 'ollama pull <model>' 실행 필요)"
}

# ────────────────────────────────────────────────────────────────────────────
# 7. JMeter 컨테이너 로드 + 기동
# ────────────────────────────────────────────────────────────────────────────
Write-Step "7/8 JMeter 컨테이너 로드 + 기동"

Write-Log "이미지 로드: $(Split-Path $IMAGE_TAR -Leaf)..."
$loadOutput = docker load -i $IMAGE_TAR 2>&1
$loadedTag = ($loadOutput | Select-String -Pattern "Loaded image: (.+)$" | Select-Object -First 1).Matches.Groups[1].Value
if (-not $loadedTag) { $loadedTag = "dscore-qa-perf:allinone-$arch" }
Write-Ok "이미지 로드: $loadedTag"

# 기존 컨테이너 정리
$existing = docker ps -a --format '{{.Names}}'
if ($existing -contains $ContainerName) {
    Write-Log "기존 컨테이너 제거: $ContainerName"
    docker stop $ContainerName 2>$null | Out-Null
    docker rm   $ContainerName 2>$null | Out-Null
}

Write-Log "컨테이너 기동..."
docker run -d `
    --name $ContainerName `
    -p 18090:18090 `
    -v "${PerfDataVolume}:/data" `
    -e JMETER_GUI_AUTOSTART=true `
    -e TZ=Asia/Seoul `
    --add-host host.docker.internal:host-gateway `
    --restart unless-stopped `
    $loadedTag | Out-Null
Write-Ok "컨테이너 기동: $ContainerName"

# ────────────────────────────────────────────────────────────────────────────
# 8. 도달성 검증 + 브라우저 열기
# ────────────────────────────────────────────────────────────────────────────
Write-Step "8/8 도달성 검증"

Write-Log "컨테이너 → 호스트 Ollama 도달..."
$reachable = $false
for ($i = 1; $i -le 30; $i++) {
    $r = docker exec $ContainerName curl -sf --max-time 3 http://host.docker.internal:11434/api/tags 2>$null
    if ($LASTEXITCODE -eq 0) { $reachable = $true; break }
    Start-Sleep 1
}
if ($reachable) { Write-Ok "컨테이너 ↔ 호스트 Ollama OK" }
else { Write-Warn "도달 검증 timeout — JMeter는 동작하지만 jmeter.ai 호출은 실패할 수 있음" }

Write-Log "GUI(noVNC) 응답 대기 (최대 60초)..."
if (Wait-HttpOk "http://localhost:18090" 60) {
    Write-Ok "GUI OK (http://localhost:18090)"
} else {
    Write-Warn "GUI 응답 없음 — 'docker logs $ContainerName' 확인"
}

# ────────────────────────────────────────────────────────────────────────────
# 완료
# ────────────────────────────────────────────────────────────────────────────
@"

╔════════════════════════════════════════════════════════════════╗
║  ✓ 설치 완료                                                    ║
╚════════════════════════════════════════════════════════════════╝
"@ | Write-Host -ForegroundColor Green

@"

  🌐 GUI:    http://localhost:18090
  ⚙️  Ollama: http://localhost:11434  (호스트, GPU 가속 활성)
  📦 컨테이너: $ContainerName
  💾 데이터:  Docker volume '$PerfDataVolume' (/data)

  📚 사용법:
     - 평상시(GUI):  브라우저에서 http://localhost:18090
     - 정식 시험(CLI):
         docker exec $ContainerName jmeter -n -t /data/jmeter/scenarios/<file>.jmx ``
           -l /data/jmeter/results/<file>-`$ts.jtl ``
           -e -o /data/jmeter/reports/<file>-`$ts/

  🛠  관리 명령:
     .\install-windows.ps1 -Check        # 상태 점검
     .\install-windows.ps1 -Uninstall    # 제거

  📖 자세한 사용법:  README-windows.md / README.md (5장 JMeter 심화)

"@ | Write-Host

if (-not $SkipBrowser) {
    Write-Log "브라우저 자동 열기..."
    Start-Sleep 1
    Start-Process "http://localhost:18090"
}
