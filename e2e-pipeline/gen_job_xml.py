"""
gen_job_xml.py — Jenkins Pipeline Job config.xml 생성 헬퍼 (디버깅용)

[작성 배경]
setup.ps1 의 Phase 5-4(Pipeline Job 생성) 에서 curl 로 전송하는 XML 이 올바른지
검증하기 위해 작성한 디버깅 도구다.
DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline 파일을 읽어 XML 이스케이프 후
Jenkins /createItem API 에 맞는 config.xml 형식으로 출력한다.

[작성 사유]
Jenkins /createItem 에 config.xml 을 POST 할 때 Jenkinsfile 내용이 XML 에 포함되는데,
Jenkinsfile 에는 <, >, & 등 XML 특수문자가 있어 이스케이프 없이 전달하면
Jenkins 가 XML 파싱 오류(400/500)를 반환한다.
xml.sax.saxutils.escape() 로 이스케이프하는 것이 올바른 방법임을 검증했다.

[주의] 경로가 하드코딩되어 있으므로 다른 환경에서 사용 시 수정 필요.
"""
import xml.sax.saxutils as sax, sys

pipeline_path = r'C:\Users\KTDS\ttcScripts\e2e-pipeline\DSCORE-ZeroTouch-QA-Docker.jenkinsPipeline'
out_path = r'C:\Users\KTDS\AppData\Local\Temp\jenkins_job.xml'

with open(pipeline_path, encoding='utf-8') as f:
    script = f.read()

# [핵심] Jenkinsfile 내 <, >, & 를 &lt;, &gt;, &amp; 로 이스케이프
# 이 과정 없이 raw 삽입하면 Jenkins config.xml 파싱 오류 발생
escaped = sax.escape(script)

xml = f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>DSCORE Zero-Touch QA Docker Pipeline</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{escaped}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>"""

with open(out_path, 'w', encoding='utf-8') as f:
    f.write(xml)

print(f'Written {len(xml)} bytes to {out_path}')
