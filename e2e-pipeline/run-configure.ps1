# run-configure.ps1 — 컨테이너가 이미 실행 중일 때 Dify + Jenkins 설정만 재수행
#
# [작성 배경]
# setup.ps1 은 docker compose up → 헬스 대기 → Ollama pull → Dify 설정 → Jenkins 설정을
# 순서대로 실행한다. 컨테이너는 이미 올라와 있는데 Dify/Jenkins 설정만 다시 해야 할 때
# (예: API Key 재발급, Chatflow 재import, Jenkins Job 재생성) setup.ps1 전체를 돌리면
# docker compose up 과 Ollama pull 단계에서 불필요한 시간이 소요된다.
# 이 스크립트는 Phase 4(Dify) + Phase 5(Jenkins) 만 실행한다.
#
# [setup.ps1 과의 차이점]
# - docker compose up, Ollama pull 단계 없음
# - 4-5 Chatflow import: Python 임시 파일 방식 대신 PowerShell 네이티브
#     [System.IO.File]::ReadAllText() + [System.Text.Json.JsonSerializer]::Serialize()
#     사용 (Python 없이도 동작하도록 의존성 제거)
# - 5-4 Pipeline Job XML escape: [System.Security.SecurityElement]::Escape() 사용
#     (Python xml.sax.saxutils.escape() 와 동일한 결과, PowerShell 순수 구현)
# - 5-6 Chatflow 게시: /workflows/publish 단일 경로만 시도 (실측 확인된 경로)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $SCRIPT_DIR

$OLLAMA_MODEL       = if ($env:OLLAMA_MODEL)       { $env:OLLAMA_MODEL }       else { "qwen3.5:9b" }
$DIFY_EMAIL         = if ($env:DIFY_EMAIL)         { $env:DIFY_EMAIL }         else { "admin@example.com" }
$DIFY_PASSWORD      = if ($env:DIFY_PASSWORD)      { $env:DIFY_PASSWORD }      else { "Admin1234!" }
$JENKINS_ADMIN_USER = if ($env:JENKINS_ADMIN_USER) { $env:JENKINS_ADMIN_USER } else { "admin" }
$JENKINS_ADMIN_PW   = if ($env:JENKINS_ADMIN_PW)   { $env:JENKINS_ADMIN_PW }   else { "Admin1234!" }
$SCRIPTS_HOME       = if ($env:SCRIPTS_HOME)       { $env:SCRIPTS_HOME }       else { $SCRIPT_DIR }
$OLLAMA_PROFILE     = if ($env:OLLAMA_PROFILE)     { $env:OLLAMA_PROFILE }     else { "host" }

$JENKINS_URL = "http://localhost:18080"
$DIFY_URL    = "http://localhost:18081"

if ($OLLAMA_PROFILE -eq "container") {
    $OLLAMA_BASE_URL = "http://ollama:11434"
} else {
    $OLLAMA_BASE_URL = "http://host.docker.internal:11434"
}

$PYTHON = "python"
try { python3 --version 2>&1 | Out-Null; if ($LASTEXITCODE -eq 0) { $PYTHON = "python3" } } catch {}

function log($msg)  { Write-Host ("`n[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) }
function ok($msg)   { Write-Host ("[{0}] v {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Green }
function warn($msg) { Write-Host ("[{0}] ! {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Yellow }

function Get-JsonField($json, $pyExpr) {
    $r = $json | & $PYTHON -c "import json,sys; d=json.load(sys.stdin); print($pyExpr)" 2>&1
    if ($LASTEXITCODE -ne 0) { return "" }
    return $r.Trim()
}

# ── Phase 4: Dify 초기 설정 ──────────────────────────────────────────────────
log "=== Phase 4: Dify 초기 설정 ==="
$DIFY_API_KEY  = ""
$DIFY_SETUP_OK = $false

log "4-1. Dify 관리자 계정 생성..."
$setupBody = '{"email":"' + $DIFY_EMAIL + '","name":"Admin","password":"' + $DIFY_PASSWORD + '"}'
$SETUP_RESP = curl.exe -sf -X POST "$DIFY_URL/console/api/setup" -H "Content-Type: application/json" -d $setupBody 2>&1
if (-not $SETUP_RESP) { $SETUP_RESP = '{"result":"error"}' }
$setupResult = Get-JsonField $SETUP_RESP "d.get('result','')"
if ($setupResult -eq "success") { ok "Dify 관리자 계정 생성 완료" }
else { warn "Dify 계정 생성 응답: $SETUP_RESP" }

log "4-2. Dify 로그인..."
$loginBody = '{"email":"' + $DIFY_EMAIL + '","password":"' + $DIFY_PASSWORD + '"}'
$LOGIN_RESP = curl.exe -sf -X POST "$DIFY_URL/console/api/login" -H "Content-Type: application/json" -d $loginBody 2>&1
if (-not $LOGIN_RESP) { $LOGIN_RESP = '{}' }
$DIFY_TOKEN = Get-JsonField $LOGIN_RESP "(d.get('data') or {}).get('access_token') or d.get('access_token') or ''"

if (-not $DIFY_TOKEN) {
    warn "Dify 로그인 실패 (응답: $LOGIN_RESP)"
} else {
    ok "Dify 로그인 성공"
    $DIFY_SETUP_OK = $true
}

if ($DIFY_SETUP_OK) {
    log "4-3. Ollama 모델 공급자 등록 ($OLLAMA_BASE_URL)..."
    curl.exe -sf -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/ollama" `
        -H "Authorization: Bearer $DIFY_TOKEN" `
        -H "Content-Type: application/json" `
        -d ("{`"credentials`":{`"base_url`":`"$OLLAMA_BASE_URL`"}}") 2>&1 | Out-Null
    ok "Ollama 공급자 등록 완료"

    log "4-4. LLM 모델 추가: $OLLAMA_MODEL..."
    $modelBody = ('{"model":"' + $OLLAMA_MODEL + '","model_type":"llm","credentials":{"base_url":"' + $OLLAMA_BASE_URL + '","context_size":8192,"max_tokens":4096,"mode":"chat","completion_type":"chat"}}')
    curl.exe -sf -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/ollama/models" `
        -H "Authorization: Bearer $DIFY_TOKEN" `
        -H "Content-Type: application/json" -d $modelBody 2>&1 | Out-Null
    ok "LLM 모델 추가 완료"

    log "4-5. Chatflow import (PowerShell native)..."
    $yamlPath = Join-Path $SCRIPT_DIR "dify-chatflow.yaml"
    $yamlContent = [System.IO.File]::ReadAllText($yamlPath, [System.Text.Encoding]::UTF8)
    $yamlContent = $yamlContent.Replace('{{OLLAMA_MODEL}}', $OLLAMA_MODEL)
    $importBody  = [System.Text.Json.JsonSerializer]::Serialize([pscustomobject]@{data=$yamlContent})
    $importBodyFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($importBodyFile, $importBody, [System.Text.Encoding]::UTF8)

    $IMPORT_RESP = curl.exe -s -X POST "$DIFY_URL/console/api/apps/import" `
        -H "Authorization: Bearer $DIFY_TOKEN" `
        -H "Content-Type: application/json" `
        -d "@$importBodyFile" 2>&1
    Remove-Item $importBodyFile -Force -ErrorAction SilentlyContinue
    if (-not $IMPORT_RESP) { $IMPORT_RESP = '{}' }

    $DIFY_APP_ID = Get-JsonField $IMPORT_RESP "d.get('id','')"
    if (-not $DIFY_APP_ID) {
        warn "Chatflow import 실패 (응답: $IMPORT_RESP)"
    } else {
        ok "Chatflow import 완료 (App ID: $DIFY_APP_ID)"

        log "4-6. Chatflow 게시..."
        curl.exe -s -X POST "$DIFY_URL/console/api/apps/$DIFY_APP_ID/workflows/publish" `
            -H "Authorization: Bearer $DIFY_TOKEN" `
            -H "Content-Type: application/json" `
            -d '{"marked_as_official": false}' 2>&1 | Out-Null
        ok "Chatflow 게시 완료"

        log "4-7. Dify API Key 생성..."
        $KEY_RESP = curl.exe -sf -X POST "$DIFY_URL/console/api/apps/$DIFY_APP_ID/api-keys" `
            -H "Authorization: Bearer $DIFY_TOKEN" `
            -H "Content-Type: application/json" -d '{}' 2>&1
        if (-not $KEY_RESP) { $KEY_RESP = '{}' }
        $DIFY_API_KEY = Get-JsonField $KEY_RESP "d.get('token','')"
        if ($DIFY_API_KEY) { ok "Dify API Key: $DIFY_API_KEY" }
        else { warn "API Key 생성 실패 (응답: $KEY_RESP)" }
    }
}

# ── Phase 5: Jenkins 설정 ─────────────────────────────────────────────────────
log "=== Phase 5: Jenkins 초기 설정 ==="
$JENKINS_READY = $false
for ($i = 1; $i -le 12; $i++) {
    curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" "$JENKINS_URL/api/json" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $JENKINS_READY = $true; break }
    Start-Sleep -Seconds 10
}

if (-not $JENKINS_READY) {
    warn "Jenkins REST API 응답 없음"
} else {
    ok "Jenkins API 연결 확인"

    log "5-1. Jenkins 플러그인 설치..."
    $pluginXml = '<jenkins><install plugin="workflow-aggregator@latest"/><install plugin="file-parameters@latest"/><install plugin="htmlpublisher@latest"/></jenkins>'
    curl.exe -sf -X POST "$JENKINS_URL/pluginManager/installNecessaryPlugins" `
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
        -H "Content-Type: text/xml" -d $pluginXml 2>&1 | Out-Null
    ok "플러그인 설치 요청 완료"

    log "플러그인 설치 대기 (60초)..."
    Start-Sleep -Seconds 60
    log "Jenkins 재시작..."
    docker restart e2e-jenkins 2>&1 | Out-Null
    Start-Sleep -Seconds 20
    $elapsed = 0
    while ($true) {
        curl.exe -sf "$JENKINS_URL/login" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 5
        $elapsed += 5
        if ($elapsed -ge 180) { warn "Jenkins 재시작 타임아웃"; break }
    }
    for ($i = 1; $i -le 12; $i++) {
        curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" "$JENKINS_URL/api/json" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 10
    }

    if ($DIFY_API_KEY) {
        log "5-2. Jenkins Credentials 등록..."
        $credContent = ('<com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>' +
            '<scope>GLOBAL</scope><id>dify-qa-api-token</id>' +
            '<description>Dify QA API Key</description>' +
            '<secret>' + $DIFY_API_KEY + '</secret>' +
            '</com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>')
        $credFile = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($credFile, $credContent, [System.Text.Encoding]::UTF8)
        curl.exe -sf -X POST "$JENKINS_URL/credentials/store/system/domain/_/createCredentials" `
            -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
            -H "Content-Type: application/xml" -d "@$credFile" 2>&1 | Out-Null
        Remove-Item $credFile -Force
        ok "Credentials 등록 완료"
    }

    log "5-4. Pipeline Job 생성..."
    $pipelinePath = Join-Path $SCRIPT_DIR "DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline"
    $pipelineScript = [System.IO.File]::ReadAllText($pipelinePath, [System.Text.Encoding]::UTF8)
    $escapedScript  = [System.Security.SecurityElement]::Escape($pipelineScript)
    $jobXml = ('<?xml version=''1.1'' encoding=''UTF-8''?>' +
        '<flow-definition plugin="workflow-job">' +
        '<description>DSCORE Zero-Touch QA Docker Pipeline</description>' +
        '<keepDependencies>false</keepDependencies><properties/>' +
        '<definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">' +
        '<script>' + $escapedScript + '</script><sandbox>true</sandbox></definition>' +
        '<triggers/><disabled>false</disabled></flow-definition>')
    $jobXmlFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($jobXmlFile, $jobXml, [System.Text.Encoding]::UTF8)
    curl.exe -sf -X POST "$JENKINS_URL/createItem?name=DSCORE-ZeroTouch-QA-Docker" `
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
        -H "Content-Type: application/xml" -d "@$jobXmlFile" 2>&1 | Out-Null
    Remove-Item $jobXmlFile -Force -ErrorAction SilentlyContinue
    ok "Pipeline Job 생성 완료"

    log "5-5. mac-ui-tester 에이전트 노드 등록..."
    $agentWorkDir   = "$env:USERPROFILE\jenkins-agent" -replace '\\', '\\\\'
    $scriptsHomeEsc = $SCRIPTS_HOME -replace '\\', '\\\\'
    $groovyLines = @(
        "import jenkins.model.*",
        "import hudson.model.*",
        "import hudson.slaves.*",
        "def instance = Jenkins.getInstance()",
        "if (instance.getNode('mac-ui-tester') != null) { println '[node] already exists'; return }",
        "def launcher = new JNLPLauncher()",
        "def node = new DumbSlave('mac-ui-tester','Playwright E2E Test Agent','$agentWorkDir','2',Node.Mode.NORMAL,'mac-ui-tester',launcher,new RetentionStrategy.Always(),new java.util.ArrayList())",
        "def envEntry = new EnvironmentVariablesNodeProperty.Entry('SCRIPTS_HOME','$scriptsHomeEsc')",
        "node.nodeProperties.add(new EnvironmentVariablesNodeProperty([envEntry]))",
        "instance.addNode(node); instance.save()",
        "println '[node] registered'"
    )
    $groovyScript   = $groovyLines -join "`n"
    $nodeGroovyFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($nodeGroovyFile, $groovyScript, [System.Text.Encoding]::UTF8)
    curl.exe -sf -X POST "$JENKINS_URL/scriptText" `
        -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
        -H "Content-Type: application/x-www-form-urlencoded" `
        --data-urlencode "script@$nodeGroovyFile" 2>&1 | Out-Null
    Remove-Item $nodeGroovyFile -Force
    ok "mac-ui-tester 노드 등록 완료"
}

# ── 완료 요약 ─────────────────────────────────────────────────────────────────
$jnlp = curl.exe -sf -u "${JENKINS_ADMIN_USER}:${JENKINS_ADMIN_PW}" `
    "$JENKINS_URL/computer/mac-ui-tester/slave-agent.jnlp" 2>&1
$NODE_SECRET = "<SECRET>"
if ($jnlp -match '<argument>([0-9a-f]{40,64})</argument>') { $NODE_SECRET = $Matches[1] }

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  DSCORE Zero-Touch QA 스택 설치 완료" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  Jenkins   -> http://localhost:18080  ($JENKINS_ADMIN_USER / $JENKINS_ADMIN_PW)"
Write-Host "  Dify 콘솔 -> http://localhost:18081  ($DIFY_EMAIL / $DIFY_PASSWORD)"
if ($DIFY_API_KEY) {
    Write-Host "  Dify API Key: $DIFY_API_KEY"
    Write-Host "  -> Jenkins Credentials 'dify-qa-api-token' 자동 등록됨"
}
Write-Host ""
Write-Host "  [남은 수동 작업] 에이전트 머신에서:" -ForegroundColor Yellow
Write-Host "    pip install playwright"
Write-Host "    playwright install chromium"
Write-Host "    curl -O http://localhost:18080/jnlpJars/agent.jar"
Write-Host ("    java -jar agent.jar -url http://localhost:18080 -secret `"$NODE_SECRET`" -name mac-ui-tester -workDir `"$env:USERPROFILE\jenkins-agent`"")
Write-Host "=================================================================" -ForegroundColor Cyan
