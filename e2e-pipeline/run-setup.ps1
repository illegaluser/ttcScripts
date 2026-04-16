# run-setup.ps1 — setup.ps1 의 래퍼 스크립트 (모델/프로파일 선택)
#
# [작성 배경]
# setup.ps1 은 OLLAMA_MODEL 기본값이 "qwen2.5-coder:14b" 이지만
# 실제 운영 환경에서는 qwen3.5:9b 를 사용한다.
# 매번 $env:OLLAMA_MODEL 를 직접 설정하는 대신 이 래퍼에서 모델명과
# 프로파일(host/container)을 고정하여 setup.ps1 을 호출한다.
#
# OLLAMA_PROFILE = "host":
#   Ollama 가 Docker 컨테이너가 아닌 호스트 머신에서 직접 실행될 때 사용.
#   Docker 컨테이너에서 호스트를 참조하려면 host.docker.internal 이 필요하며
#   이 경우 OLLAMA_BASE_URL = "http://host.docker.internal:11434" 로 설정된다.
#   Windows GPU 환경에서는 컨테이너 안에서 GPU 를 직접 쓰는 것보다
#   호스트 Ollama 를 사용하는 것이 설정이 단순하다.

$env:OLLAMA_MODEL   = "qwen3.5:9b"
$env:OLLAMA_PROFILE = "host"
& "$PSScriptRoot\setup.ps1"
