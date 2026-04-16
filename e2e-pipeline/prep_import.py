"""
prep_import.py — Dify Chatflow import 요청 body 생성 헬퍼 (디버깅용)

[작성 배경]
setup.ps1 의 Phase 4-5(Chatflow import) 에서 curl body 가 올바르게 생성되는지
검증하기 위해 작성한 디버깅 도구다.
dify-chatflow.yaml 을 읽어 {{OLLAMA_MODEL}} 플레이스홀더를 치환한 뒤
{"mode":"yaml-content","yaml_content":"..."} 형태의 JSON body 를 임시 파일에 저장한다.

[사용법]
    python prep_import.py qwen3.5:9b

[변경 이력]
- 원본 setup.sh 의 Dify import API 경로: /console/api/apps/import (단수)
  → 실제 Dify 1.13.x 경로: /console/api/apps/imports (복수)
- 원본 body: {"data": "<yaml>"} → 실제 body: {"mode":"yaml-content","yaml_content":"<yaml>"}
  Dify API 스펙 변경으로 키 이름이 바뀌었다.
  (잘못된 body 로는 422 Unprocessable Entity 가 반환됨)

[주의] 경로가 하드코딩되어 있으므로 다른 환경에서 사용 시 수정 필요.
"""
import json, sys

ollama_model = sys.argv[1]
yaml_path = r'C:\Users\KTDS\ttcScripts\e2e-pipeline\dify-chatflow.yaml'

with open(yaml_path, encoding='utf-8') as f:
    content = f.read()

content = content.replace('{{OLLAMA_MODEL}}', ollama_model)

# [변경] body 키: "data" → "mode"+"yaml_content"
# 사유: Dify 1.13.x 에서 /apps/imports API 의 body 스펙이 변경됨
body = json.dumps({'mode': 'yaml-content', 'yaml_content': content})

out_path = r'C:\Users\KTDS\AppData\Local\Temp\dify_import_body.json'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(body)

print('Body size:', len(body), 'bytes')
print('Written to:', out_path)
