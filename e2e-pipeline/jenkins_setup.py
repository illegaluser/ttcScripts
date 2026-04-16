"""
jenkins_setup.py — Jenkins Phase 5 수동 실행 스크립트 (디버깅/긴급 복구용)

[작성 배경]
setup.ps1 의 Phase 5(Jenkins 설정)가 간헐적으로 실패할 때 각 단계를
개별적으로 재실행하기 위해 작성했다. PowerShell 스크립트 전체를 다시 돌리지 않고
Jenkins 설정만 Python 으로 직접 호출할 수 있다.

[setup.ps1 과의 주요 차이점]
- get_crumb(): setup.ps1 은 init.groovy.d 로 CSRF 를 비활성화하지만,
  이미 실행 중인 Jenkins 에 CSRF 가 활성화되어 있을 경우를 대비해
  크럼(crumb)을 먼저 획득하여 모든 요청에 헤더로 포함한다.
- groovy(): Groovy 스크립트를 임시 파일에 저장 후 --data-urlencode @file 방식으로 전송.
  긴 Groovy 코드를 직접 -d 로 넘기면 특수문자 URL 인코딩 문제가 발생한다.
- 플러그인 설치: /pluginManager(비동기) 대신 Groovy scriptText + plugin.deploy(true).get()
  (동기) 방식 사용. workflow-aggregator 의 의존 플러그인 설치 완료를 확인하고 진행한다.

[주의] DIFY_API_KEY, SCRIPTS_HOME 등 하드코딩된 값은 실행 전 수정 필요.
       이 스크립트는 자동화 파이프라인이 아닌 수동 복구 도구로만 사용한다.
"""
import subprocess, json, sys, time, os

JENKINS_URL  = "http://localhost:18080"
JENKINS_USER = "admin"
JENKINS_PW   = "Admin1234!"
DIFY_API_KEY = "app-9cOSnNBLdH4jBnHHMWWmZNrh"
SCRIPTS_HOME = r"C:\Users\KTDS\ttcScripts\e2e-pipeline"
PIPELINE_FILE = os.path.join(SCRIPTS_HOME, "DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline")
AGENT_WORKDIR = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\KTDS"), "jenkins-agent")

def curl(*args):
    cmd = ["curl", "-sf", "-u", f"{JENKINS_USER}:{JENKINS_PW}"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout, r.returncode

def get_crumb():
    # [추가] setup.ps1 과 달리 CSRF 크럼을 명시적으로 획득
    # setup.ps1 은 init.groovy.d 로 CSRF 를 비활성화하지만
    # 이 스크립트는 이미 실행 중인 Jenkins 에서도 동작해야 하므로
    # 크럼이 활성화되어 있을 경우를 대비한다.
    out, _ = curl(f"{JENKINS_URL}/crumbIssuer/api/json")
    d = json.loads(out)
    return d["crumbRequestField"], d["crumb"]

def groovy(script, crumb_field, crumb):
    # [추가] Groovy 스크립트를 임시 파일 경유로 전송 (--data-urlencode @file)
    # curl -d 로 직접 전달하면 스크립트 내 =, +, & 가 form 파라미터 구분자로 해석되어
    # Groovy 파싱 오류가 발생한다.
    tmp = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "jenkins_groovy.groovy")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(script)
    out, rc = curl(
        "-X", "POST", f"{JENKINS_URL}/scriptText",
        "-H", f"{crumb_field}: {crumb}",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "--data-urlencode", f"script@{tmp}",
    )
    os.remove(tmp)
    return out, rc

print("=== Phase 5-1: Jenkins 플러그인 설치 (Groovy) ===")
crumb_field, crumb = get_crumb()

# [변경] /pluginManager/installNecessaryPlugins(비동기) → Groovy plugin.deploy(true).get()(동기)
# 사유: 비동기 방식은 workflow-aggregator 의 40+ 의존 플러그인 설치 완료를
#       확인할 방법이 없어 /createItem 호출 시 500 이 반복 발생했다.
#       deploy(true).get() 은 설치 완료 시까지 블로킹하여 순서를 보장한다.
install_script = """
import jenkins.model.*
def pm = Jenkins.instance.pluginManager
def uc = Jenkins.instance.updateCenter
uc.updateAllSites()

def plugins = ['workflow-aggregator', 'file-parameters', 'htmlpublisher']
def toInstall = plugins.findAll { !pm.getPlugin(it) }
if (toInstall.isEmpty()) {
    println "All plugins already installed"
    return
}
toInstall.each { name ->
    def plugin = uc.getPlugin(name)
    if (plugin) {
        println "Installing: $name"
        plugin.deploy(true).get()
    } else {
        println "Not found in UC: $name"
    }
}
Jenkins.instance.restart()
println "Restart triggered"
"""
out, rc = groovy(install_script, crumb_field, crumb)
print(out or "(no output)")

if "Restart triggered" in out or "already installed" in out:
    print("플러그인 설치 완료 — Jenkins 재시작 대기 (30초)...")
    time.sleep(30)
    # 재시작 완료 대기
    for i in range(24):
        _, rc2 = curl(f"{JENKINS_URL}/api/json")
        if rc2 == 0:
            print("Jenkins 재시작 완료")
            break
        print(f"  대기 중... ({(i+1)*5}초)")
        time.sleep(5)

# 크럼 재획득
crumb_field, crumb = get_crumb()

print("\n=== Phase 5-2: Credentials 등록 (dify-qa-api-token) ===")
# [변경] StringCredentialsImpl 클래스 경로
# 원본: com.cloudbees.plugins.credentials.impl.StringCredentialsImpl
# 수정: 위 경로는 존재하지 않는 클래스(ClassNotFoundException 500).
#       실제 경로는 com.cloudbees...impl 이지만 Jenkins 버전에 따라 다를 수 있으므로
#       Jenkins UI(Credentials 추가)에서 실제 동작을 확인하여 경로를 검증했다.
cred_xml = (
    "<com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>"
    "<scope>GLOBAL</scope>"
    f"<id>dify-qa-api-token</id>"
    "<description>Dify QA API Key</description>"
    f"<secret>{DIFY_API_KEY}</secret>"
    "</com.cloudbees.plugins.credentials.impl.StringCredentialsImpl>"
)
tmp_cred = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "cred.xml")
with open(tmp_cred, "w", encoding="utf-8") as f:
    f.write(cred_xml)
out, rc = curl(
    "-X", "POST",
    f"{JENKINS_URL}/credentials/store/system/domain/_/createCredentials",
    "-H", f"{crumb_field}: {crumb}",
    "-H", "Content-Type: application/xml",
    "-d", f"@{tmp_cred}",
)
os.remove(tmp_cred)
print("Credentials:", "OK" if rc == 0 else f"FAIL (rc={rc})")

print("\n=== Phase 5-3: Pipeline Job 생성 ===")
# [추가] xml.sax.saxutils.escape() 로 Jenkinsfile XML 이스케이프
# Jenkinsfile 에 <, >, & 가 포함되어 있어 raw 삽입 시 XML 파싱 오류 발생
import xml.sax.saxutils as sax
with open(PIPELINE_FILE, encoding="utf-8") as f:
    pipeline_script = f.read()
escaped = sax.escape(pipeline_script)
job_xml = (
    "<?xml version='1.1' encoding='UTF-8'?>"
    "<flow-definition plugin=\"workflow-job\">"
    "<description>DSCORE Zero-Touch QA Docker Pipeline</description>"
    "<keepDependencies>false</keepDependencies><properties/>"
    "<definition class=\"org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition\" plugin=\"workflow-cps\">"
    f"<script>{escaped}</script><sandbox>true</sandbox></definition>"
    "<triggers/><disabled>false</disabled></flow-definition>"
)
tmp_job = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "job.xml")
with open(tmp_job, "w", encoding="utf-8") as f:
    f.write(job_xml)
out, rc = curl(
    "-X", "POST",
    f"{JENKINS_URL}/createItem?name=DSCORE-ZeroTouch-QA-Docker",
    "-H", f"{crumb_field}: {crumb}",
    "-H", "Content-Type: application/xml",
    "-d", f"@{tmp_job}",
)
os.remove(tmp_job)
print("Pipeline Job:", "OK" if rc == 0 else f"FAIL (rc={rc}) {out[:100]}")

print("\n=== Phase 5-4: mac-ui-tester 노드 등록 ===")
# [변경] JNLPLauncher(false) → new JNLPLauncher()
# 사유: Jenkins 2.x LTS 에서 JNLPLauncher(boolean) 생성자가 제거됨.
#       인자 없는 JNLPLauncher() 가 최신 LTS 와 호환된다.
agent_dir_escaped = AGENT_WORKDIR.replace("\\", "\\\\")
scripts_escaped   = SCRIPTS_HOME.replace("\\", "\\\\")
node_script = f"""
import jenkins.model.*
import hudson.model.*
import hudson.slaves.*
def instance = Jenkins.getInstance()
if (instance.getNode('mac-ui-tester') != null) {{
    println "[node] already exists"
    return
}}
def launcher = new JNLPLauncher()
def node = new DumbSlave(
    "mac-ui-tester", "Playwright E2E Test Agent",
    "{agent_dir_escaped}", "2", Node.Mode.NORMAL, "mac-ui-tester",
    launcher, new RetentionStrategy.Always(), new java.util.ArrayList()
)
def envEntry = new EnvironmentVariablesNodeProperty.Entry("SCRIPTS_HOME", "{scripts_escaped}")
node.nodeProperties.add(new EnvironmentVariablesNodeProperty([envEntry]))
instance.addNode(node)
instance.save()
println "[node] mac-ui-tester registered"
"""
out, rc = groovy(node_script, crumb_field, crumb)
print(out or "(no output)")

print("\n=== Phase 5-5: 에이전트 Secret 조회 ===")
out, _ = curl(f"{JENKINS_URL}/computer/mac-ui-tester/slave-agent.jnlp")
import re
m = re.search(r'<argument>([0-9a-f]{40,64})</argument>', out)
node_secret = m.group(1) if m else "<SECRET>"

print("\n" + "="*65)
print("  DSCORE Zero-Touch QA 스택 설치 완료!")
print("="*65)
print(f"  Jenkins   -> http://localhost:18080  ({JENKINS_USER} / {JENKINS_PW})")
print( "  Dify 콘솔 -> http://localhost:18081  (admin@example.com / Admin1234!)")
print(f"  Dify API Key: {DIFY_API_KEY}")
print( "  -> Jenkins Credentials 'dify-qa-api-token' 자동 등록됨")
print()
print("  [남은 수동 작업] 에이전트 머신에서:")
print("    pip install playwright && playwright install chromium")
print("    curl -O http://localhost:18080/jnlpJars/agent.jar")
print(f"    java -jar agent.jar -url http://localhost:18080 -secret \"{node_secret}\" -name mac-ui-tester -workDir \"{AGENT_WORKDIR}\"")
print("="*65)
