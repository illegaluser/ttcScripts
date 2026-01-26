FROM python:3.11-slim-bookworm

# 1. 시스템 패키지 (Curl, Build tool, Node.js)
# Node.js는 Promptfoo 실행을 위해 필수
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# 2. Python 라이브러리 (버전 고정)
# pytest-xdist: 병렬/직렬 실행 제어 (-n 옵션) 지원
# pydantic: DeepEval 호환성 유지를 위해 2.6.0 고정
# jsonschema: Format Compliance(스키마 검증) 실행을 위해 추가
RUN pip install --no-cache-dir \
    deepeval==1.3.5 \
    pytest==8.0.0 \
    pytest-xdist==3.5.0 \
    langchain-core==0.1.30 \
    pandas==2.2.0 \
    httpx==0.26.0 \
    langfuse==2.15.0 \
    requests==2.31.0 \
    pydantic==2.6.0 \
    jsonschema==4.21.1

# 3. Promptfoo (정적 분석 도구)
RUN npm install -g promptfoo@0.50.0

# 4. 환경 변수 설정 (Module Import 경로)
ENV PYTHONPATH=/app

WORKDIR /app
CMD ["tail", "-f", "/dev/null"]