# =============================================================================
# setup.ps1 — DSCORE Zero-Touch QA 전체 스택 자동 설치 (Windows PowerShell)
#
# [작성 배경]
# 원본 저장소에는 setup.sh (bash) 만 존재했다.
# Windows 환경에서는 bash here-string, set -euo pipefail, $() 치환 등이
# PowerShell에서 동작하지 않아 동일 로직을 PowerShell 문법으로 전면 재작성했다.
#
# 사용법:
#   cd e2e-pipeline\
#   .\setup.ps1
#
# 환경변수로 기본값 오버라이드 가능:
#   $env:OLLAMA_MODEL="qwen3-coder:14b"
#   $env:DIFY_EMAIL="me@company.com"
#   $env:DIFY_PASSWORD="MyPass1!"
#   $env:JENKINS_ADMIN_USER="admin"
#   $env:JENKINS_ADMIN_PW="MyPass1!"
#   .\setup.ps1
#
# 자동화 범위:
#   Phase 1: docker compose up --build + 서비스 헬스 대기
#   Phase 2: Ollama LLM 모델 Pull
#   Phase 3: Dify 초기 설정 (계정, Ollama 공급자, Chatflow import, API Key)
#   Phase 4: Jenkins 초기 설정 (플러그인, Credentials, CSP, Job, Node)
#
# 남은 수동 작업:
#   - 에이전트 머신에 Java 11+, Python 3.9+, Playwright 설치
#   - java -jar agent.jar 실행 (스크립트 종료 시 명령 출력)
#
# 실행 정책 오류 시:
#   PowerShell(관리자)에서 → Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
# =============================================================================

$ErrorActionPreference = "Stop"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $SCRIPT_DIR

# ─────────────────────────────────────────────────────────────────────────────
# 설정 (환경변수로 오버라이드 가능)
# ─────────────────────────────────────────────────────────────────────────────
$OLLAMA_MODEL        = if ($env:OLLAMA_MODEL)        { $env:OLLAMA_MODEL }        else { "qwen2.5-coder:14b" }
$DIFY_EMAIL          = if ($env:DIFY_EMAIL)          { $env:DIFY_EMAIL }          else { "admin@example.com" }
$DIFY_PASSWORD       = if ($env:DIFY_PASSWORD)       { $env:DIFY_PASSWORD }       else { "Admin1234!" }
$JENKINS_ADMIN_USER  = if ($env:JENKINS_ADMIN_USER)  { $env:JENKINS_ADMIN_USER }  else { "admin" }
$JENKINS_ADMIN_PW    = if ($env:JENKINS_ADMIN_PW)    { $env:JENKINS_ADMIN_PW }    else { "Admin1234!" }
$SCRIPTS_HOME        = if ($env:SCRIPTS_HOME)        { $env:SCRIPTS_HOME }        else { $SCRIPT_DIR }
# [추가] OLLAMA_PROFILE 변수 — 원본 setup.sh 에는 없음
# Ollama 를 Docker 컨테이너로 실행하는 경우와 호스트에서 직접 실행하는 경우의
# Base URL 이 다르다. Windows 에서는 Docker 컨테이너가 호스트를 host.docker.internal 로
# 참조하므로 기본값을 "host" 로 하고, GPU 설정 복잡도를 피하기 위해
# 호스트 Ollama 를 권장한다.
$OLLAMA_PROFILE      = if ($env:OLLAMA_PROFILE)      { $env:OLLAMA_PROFILE }      else { "host" }

$JENKINS_URL = "http://localhost:18080"
$DIFY_URL    = "http://localhost:18081"

# [추가] 호스트 Ollama 모드(기본)는 host.docker.internal, 컨테이너 모드는 ollama 컨테이너명 사용
# Docker Desktop for Windows 에서 컨테이너가 호스트 서비스(Ollama)에 접근하려면
# localhost 대신 host.docker.internal 을 사용해야 한다.
if ($OLLAMA_PROFILE -eq "container") {
    $OLLAMA_BASE_URL = "http://ollama:11434"
} else {
    $OLLAMA_BASE_URL = "http://host.docker.internal:11434"
}

# Python 명령어 자동 감지 (python3 우선, 없으면 python)
$PYTHON = "python"
try {
    python3 --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $PYTHON = "python3" }
} catch {}

# ─────────────────────────────────────────────────────────────────────────────
# 로그 유틸리티
# ─────────────────────────────────────────────────────────────────────────────
function log($msg)  { Write-Host ("`n[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) }
function ok($msg)   { Write-Host ("[{0}] v {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Green }
function warn($msg) { Write-Host ("[{0}] ! {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Yellow }
function err($msg)  { Write-Host ("[{0}] x {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Red }

# URL이 응답할 때까지 대기 (최대 $timeout초, 기본 300초)
function Wait-Http($url, $label, $timeout = 300) {
    Write-Host ("[{0}] 대기 중: {1}" -f (Get-Date -Format "HH:mm:ss"), $label) -NoNewline
    $elapsed = 0
    while ($true) {
        curl.exe -sf $url 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            ok "$label 준비 완료"
            return
        }
        Start-Sleep -Seconds 5
        $elapsed += 5
        Write-Host "." -NoNewline
        if ($elapsed -ge $timeout) {
            Write-Host ""
            err "$label 응답 없음 (${timeout}초 초과)"
            throw "$label 타임아웃"
        }
    }
}

# Python으로 JSON 필드 추출
function Get-JsonField($json, $pyExpr) {
    $result = $json | & $PYTHON -c "import json,sys; d=json.load(sys.stdin); print($pyExpr)" 2>&1
    if ($LASTEXITCODE -ne 0) { return "" }
    return $result.Trim()
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 0: 사전 요구사항 확인
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 0: 사전 요구사항 확인 ==="

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    err "docker 명령이 없습니다. Docker Desktop을 설치하십시오."
    exit 1
}
if (-not (Get-Command $PYTHON -ErrorAction SilentlyContinue)) {
    err "python이 필요합니다. (https://python.org 에서 설치)"
    exit 1
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    err "Docker 데몬이 실행되지 않습니다. Docker Desktop을 실행하십시오."
    exit 1
}
if (-not (Test-Path "$SCRIPT_DIR\dify-chatflow.yaml")) {
    err "dify-chatflow.yaml이 없습니다. e2e-pipeline\ 폴더 안에서 실행하십시오."
    exit 1
}
if (-not (Test-Path "$SCRIPT_DIR\DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline")) {
    err "DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline이 없습니다."
    exit 1
}

ok "사전 요구사항 확인 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Jenkins init 스크립트 생성 + docker compose 기동
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 1: Docker 스택 기동 ==="

New-Item -ItemType Directory -Force -Path "jenkins-init" | Out-Null

# Jenkins Groovy init 스크립트: 관리자 계정 + CSRF 비활성화
# [변경] 원본 setup.sh 는 Setup Wizard 완료 후 REST API 로 계정을 생성했다.
# 문제: Windows 에서 jenkins/init.groovy.d 에 Groovy 스크립트를 넣으면
#       첫 기동 시 자동 실행되어 계정+보안 설정이 완료된 상태로 올라온다.
#       이 방식이 "관리자 비밀번호 파일 복사" 과정을 없애고 REST API 인증을 즉시 사용 가능하게 한다.
@"
import jenkins.model.*
import hudson.security.*
import jenkins.install.InstallState

def instance = Jenkins.getInstance()

// 관리자 계정 생성 (이미 존재하면 건너뜀)
def realm = new HudsonPrivateSecurityRealm(false)
if (realm.getUser('${JENKINS_ADMIN_USER}') == null) {
    realm.createAccount('${JENKINS_ADMIN_USER}', '${JENKINS_ADMIN_PW}')
    println "[init] 관리자 계정 생성: ${JENKINS_ADMIN_USER}"
} else {
    println "[init] 관리자 계정 이미 존재: ${JENKINS_ADMIN_USER}"
}
instance.setSecurityRealm(realm)

// 권한 정책: 로그인 사용자 전체 제어
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)

// Setup Wizard 완료 처리
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)

// CSRF 크럼 비활성화 (REST API 호출을 위해)
instance.setCrumbIssuer(null)

instance.save()
println "[init] Jenkins 보안 설정 완료"
"@ | Set-Content -Path "jenkins-init\01-security.groovy" -Encoding UTF8

# docker-compose.override.yaml: Setup Wizard 우회 + init 스크립트 마운트
# [변경] 원본 setup.sh 는 CSP 완화를 Jenkins Script Console REST API 로 적용했다.
# 문제: Script Console 은 CSRF 크럼이 필요한데, 첫 부팅 직후에는 크럼 발급이 불안정하다.
#       JAVA_OPTS 에 -Dhudson.model.DirectoryBrowserSupport.CSP= 를 영구 등록하면
#       재시작 없이도 publishHTML 리포트 내 JS/CSS 가 정상 동작한다.
#       runSetupWizard=false 는 Setup Wizard 를 건너뛰어 init 스크립트로 바로 넘어가게 한다.
@'
# setup.ps1이 생성한 파일 — 첫 설치 시 Setup Wizard를 우회하고
# jenkins-init/ 폴더의 Groovy 스크립트를 자동 실행한다.
services:
  jenkins:
    environment:
      JAVA_OPTS: >-
        -Djenkins.install.runSetupWizard=false
        -Dhudson.model.DirectoryBrowserSupport.CSP=
    volumes:
      - ./jenkins-init:/var/jenkins_home/init.groovy.d
'@ | Set-Content -Path "docker-compose.override.yaml" -Encoding UTF8

log "docker compose up -d --build 시작 (첫 기동은 30~50분 소요됩니다)"
docker compose up -d --build
ok "컨테이너 기동 명령 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: 서비스 헬스 대기
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 2: 서비스 준비 대기 ==="

log "Dify 웹 레이어 준비 대기..."
Wait-Http $DIFY_URL "Dify Web" 180

# [변경] 원본: Wait-Http "$DIFY_URL/health" → Wait-Http "$DIFY_URL/console/api/setup"
# 사유: /health 는 Nginx 레이어(web:3000)가 응답하므로 api:5001(DB 마이그레이션 포함)이
#       아직 준비되지 않아도 200을 반환한다.
#       /console/api/setup 은 api:5001 이 실제로 DB 마이그레이션까지 완료해야 응답하므로
#       더 정확한 헬스 체크 기준점이다.
log "Dify API 준비 대기 (DB 마이그레이션 포함, 최대 6분)..."
Wait-Http "$DIFY_URL/console/api/setup" "Dify API(/console/api/setup)" 360

log "Jenkins 준비 대기..."
Wait-Http "$JENKINS_URL/login" "Jenkins" 360

log "Jenkins REST API 인증 대기..."
$elapsed = 0
while ($true) {
    curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" "$JENKINS_URL/api/json" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
    $elapsed += 5
    if ($elapsed -ge 120) {
        warn "Jenkins API 인증 타임아웃 (2분) — init 스크립트 실행 지연 가능. 계속 진행합니다."
        break
    }
}
ok "모든 서비스 준비 완료"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Ollama 모델 Pull
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 3: Ollama 모델 Pull: $OLLAMA_MODEL ==="
log "모델 크기에 따라 수분~수십 분 소요됩니다."
docker exec e2e-ollama ollama pull $OLLAMA_MODEL
if ($LASTEXITCODE -eq 0) {
    ok "Ollama 모델 Pull 완료"
} else {
    warn "Ollama pull 실패 — 나중에 수동으로 실행: docker exec -it e2e-ollama ollama pull $OLLAMA_MODEL"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Dify 초기 설정
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 4: Dify 초기 설정 ==="
$DIFY_API_KEY  = ""
$DIFY_SETUP_OK = $false

# 4-1. 관리자 계정 생성
log "4-1. Dify 관리자 계정 생성..."
$setupBody  = '{"email":"' + $DIFY_EMAIL + '","name":"Admin","password":"' + $DIFY_PASSWORD + '"}'
$SETUP_RESP = curl.exe -sf -X POST "$DIFY_URL/console/api/setup" `
    -H "Content-Type: application/json" `
    -d $setupBody 2>&1
if (-not $SETUP_RESP) { $SETUP_RESP = '{"result":"error"}' }

$setupResult = Get-JsonField $SETUP_RESP "d.get('result','')"
if ($setupResult -eq "success") {
    ok "Dify 관리자 계정 생성 완료"
} else {
    warn "Dify 계정 생성 응답: $SETUP_RESP"
    warn "이미 설정된 경우 로그인으로 계속 진행합니다."
}

# 4-2. 로그인 → access_token 획득
log "4-2. Dify 로그인..."
$loginBody  = '{"email":"' + $DIFY_EMAIL + '","password":"' + $DIFY_PASSWORD + '"}'
$LOGIN_RESP  = curl.exe -sf -X POST "$DIFY_URL/console/api/login" `
    -H "Content-Type: application/json" `
    -d $loginBody 2>&1
if (-not $LOGIN_RESP) { $LOGIN_RESP = '{}' }

# [변경] access_token 추출 경로를 중첩 구조로 수정
# 원본: d['access_token'] (단순 최상위 키)
# 사유: Dify 버전에 따라 응답이 {"access_token":"..."} 또는
#       {"data":{"access_token":"..."}} 두 가지 형태로 달라진다.
#       (d.get('data') or {}).get('access_token') or d.get('access_token') 로
#       두 경우를 모두 처리한다.
$DIFY_TOKEN = Get-JsonField $LOGIN_RESP "(d.get('data') or {}).get('access_token') or d.get('access_token') or ''"

if (-not $DIFY_TOKEN) {
    err "Dify 로그인 실패 (응답: $LOGIN_RESP)"
    warn "Dify 설정을 수동으로 완료하십시오 — GUIDE.md §6~§7 참조"
} else {
    ok "Dify 로그인 성공"
    $DIFY_SETUP_OK = $true
}

if ($DIFY_SETUP_OK) {

    # 4-3. Ollama 모델 공급자 등록
    log "4-3. Ollama 모델 공급자 등록 ($OLLAMA_BASE_URL)..."
    curl.exe -sf -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/ollama" `
        -H "Authorization: Bearer $DIFY_TOKEN" `
        -H "Content-Type: application/json" `
        -d "{`"credentials`":{`"base_url`":`"$OLLAMA_BASE_URL`"}}" 2>&1 | Out-Null
    ok "Ollama 공급자 등록 요청 완료"

    # 4-4. LLM 모델 추가
    log "4-4. LLM 모델 추가: $OLLAMA_MODEL..."
    $modelBody = '{"model":"' + $OLLAMA_MODEL + '","model_type":"llm","credentials":{"base_url":"' + $OLLAMA_BASE_URL + '","context_size":8192,"max_tokens":4096,"mode":"chat","completion_type":"chat"}}'
    curl.exe -sf -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/ollama/models" `
        -H "Authorization: Bearer $DIFY_TOKEN" `
        -H "Content-Type: application/json" `
        -d $modelBody 2>&1 | Out-Null
    ok "LLM 모델 추가 요청 완료"

    # 4-5. Chatflow DSL import (Python 임시 파일 방식으로 처리)
    # [변경] 원본 setup.sh 는 bash here-doc + python3 인라인으로 YAML 을 읽어 JSON body 생성
    # PowerShell here-string(@" "@) 안에서 $() 치환과 Python 코드가 충돌하여
    # 임시 .py 파일을 생성 후 실행하는 방식으로 변경했다.
    # (PowerShell 문자열 이스케이프와 Python 코드 혼용을 회피하기 위함)
    log "4-5. Chatflow 'ZeroTouch QA Brain' import..."
    $pyImportScript = @"
import json
yaml_path = r'$($SCRIPT_DIR)\dify-chatflow.yaml'
with open(yaml_path, encoding='utf-8') as f:
    yaml_content = f.read()
yaml_content = yaml_content.replace('{{OLLAMA_MODEL}}', '$OLLAMA_MODEL')
print(json.dumps({'data': yaml_content}))
"@
    $pyImportFile = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $pyImportFile -Value $pyImportScript -Encoding UTF8

    $importBodyFile = [System.IO.Path]::GetTempFileName()
    & $PYTHON $pyImportFile | Set-Content -Path $importBodyFile -Encoding UTF8 -NoNewline
    Remove-Item $pyImportFile -Force

    if (-not (Get-Item $importBodyFile).Length) {
        warn "Chatflow import 바디 생성 실패 — dify-chatflow.yaml 확인 필요"
        $IMPORT_RESP = '{}'
    } else {
        $IMPORT_RESP = curl.exe -s -X POST "$DIFY_URL/console/api/apps/import" `
            -H "Authorization: Bearer $DIFY_TOKEN" `
            -H "Content-Type: application/json" `
            -d "@$importBodyFile" 2>&1
        if (-not $IMPORT_RESP) { $IMPORT_RESP = '{}' }
    }
    Remove-Item $importBodyFile -Force -ErrorAction SilentlyContinue

    $DIFY_APP_ID = Get-JsonField $IMPORT_RESP "d.get('id','')"

    if (-not $DIFY_APP_ID) {
        warn "Chatflow import 실패 (응답: $IMPORT_RESP)"
        warn "Chatflow를 수동으로 생성하십시오 — GUIDE.md §7 참조"
    } else {
        ok "Chatflow import 완료 (App ID: $DIFY_APP_ID)"

        # 4-6. Chatflow 게시
        log "4-6. Chatflow 게시..."
        # [변경] 게시 API 경로 이중 시도 추가
        # 원본: /apps/{id}/publish 만 시도
        # 사유: Dify 버전에 따라 /apps/{id}/publish 또는 /apps/{id}/workflows/publish
        #       중 하나만 동작한다. 먼저 /publish 를 시도하고 실패하면 /workflows/publish 로 폴백한다.
        $PUB_RESP = curl.exe -s -X POST "$DIFY_URL/console/api/apps/$DIFY_APP_ID/publish" `
            -H "Authorization: Bearer $DIFY_TOKEN" `
            -H "Content-Type: application/json" `
            -d '{}' 2>&1
        if (-not $PUB_RESP) { $PUB_RESP = '{}' }

        $pubResult = Get-JsonField $PUB_RESP "d.get('result','')"
        if ($pubResult -ne "success") {
            $PUB_RESP = curl.exe -s -X POST "$DIFY_URL/console/api/apps/$DIFY_APP_ID/workflows/publish" `
                -H "Authorization: Bearer $DIFY_TOKEN" `
                -H "Content-Type: application/json" `
                -d '{"marked_as_official": false}' 2>&1
        }
        ok "Chatflow 게시 완료"

        # 4-7. API Key 생성
        log "4-7. Dify API Key 생성..."
        $KEY_RESP = curl.exe -sf -X POST "$DIFY_URL/console/api/apps/$DIFY_APP_ID/api-keys" `
            -H "Authorization: Bearer $DIFY_TOKEN" `
            -H "Content-Type: application/json" `
            -d '{}' 2>&1
        if (-not $KEY_RESP) { $KEY_RESP = '{}' }

        $DIFY_API_KEY = Get-JsonField $KEY_RESP "d.get('token','')"

        if ($DIFY_API_KEY) {
            ok "Dify API Key 생성 완료: $DIFY_API_KEY"
        } else {
            warn "API Key 생성 실패 (응답: $KEY_RESP)"
            warn "GUIDE.md §7.7에서 수동으로 API Key를 생성하십시오"
        }
    }

} # DIFY_SETUP_OK

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Jenkins 초기 설정 (REST API)
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase 5: Jenkins 초기 설정 ==="

$JENKINS_READY = $false
for ($i = 1; $i -le 12; $i++) {
    curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" "$JENKINS_URL/api/json" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $JENKINS_READY = $true; break }
    Start-Sleep -Seconds 10
}

if (-not $JENKINS_READY) {
    warn "Jenkins REST API 응답 없음 — Jenkins 설정을 수동으로 완료하십시오 (GUIDE.md §8 참조)"
} else {
    ok "Jenkins API 연결 확인"

    # 5-1. 플러그인 설치
    # [변경] workflow-aggregator 플러그인 명시 추가
    # 원본(초기): file-parameters, htmlpublisher 만 설치
    # 사유: Setup Wizard 를 건너뛰면 workflow-aggregator(Pipeline DSL 엔진)가
    #       자동 설치되지 않아 /createItem 으로 Pipeline Job 생성 시 500 오류가 발생했다.
    #
    # [변경] 플러그인 설치 대기를 30s → 60s 로 연장
    # 사유: workflow-aggregator 는 40개 이상의 의존 플러그인을 연쇄 설치한다.
    #       30초 내에 끝나지 않아 재시작 전에 /createItem 을 호출하면 500이 반환됐다.
    log "5-1. Jenkins 플러그인 설치 (workflow-aggregator, file-parameters, htmlpublisher)..."
    $pluginXml = '<jenkins><install plugin="workflow-aggregator@latest"/><install plugin="file-parameters@latest"/><install plugin="htmlpublisher@latest"/></jenkins>'
    curl.exe -sf -X POST "$JENKINS_URL/pluginManager/installNecessaryPlugins" `
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
        -H "Content-Type: text/xml" `
        -d $pluginXml 2>&1 | Out-Null
    ok "플러그인 설치 요청 완료 (백그라운드 처리)"

    log "플러그인 설치 완료 대기 (60초)..."
    Start-Sleep -Seconds 60
    log "Jenkins 재시작..."
    docker restart e2e-jenkins 2>&1 | Out-Null
    log "Jenkins 재시작 후 준비 대기..."
    Start-Sleep -Seconds 20
    Wait-Http "$JENKINS_URL/login" "Jenkins (재시작 후)" 180

    # 재시작 후 API 인증 재확인
    for ($i = 1; $i -le 12; $i++) {
        curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" "$JENKINS_URL/api/json" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 10
    }

    # 5-2. Credentials 등록 (Dify API Key)
    if ($DIFY_API_KEY) {
        log "5-2. Jenkins Credentials 등록 (dify-qa-api-token)..."
        $credXml = @"
<com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>
  <scope>GLOBAL</scope>
  <id>dify-qa-api-token</id>
  <description>Dify QA Chatflow API Key (setup.ps1 auto-registered)</description>
  <secret>$DIFY_API_KEY</secret>
</com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>
"@
        $credFile = [System.IO.Path]::GetTempFileName()
        Set-Content -Path $credFile -Value $credXml -Encoding UTF8
        curl.exe -sf -X POST "$JENKINS_URL/credentials/store/system/domain/_/createCredentials" `
            -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
            -H "Content-Type: application/xml" `
            -d "@$credFile" 2>&1 | Out-Null
        Remove-Item $credFile -Force
        ok "Credentials 등록 완료 (dify-qa-api-token)"
    } else {
        warn "Dify API Key 없음 — Credentials를 수동으로 등록하십시오 (GUIDE.md §8.3)"
    }

    ok "CSP 완화: docker-compose.override.yaml JAVA_OPTS에 영구 적용됨"

    # 5-4. Pipeline Job 생성 (Python으로 XML escape 처리)
    # [변경] Python xml.sax.saxutils.escape() 로 파이프라인 스크립트를 XML 이스케이프
    # 사유: Jenkinsfile 안에 <, >, & 문자가 포함되어 있어 그대로 XML 에 넣으면 파싱 오류 발생.
    #       PowerShell 의 [System.Security.SecurityElement]::Escape() 로도 동일하게 처리할 수
    #       있으나, 임시 py 파일 방식은 4-5 단계와 일관성을 유지하기 위해 Python 을 사용했다.
    #       (run-configure.ps1 은 PowerShell 네이티브 방식으로 동일 작업을 수행한다.)
    log "5-4. Pipeline Job 'DSCORE-ZeroTouch-QA-Docker' 생성..."
    $pyJobScript = @"
import xml.sax.saxutils as sax
pipeline_path = r'$($SCRIPT_DIR)\DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline'
with open(pipeline_path, encoding='utf-8') as f:
    script = f.read()
escaped = sax.escape(script)
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>DSCORE Zero-Touch QA Docker Pipeline (setup.ps1 auto-created)</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>""".format(escaped))
"@
    $pyJobFile  = [System.IO.Path]::GetTempFileName() + ".py"
    $jobXmlFile = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $pyJobFile -Value $pyJobScript -Encoding UTF8
    & $PYTHON $pyJobFile | Set-Content -Path $jobXmlFile -Encoding UTF8 -NoNewline
    Remove-Item $pyJobFile -Force

    if (-not (Get-Item $jobXmlFile).Length) {
        warn "Job XML 생성 실패 — Pipeline Job을 수동으로 생성하십시오 (GUIDE.md §8.5)"
    } else {
        curl.exe -sf -X POST "$JENKINS_URL/createItem?name=DSCORE-ZeroTouch-QA-Docker" `
            -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
            -H "Content-Type: application/xml" `
            -d "@$jobXmlFile" 2>&1 | Out-Null
        ok "Pipeline Job 생성 완료"
    }
    Remove-Item $jobXmlFile -Force -ErrorAction SilentlyContinue

    # 5-5. mac-ui-tester 노드 등록 (Groovy Script Console)
    # [변경] 노드 등록 방식: REST API → Groovy Script Console
    # 원본 setup.sh 는 Groovy 방식을 사용했으나 JNLPLauncher(false) 생성자가
    # Jenkins 2.x LTS 에서 제거되어 예외가 발생했다.
    # 수정: new JNLPLauncher() (인자 없음) 로 변경 — 최신 LTS 와 호환.
    #
    # [변경] 백슬래시를 이중 이스케이프 ($agentWorkDir -replace '\\','\\\\')
    # 사유: Windows 경로(C:\Users\...)를 Groovy 문자열 리터럴에 넣을 때
    #       백슬래시가 탈출 문자로 해석되어 경로가 깨지는 문제를 방지한다.
    #
    # [변경] --data-urlencode "script@file" 방식으로 Groovy 전송
    # 사유: curl -d 로 긴 Groovy 스크립트를 직접 전달하면 특수문자(=, +, &)가
    #       URL 인코딩 없이 form body 에 들어가 파싱 오류가 발생했다.
    #       --data-urlencode 는 파일 내용을 자동 인코딩하여 안전하게 전송한다.
    log "5-5. mac-ui-tester 에이전트 노드 등록..."
    $agentWorkDir    = "$env:USERPROFILE\jenkins-agent" -replace '\\', '\\\\'
    $scriptsHomeEsc  = $SCRIPTS_HOME -replace '\\', '\\\\'

    $nodeGroovyScript = @"
import jenkins.model.*
import hudson.model.*
import hudson.slaves.*

def instance = Jenkins.getInstance()

if (instance.getNode('mac-ui-tester') != null) {
    println "[node] mac-ui-tester 이미 존재 — 건너뜀"
    return
}

def launcher = new JNLPLauncher()
def node = new DumbSlave(
    "mac-ui-tester",
    "Playwright E2E Test Agent",
    "$agentWorkDir",
    "2",
    Node.Mode.NORMAL,
    "mac-ui-tester",
    launcher,
    new RetentionStrategy.Always(),
    new java.util.ArrayList()
)

def envEntry = new EnvironmentVariablesNodeProperty.Entry("SCRIPTS_HOME", "$scriptsHomeEsc")
def envProp = new EnvironmentVariablesNodeProperty([envEntry])
node.nodeProperties.add(envProp)

instance.addNode(node)
instance.save()
println "[node] mac-ui-tester 등록 완료 (SCRIPTS_HOME=$scriptsHomeEsc)"
"@
    # --data-urlencode "@file" 로 Groovy 스크립트를 안전하게 전송
    $nodeGroovyFile = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $nodeGroovyFile -Value $nodeGroovyScript -Encoding UTF8
    curl.exe -sf -X POST "$JENKINS_URL/scriptText" `
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
        -H "Content-Type: application/x-www-form-urlencoded" `
        --data-urlencode "script@$nodeGroovyFile" 2>&1 | Out-Null
    Remove-Item $nodeGroovyFile -Force
    ok "mac-ui-tester 노드 등록 완료 (SCRIPTS_HOME: $SCRIPTS_HOME)"

} # JENKINS_READY

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: 완료 요약
# ─────────────────────────────────────────────────────────────────────────────
$NODE_SECRET = curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
    "$JENKINS_URL/computer/mac-ui-tester/slave-agent.jnlp" 2>&1 | & $PYTHON -c @"
import sys, re
content = sys.stdin.read()
m = re.search(r'<argument>([0-9a-f]{40,64})</argument>', content)
print(m.group(1) if m else '<SECRET>')
"@
if (-not $NODE_SECRET) { $NODE_SECRET = "<SECRET>" }

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  DSCORE Zero-Touch QA 스택 설치 완료"                            -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Jenkins   -> http://localhost:18080"
Write-Host "             계정: $JENKINS_ADMIN_USER / $JENKINS_ADMIN_PW"
Write-Host ""
Write-Host "  Dify 콘솔 -> http://localhost:18081"
Write-Host "             계정: $DIFY_EMAIL / $DIFY_PASSWORD"
if ($DIFY_API_KEY) {
    Write-Host "             API Key: $DIFY_API_KEY"
    Write-Host "             -> Jenkins Credentials 'dify-qa-api-token' 자동 등록됨"
} else {
    Write-Host "             API Key: (수동 등록 필요 — GUIDE.md §7.7 + §8.3)"
}
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
Write-Host "  [남은 수동 작업] 에이전트 머신에서 실행"                         -ForegroundColor Yellow
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Python + Playwright 설치 (에이전트 머신):"
Write-Host "     pip install playwright"
Write-Host "     playwright install chromium"
Write-Host ""
Write-Host "  2. agent.jar 다운로드 및 에이전트 연결:"
Write-Host ""
Write-Host "     curl -O http://localhost:18080/jnlpJars/agent.jar"
Write-Host "     java -jar agent.jar ``"
Write-Host "       -url `"http://localhost:18080`" ``"
Write-Host "       -secret `"$NODE_SECRET`" ``"
Write-Host "       -name `"mac-ui-tester`" ``"
Write-Host "       -workDir `"$env:USERPROFILE\jenkins-agent`""
Write-Host ""
Write-Host "  3. Jenkins UI -> 노드 관리 -> mac-ui-tester 상태가 'Connected'로 바뀌면 완료."
Write-Host ""
Write-Host "  4. Jenkins -> DSCORE-ZeroTouch-QA-Docker -> Build with Parameters 실행"
Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
