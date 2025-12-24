# DSCORE-TTC 통합 구축 가이드

---

## 0. 개요 및 문서 범위

이 문서는 **온프레미스 DevOps + Test Management 시스템**을 처음부터 끝까지 구축하는 완전한 가이드다.

### 시스템 구성:

**Phase 1: DevOps 인프라 구축 (Section 1-3)**
1. Jenkins, SonarQube, GitLab, Dify를 Docker Compose로 구성
2. 네트워크, 볼륨, 포트 매핑 설정
3. 초기 인증 토큰 발급

**Phase 2: 지식 관리 자동화 (Section 4-6)**
1. 문서/이미지/코드/웹을 Dify Knowledge Base에 자동 업로드
2. Jenkins Pipeline으로 지식 동기화 자동화
3. AI가 코드와 문서를 학습하여 컨텍스트 제공

**Phase 3: 품질 분석 자동화 (Section 5-6)**
1. SonarQube 정적 분석 결과를 Dify Workflow(LLM)로 진단
2. 진단 결과를 GitLab Issue로 자동 등록
3. 중복 방지 메커니즘 적용

**Phase 4: E2E 테스트 자동화 (Section 9)**
1. **실제 개발 중인 프로젝트(웹앱)**의 E2E 테스트 자동 작성
2. Playwright + Roo Code(AI)가 브라우저로 화면 분석
3. 생성된 테스트를 Jenkins CI/CD 파이프라인에 통합

### Jenkins Pipeline 구성 (총 6개):

1. **Job #1: DSCORE-Knowledge-Sync** - 문서형 Dataset 업로드
2. **Job #2: DSCORE-Knowledge-Sync-QA** - Q&A형 Dataset 업로드
3. **Job #3: DSCORE-Knowledge-Sync-Vision** - 이미지 Vision 분석
4. **Job #4: DSCORE-Code-Knowledge-Sync** - 코드 컨텍스트 지식화
5. **Job #5: DSCORE-Quality-Issue-Workflow** - 정적 분석 및 이슈 자동 등록
6. **Job #6: DSCORE-Web-Knowledge-Sync** - 웹 콘텐츠 수집 및 지식화

### 최종 산출물:

* **온프레미스 DevOps 인프라:** 개발/테스트/배포가 가능한 완전한 환경
* **AI 기반 지식 관리:** 문서, 코드, 웹 지식의 자동 학습 및 검색
* **자동화된 품질 분석:** 정적 분석 → LLM 진단 → Issue 등록
* **E2E 테스트 자동 생성:** AI가 실제 웹앱을 분석하여 테스트 코드 작성

---

## 1. 고정 전제 및 주소 체계

### 1.1 호스트 브라우저 접속 URL (고정)

1. **Jenkins:** `http://localhost:8080`
2. **SonarQube:** `http://localhost:9000`
3. **GitLab:** `http://localhost:8929`

### 1.2 컨테이너 내부 접근 URL (고정)

1. **Jenkins → Dify API:** `http://api:5001` 또는 `http://api:5001/v1`
2. **Jenkins → SonarQube:** `http://sonarqube:9000`
3. **Jenkins → GitLab:** `http://gitlab:8929`
4. **Jenkins → Ollama:** `http://host.docker.internal:11434` (Vision 사용 시)

### 1.3 공유 볼륨 및 경로 (고정)

1. **호스트 폴더:** `<PROJECT_ROOT>/data/knowledges`
2. **Jenkins 컨테이너 마운트:** `/var/knowledges`
3. **원본 문서 폴더(컨테이너 기준):** `/var/knowledges/docs/org`
4. **변환 결과 폴더(컨테이너 기준):** `/var/knowledges/docs/result`
5. **코드 컨텍스트 폴더(컨테이너 기준):** `/var/knowledges/codes`
6. **스크립트 경로(컨테이너 기준):** `/var/jenkins_home/scripts/`

---

## 2. 호스트 환경 설정 및 Docker 인프라 구성

### 2.1 `/etc/hosts` 오염 복구 (macOS)

**문제 원인:**

1. `/etc/hosts` 에 `127.0.0.1 gitlab` 라인이 추가되면, GitLab의 external url 설정 및 브라우저 리다이렉트 흐름이 꼬여 UI 접속 문제가 발생할 수 있다.
2. 따라서 해당 라인은 제거한다.

**복구 절차:**

1. 터미널에서 다음 명령을 실행한다.
```bash
sudo grep -n "gitlab" /etc/hosts || true
```

2. `127.0.0.1 gitlab` 라인이 확인되면 다음 명령을 실행한다.
```bash
sudo sed -i '' '/^[[:space:]]*127\.0\.0\.1[[:space:]]\+gitlab[[:space:]]*$/d' /etc/hosts
```

3. 변경 내용을 확인한다.
```bash
sudo grep -n "gitlab" /etc/hosts || true
```

4. DNS 캐시를 비운다.
```bash
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder
```

### 2.2 프로젝트 디렉터리 구성

1. `<PROJECT_ROOT>`는 다음과 같이 둔다. (예: `/Users/luuuuunatic/Developer/dscore-ttc`)
2. 터미널에서 `<PROJECT_ROOT>`로 이동한다.
3. 다음 명령을 실행하여 필수 폴더를 생성한다.
```bash
mkdir -p data/jenkins/scripts
mkdir -p data/knowledges/docs/org
mkdir -p data/knowledges/docs/result
mkdir -p data/gitlab
mkdir -p data/sonarqube
mkdir -p data/postgres-sonar
```

### 2.3 Docker 네트워크 생성 (필수)

1. 다음 명령을 실행한다.
```bash
docker network create devops-net 2>/dev/null || true
docker network ls | grep devops-net
```

2. `devops-net` 이 목록에 보이면 다음 단계로 진행한다.

### 2.4 DevOps 스택 (`docker-compose.yaml`) 배치

파일: `<PROJECT_ROOT>/docker-compose.yaml`

*(아래 내용을 그대로 저장한다. 포트/호스트명/네트워크를 임의로 바꾸지 않는다.)*

```yaml
networks:
  devops-net:
    external: true
services:
  #
  # 1) SonarQube DB
  #
  postgres-sonar:
    image: postgres:15-alpine
    container_name: postgres-sonar
    environment:
      POSTGRES_USER: sonar
      POSTGRES_PASSWORD: sonarpassword
      POSTGRES_DB: sonar
    volumes:
      - ./data/postgres-sonar:/var/lib/postgresql/data
    networks:
      - devops-net
    restart: unless-stopped
  #
  # 2) SonarQube
  #
  sonarqube:
    image: sonarqube:community
    container_name: sonarqube
    depends_on:
      - postgres-sonar
    environment:
      SONAR_JDBC_URL: jdbc:postgresql://postgres-sonar:5432/sonar
      SONAR_JDBC_USERNAME: sonar
      SONAR_JDBC_PASSWORD: sonarpassword
      SONAR_ES_BOOTSTRAP_CHECKS_DISABLE: true
    ports:
      - "9000:9000"
    volumes:
      - ./data/sonarqube/data:/opt/sonarqube/data
      - ./data/sonarqube/extensions:/opt/sonarqube/extensions
      - ./data/sonarqube/logs:/opt/sonarqube/logs
    networks:
      - devops-net
    restart: unless-stopped
  #
  # 3) GitLab CE
  #
  gitlab:
    #
    image: gitlab/gitlab-ce:latest
    container_name: gitlab
    # Apple Silicon (M1/M2/M3) 환경에서 amd64 이미지 경고가 뜰 수 있다.
    # 경고를 제거하려면 아래 platform 라인을 활성화한다.
    # platform: linux/amd64
    hostname: gitlab.local
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://localhost:8929'
        gitlab_rails['gitlab_shell_ssh_port'] = 2224
        puma['worker_processes'] = 2
        sidekiq['concurrency'] = 5
        prometheus_monitoring['enable'] = false
        gitlab_rails['time_zone'] = 'Asia/Seoul'
    ports:
      # GitLab이 컨테이너 내부에서 8929 포트로 서비스되는 구성이다.
      # 따라서 호스트 8929를 컨테이너 8929로 매핑한다.
      - "8929:8929"
      - "2224:22"
    volumes:
      - ./data/gitlab/config:/etc/gitlab
      - ./data/gitlab/logs:/var/log/gitlab
      - ./data/gitlab/data:/var/opt/gitlab
    networks:
      - devops-net
    shm_size: "256m"
    restart: unless-stopped
  #
  # 4) Jenkins (Custom Image)
  #
  jenkins:
    build:
      context: .
      dockerfile: Dockerfile.jenkins
    container_name: jenkins
    user: root
    ports:
      - "8080:8080"
      - "50000:50000"
    volumes:
      - ./data/jenkins:/var/jenkins_home
      - ./data/jenkins/scripts:/var/jenkins_home/scripts
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data/knowledges:/var/knowledges
    networks:
      - devops-net
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

**포트 매핑 관련 핵심 정리**

1. GitLab external_url `http://localhost:8929` 로 두고 컨테이너 내부 포트도 8929로 쓰는 구성에서는 `8929:8929` 가 맞는다.
2. `8929:80` 매핑은 "컨테이너 내부 HTTP가 80"인 구성에서만 맞는다.
3. 현재 구성은 "컨테이너 내부 HTTP가 8929"인 구성이다.

### 2.5 Jenkins 커스텀 이미지 (`Dockerfile.jenkins`) 배치

파일: `<PROJECT_ROOT>/Dockerfile.jenkins`

**중요 규칙:**
- fitz 패키지는 설치하지 않고, pymupdf 만 설치한다.
- Crawl4AI와 Playwright를 설치하여 웹 스크래핑 기능을 지원한다.

```dockerfile
# DSCORE-TTC 통합 Jenkins 이미지
# 목적:
# - Jenkins 컨테이너 내부에서 문서 변환, 웹 스크래핑, 업로드를 바로 실행한다.
# - PPTX -> PDF 변환을 위해 LibreOffice를 설치한다.
# - PDF 텍스트 추출을 위해 PyMuPDF(pymupdf)를 설치한다.
# - 웹 스크래핑을 위해 Crawl4AI와 Playwright를 설치한다.
#
# 중요한 규칙:
# - pip에서 "fitz" 패키지는 설치하지 않는다.
# - 대신 "pymupdf"만 설치한다.
# - 파이썬 코드에서는 "import fitz"를 사용한다.
#   PyMuPDF(pymupdf)가 제공하는 모듈 이름이 fitz이기 때문이다.

FROM jenkins/jenkins:lts-jdk21
USER root

# 1. 시스템 의존성 설치 (Playwright 브라우저 실행용 라이브러리 포함)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # python3 / pip: Jenkins 컨테이너 내부에서 스크립트 실행용 런타임
    python3 python3-pip python3-venv \
    # curl/jq: Jenkins 단계에서 API 상태 확인, JSON 응답 확인용
    curl jq \
    # Playwright(Chromium) 실행에 필요한 최소 런타임 라이브러리
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    # poppler-utils: PDF 처리 보조 도구(pdftotext 등), 문제 분석용
    poppler-utils \
    # libreoffice-impress: PPTX -> PDF 변환용(헤드리스 변환)
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# 2. 필수 파이썬 패키지 설치
RUN pip3 install --no-cache-dir --break-system-packages \
    # requests: Dify API / Ollama API 호출용 HTTP 클라이언트
    requests \
    # tenacity: 업로드/변환 과정 재시도 정책 구성용
    tenacity \
    # beautifulsoup4/lxml/html2text: 웹 스크래핑 및 HTML -> 텍스트 변환
    beautifulsoup4 lxml html2text \
    # pypdf/pdf2image/pillow: PDF 파싱/이미지 변환/이미지 입출력(확장 대비)
    pypdf pdf2image pillow \
    # python-docx: docx 텍스트 추출용
    python-docx \
    # python-pptx: pptx 메타 분석/확장 대비(변환은 LibreOffice가 담당)
    python-pptx \
    # pandas/openpyxl: xlsx/xls 로딩 및 표 데이터 추출용
    pandas openpyxl \
    # pymupdf: PDF 텍스트 추출용(모듈명 fitz)
    pymupdf \
    # 웹 스크래핑 엔진: Crawl4AI와 Playwright
    crawl4ai playwright

# 3. Playwright 브라우저 엔진 및 시스템 라이브러리 설치 (핵심)
# Chromium 브라우저와 필요한 시스템 라이브러리를 함께 설치한다.
RUN python3 -m playwright install --with-deps chromium

ENV TZ=Asia/Seoul

RUN mkdir -p /var/jenkins_home/scripts \
    /var/jenkins_home/knowledges \
    && chown -R jenkins:jenkins /var/jenkins_home

USER jenkins
```

### 2.6 DevOps 스택 기동 및 상태 확인

1. `<PROJECT_ROOT>` 에서 다음 명령을 실행한다.
```bash
docker compose up -d --build --force-recreate
```

2. 상태를 확인한다. (`docker compose ps`)
   * `postgres-sonar`, `sonarqube`, `gitlab`, `jenkins`가 Up 상태여야 한다.

3. GitLab 초기 기동 확인 (필수):
```bash
docker exec -it gitlab gitlab-ctl status
```
   * GitLab의 주요 프로세스가 `run:` 상태여야 한다.

### 2.7 Dify 스택 설정 및 devops-net 연결 (필수)

1. Dify 설치 폴더(예: `<PROJECT_ROOT>/dify/docker`)로 이동한다.

2. **환경변수 파일 복사 (필수):**
```bash
cp .env.example .env
```

3. `<DIFY_ROOT>/docker-compose.override.yaml` 파일을 생성한다.
```yaml
services:
  # Jenkins에서 DNS 이름 "api"로 접근하기 위해 api 서비스에 devops-net을 추가한다.
  api:
    networks:
      - default
      - devops-net
  # 워커도 동일 네트워크에 붙여 Dify 내부 처리 흐름을 고정한다.
  worker:
    networks:
      - default
      - devops-net
  # 웹 UI(nginx)도 devops-net에 붙여 내부 테스트 동선을 단순화한다.
  nginx:
    networks:
      - default
      - devops-net
networks:
  devops-net:
    external: true
```

4. Dify 폴더에서 `docker compose up -d`를 실행한다.

5. Jenkins 컨테이너에서 Dify API DNS 확인을 실행한다.
```bash
docker exec -it jenkins sh -c 'curl -sS -o /dev/null -w "%{http_code}\n" http://api:5001/ || true'
```
   * 404가 출력되어도 정상이다. 핵심은 000이 아니고 응답 코드가 돌아오는 것이다.

---

## 3. 초기 설정 및 인증 토큰 발급 (상세)

### 3.1 Dify 지식베이스 준비 (문서형 1개 + Q&A형 1개)

**왜 Dataset을 2개로 분리해야 하는가**

1. Dify 문서 생성 API는 요청의 `doc_form` 값이 Dataset의 `doc_form` 과 일치해야 한다.
2. `doc_form` 값은 `text_model`, `hierarchical_model`, `qa_model` 중 하나다.
3. Dataset의 `doc_form` 은 지식베이스 상세 조회 응답에서도 확인된다.
4. 따라서 문서 업로드용 Dataset과 Q&A 업로드용 Dataset을 분리한다.

**Dify API Key 발급 (문서형/QA형 공통)**

1. Dify 콘솔에 로그인한다.
2. 상단 또는 좌측 메뉴에서 "API Keys" 또는 "API Access" 메뉴를 연다.
3. "Create API Key" 또는 "New Key" 버튼을 선택한다.
4. 키 이름 입력란에 식별 가능한 이름을 입력한다.
5. 생성 버튼을 선택한다.
6. 화면에 표시되는 API Key를 즉시 복사한다.
7. API Key는 재표시가 제한될 수 있으므로 즉시 저장한다.

**Dataset UUID 확인 (문서형/QA형 각각)**

1. Dify 콘솔에서 Knowledge 또는 Datasets 메뉴로 이동한다.
2. 문서형 Dataset을 하나 생성한다.
3. Q&A형 Dataset을 하나 생성한다.
4. 각 Dataset의 상세 화면으로 진입한다.
5. 브라우저 주소창 URL 경로에서 Dataset UUID를 확인한다.
6. Dataset UUID를 각각 저장한다.

### 3.2 Jenkins 초기 설정 (Unlock, 플러그인, Credential)

1. `http://localhost:8080` 접속.
2. `docker exec -it jenkins cat /var/jenkins_home/secrets/initialAdminPassword` 로 초기 비밀번호 확인 및 입력.
3. "Install suggested plugins" 진행.
4. **필수 플러그인 설치:** Manage Jenkins -> Plugins -> Available -> `SonarQube Scanner` 설치 (Install without restart).

### 3.3 Jenkins Credentials 등록 절차 (UI 상세)

Jenkins 관리 -> Credentials -> System -> Global credentials (unrestricted) -> Add Credentials (Kind: **Secret text**)

| Credential ID (고정) | 설명 |
| --- | --- |
| `gitlab-access-token` | GitLab PAT (`api`, `read_repository`, `write_repository` 권한) |
| `sonarqube-token` | SonarQube User Token (Security > Generate Tokens) |
| `dify-knowledge-key` | Dify 문서형 API Key |
| `dify-dataset-id` | Dify 문서형 Dataset UUID |
| `dify-knowledge-key-qa` | Dify Q&A형 API Key |
| `dify-dataset-id-qa` | Dify Q&A형 Dataset UUID |

**추가 설정:** Manage Jenkins -> Configure System -> Global properties -> Environment variables 체크 -> Name: `SONAR_TOKEN`, Value: (SonarQube Token) 추가.

### 3.4 토큰 발급 절차 상세

**GitLab PAT 발급**

1. `http://localhost:8929` 접속 및 로그인.
2. 프로필 -> Preferences (또는 Edit profile) -> Access Tokens.
3. Token name 입력.
4. Scopes 선택: `api`, `read_repository`, `write_repository`.
5. "Create personal access token" 선택 -> 값(`glpat-...`) 복사 및 저장.

**SonarQube 토큰 발급**

1. `http://localhost:9000` 접속 (ID: `admin` / PW: `admin` -> 변경).
2. 프로필 -> My Account -> Security.
3. "Generate Tokens" -> Name 입력 -> Generate.
4. 값(`sqp-...`) 복사 및 저장.

---

## 4. 파이썬 스크립트 배치 (코드 및 상세 주석)

모든 스크립트는 Jenkins 컨테이너의 `/var/jenkins_home/scripts/` 경로에 배치하고 실행 권한(`chmod +x`)을 부여한다.

### 4.1 `doc_processor.py` (문서 변환 및 업로드)

**이 스크립트는 convert 와 upload 를 제공한다. upload 는 Dify 문서 생성 API의 doc_form을 Dataset과 일치시키도록 강제한다.**

```python
#!/usr/bin/env python3
# ============================================================================
# doc_processor.py
# 목적: 
#   - convert: SOURCE_DIR의 문서(PDF, DOCX, XLSX, TXT, PPTX)를 RESULT_DIR로 변환
#   - upload: RESULT_DIR의 결과물을 Dify Dataset에 업로드
# 
# 주요 특징:
#   - PyMuPDF(import fitz)로 PDF 텍스트 추출
#   - python-docx로 DOCX 텍스트 추출
#   - pandas로 XLSX/XLS 표 데이터 추출
#   - LibreOffice(soffice)로 PPTX -> PDF 변환
#   - Dify API doc_form 검증 (Dataset과 요청 일치 확인)
# ============================================================================
import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple
import requests
import fitz  # PyMuPDF (pymupdf)가 제공하는 모듈 이름이다.

# ============================================================================
# 고정 디렉터리
# - Jenkins 컨테이너에서 /var/knowledges는 호스트와 공유된 볼륨이다.
# ============================================================================
SOURCE_DIR = "/var/knowledges/docs/org"      # 원본 문서 폴더
RESULT_DIR = "/var/knowledges/docs/result"   # 변환 결과 폴더

# ============================================================================
# Dify API 베이스 URL
# - Jenkins 컨테이너는 devops-net에서 "api" DNS로 Dify API 컨테이너에 접근한다.
# ============================================================================
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://api:5001/v1")

def log(msg: str) -> None:
    """
    Jenkins Console Output에서 단계별 진행을 명확히 보기 위한 로그 출력 함수다.
    flush=True를 사용하여 버퍼링 없이 즉시 출력한다.
    """
    print(msg, flush=True)

def safe_read_text(path: Path, max_bytes: int = 5_000_000) -> str:
    """
    텍스트 파일을 안전하게 읽는다.
    너무 큰 파일은 max_bytes까지만 읽어 변환 단계에서 과도한 메모리 사용을 막는다.
    
    Args:
        path: 읽을 파일 경로
        max_bytes: 최대 읽을 바이트 수 (기본 5MB)
    
    Returns:
        UTF-8 디코딩된 문자열 (오류 발생 시 빈 문자열)
    """
    try:
        data = path.read_bytes()
        data = data[:max_bytes]  # 최대 크기 제한
        return data.decode("utf-8", errors="ignore")  # 디코딩 오류 무시
    except Exception:
        return ""

def pdf_to_markdown(pdf_path: Path) -> str:
    """
    PDF에서 텍스트를 추출해 Markdown 본문으로 만든다.
    PyMuPDF를 사용하며, 코드에서는 import fitz 형태로 호출한다.
    
    Args:
        pdf_path: PDF 파일 경로
    
    Returns:
        Markdown 형식의 문자열 (각 페이지를 ## Page N 헤더로 구분)
    """
    doc = fitz.open(str(pdf_path))
    parts = []
    
    # 각 페이지를 순회하며 텍스트 추출
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""  # 페이지 텍스트 추출
        text = text.strip()
        if text:
            parts.append(f"## Page {i}\n\n{text}\n")
    
    doc.close()
    
    if not parts:
        return ""  # 텍스트가 없으면 빈 문자열 반환
    
    # 파일명을 제목으로, 페이지별 텍스트를 본문으로 조합
    title = pdf_path.name
    body = "\n".join(parts).strip()
    return f"# {title}\n\n{body}\n"

def docx_to_markdown(docx_path: Path) -> str:
    """
    DOCX에서 문단 텍스트를 추출해 Markdown 본문으로 만든다.
    python-docx를 사용한다.
    
    Args:
        docx_path: DOCX 파일 경로
    
    Returns:
        Markdown 형식의 문자열 (각 문단을 개행으로 구분)
    """
    try:
        from docx import Document
    except Exception:
        return ""
    
    try:
        d = Document(str(docx_path))
        lines = []
        
        # 모든 문단을 순회하며 텍스트 추출
        for p in d.paragraphs:
            t = (p.text or "").strip()
            if t:
                lines.append(t)
        
        if not lines:
            return ""
        
        # 파일명을 제목으로, 문단을 본문으로 조합
        return f"# {docx_path.name}\n\n" + "\n\n".join(lines) + "\n"
    except Exception:
        return ""

def excel_to_markdown(xls_path: Path) -> str:
    """
    XLSX/XLS에서 시트 데이터를 읽어 Markdown 형태로 만든다.
    pandas + openpyxl을 사용한다.
    
    Args:
        xls_path: XLSX/XLS 파일 경로
    
    Returns:
        Markdown 형식의 문자열 (각 시트를 ## Sheet: 헤더로 구분, 표는 Markdown 표 형식)
    """
    try:
        import pandas as pd
    except Exception:
        return ""
    
    try:
        # 여러 시트가 있는 경우, 모든 시트를 읽어 Markdown으로 합친다.
        sheets = pd.read_excel(str(xls_path), sheet_name=None)
        if not sheets:
            return ""
        
        out = [f"# {xls_path.name}\n"]
        for sheet_name, df in sheets.items():
            out.append(f"## Sheet: {sheet_name}\n")
            # DataFrame을 Markdown 표 형식으로 변환
            out.append(df.to_markdown(index=False))
            out.append("\n")
        
        return "\n".join(out).strip() + "\n"
    except Exception:
        return ""

def pptx_to_pdf(pptx_path: Path, out_dir: Path) -> Optional[Path]:
    """
    PPTX를 PDF로 변환한다.
    LibreOffice(soffice)를 사용하며, Dockerfile에서 libreoffice-impress를 설치한다.
    
    Args:
        pptx_path: PPTX 파일 경로
        out_dir: 출력 디렉터리
    
    Returns:
        변환된 PDF 파일 경로 (실패 시 None)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # LibreOffice는 출력 파일명을 원본과 동일한 base name으로 만든다.
    expected_pdf = out_dir / (pptx_path.stem + ".pdf")
    
    # soffice 명령: headless 모드로 PPTX를 PDF로 변환
    cmd = [
        "soffice", "--headless", "--nologo", "--nolockcheck", "--norestore",
        "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path),
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        return None
    
    # 변환 성공 시 PDF 파일 경로 반환
    if expected_pdf.exists():
        return expected_pdf
    return None

def write_text(path: Path, content: str) -> None:
    """
    UTF-8로 파일을 저장한다.
    
    Args:
        path: 저장할 파일 경로
        content: 저장할 내용
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def convert_one(src_path: Path) -> None:
    """
    단일 파일을 변환해 RESULT_DIR에 저장한다.
    변환 대상 확장자는 아래 규칙을 따른다.
    - pdf: 텍스트 추출 후 .pdf.md 저장
    - docx: 텍스트 추출 후 .docx.md 저장
    - xlsx/xls: 표 추출 후 .xlsx.md/.xls.md 저장
    - txt: 그대로 .txt.md 저장
    - pptx: PDF 변환 후 .pdf 저장
    
    Args:
        src_path: 원본 파일 경로
    """
    ext = src_path.suffix.lower()
    
    # PDF 처리
    if ext == ".pdf":
        md = pdf_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    
    # DOCX 처리
    if ext == ".docx":
        md = docx_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    
    # XLSX/XLS 처리
    if ext in [".xlsx", ".xls"]:
        md = excel_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    
    # TXT 처리
    if ext == ".txt":
        text = safe_read_text(src_path)
        if text.strip():
            md = f"# {src_path.name}\n\n{text.strip()}\n"
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    
    # PPTX 처리 (PDF로 변환)
    if ext == ".pptx":
        pdf = pptx_to_pdf(src_path, Path(RESULT_DIR))
        if pdf:
            log(f"[Saved] {pdf}")
        return

def convert_all() -> None:
    """
    SOURCE_DIR 아래의 파일을 순회하며 변환한다.
    지원 확장자: .pdf, .docx, .xlsx, .xls, .txt, .pptx
    """
    log("[Convert] start")
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    src_root = Path(SOURCE_DIR)
    for root, _, files in os.walk(src_root):
        for name in files:
            p = Path(root) / name
            ext = p.suffix.lower()
            
            # 지원하지 않는 확장자는 건너뛴다
            if ext not in [".pdf", ".docx", ".xlsx", ".xls", ".txt", ".pptx"]:
                continue
            
            log(f"[Convert] {p.name}")
            convert_one(p)
    
    log("[Convert] done")

def dify_headers(api_key: str) -> dict:
    """
    Dify API 호출 공통 헤더를 만든다.
    Dify API는 Authorization 헤더에 Bearer 토큰 형태를 요구한다.
    
    Args:
        api_key: Dify API Key
    
    Returns:
        HTTP 헤더 딕셔너리
    """
    return {
        "Authorization": f"Bearer {api_key}",
    }

def get_dataset_doc_form(api_key: str, dataset_id: str) -> str:
    """
    Dataset 상세 조회로 doc_form을 확인한다.
    doc_form 값은 text_model / hierarchical_model / qa_model 중 하나다.
    
    Args:
        api_key: Dify API Key
        dataset_id: Dataset UUID
    
    Returns:
        Dataset의 doc_form 값
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    r = requests.get(url, headers=dify_headers(api_key), timeout=60)
    r.raise_for_status()
    data = r.json()
    doc_form = data.get("doc_form", "")
    return str(doc_form)

def ensure_doc_form_matches(api_key: str, dataset_id: str, expected_doc_form: str) -> None:
    """
    업로드 요청의 doc_form과 Dataset의 doc_form이 동일한지 검증한다.
    다르면 Dify에서 400 invalid_param(doc_form mismatch) 오류가 발생한다.
    
    Args:
        api_key: Dify API Key
        dataset_id: Dataset UUID
        expected_doc_form: 업로드 시 사용할 doc_form
    
    Raises:
        SystemExit: doc_form이 일치하지 않을 때
    """
    actual = get_dataset_doc_form(api_key, dataset_id)
    if actual != expected_doc_form:
        raise SystemExit(
            f"[FAIL] Dataset doc_form mismatch. dataset={actual} / request={expected_doc_form}"
        )

def upload_text_document(api_key: str, dataset_id: str, name: str, text: str, doc_form: str, doc_language: str) -> Tuple[bool, str]:
    """
    텍스트 기반 문서 생성 API로 업로드한다.
    Endpoint는 /datasets/{dataset_id}/document/create-by-text 이다.
    
    Args:
        api_key: Dify API Key
        dataset_id: Dataset UUID
        name: 문서 이름
        text: 문서 내용
        doc_form: 문서 형식 (text_model, qa_model 등)
        doc_language: 문서 언어 (Korean, English 등)
    
    Returns:
        (성공 여부, 상세 메시지) 튜플
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-text"
    payload = {
        "name": name,
        "text": text,
        "indexing_technique": "economy",
        "doc_form": doc_form,
        "doc_language": doc_language,
        "process_rule": {
            "rules": {
                "remove_extra_spaces": True,  # 불필요한 공백 제거
            },
            "remove_urls_emails": False,  # URL/이메일 유지
        },
        "mode": "automatic",  # 자동 청크 분할
    }
    
    r = requests.post(
        url, 
        headers={**dify_headers(api_key), "Content-Type": "application/json"}, 
        json=payload, 
        timeout=300
    )
    
    if r.status_code >= 400:
        return False, f"{r.status_code} / {r.text}"
    return True, "OK"

def upload_file_document(api_key: str, dataset_id: str, file_path: Path, doc_form: str, doc_language: str) -> Tuple[bool, str]:
    """
    파일 업로드 기반 문서 생성 API로 업로드한다.
    Endpoint는 /datasets/{dataset_id}/document/create-by-file 이다.
    
    Args:
        api_key: Dify API Key
        dataset_id: Dataset UUID
        file_path: 업로드할 파일 경로
        doc_form: 문서 형식 (text_model, qa_model 등)
        doc_language: 문서 언어 (Korean, English 등)
    
    Returns:
        (성공 여부, 상세 메시지) 튜플
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-file"
    data = {
        "name": file_path.name,
        "indexing_technique": "economy",
        "doc_form": doc_form,
        "doc_language": doc_language,
        "process_rule": {
            "rules": {
                "remove_extra_spaces": True,
            },
            "remove_urls_emails": False,
        },
        "mode": "automatic",
    }
    
    # multipart/form-data 형식으로 파일과 메타데이터 전송
    with file_path.open("rb") as f:
        files = {
            "file": (file_path.name, f),
            "data": (None, json.dumps(data)),
        }
        r = requests.post(url, headers=dify_headers(api_key), files=files, timeout=600)
    
    if r.status_code >= 400:
        return False, f"{r.status_code} / {r.text}"
    return True, "OK"

def upload_all(api_key: str, dataset_id: str, doc_form: str, doc_language: str) -> None:
    """
    RESULT_DIR의 파일을 스캔해 업로드한다.
    - .md는 create-by-text로 업로드한다.
    - .pdf는 create-by-file로 업로드한다.
    
    Args:
        api_key: Dify API Key
        dataset_id: Dataset UUID
        doc_form: 문서 형식
        doc_language: 문서 언어
    """
    log("[Upload] start")
    
    # doc_form 일치 여부 검증 (필수)
    ensure_doc_form_matches(api_key, dataset_id, doc_form)
    
    result_root = Path(RESULT_DIR)
    if not result_root.exists():
        log("[Upload] result dir not found")
        return
    
    # RESULT_DIR의 모든 파일을 순회
    for p in sorted(result_root.glob("*")):
        if p.is_dir():
            continue
        
        # Markdown 파일은 텍스트로 업로드
        if p.suffix.lower() == ".md":
            text = safe_read_text(p)
            ok, detail = upload_text_document(api_key, dataset_id, p.name, text, doc_form, doc_language)
            if ok:
                log(f"[Upload:OK] {p.name}")
            else:
                log(f"[Upload:FAIL] {p.name} / {detail}")
            continue
        
        # PDF 파일은 파일로 업로드
        if p.suffix.lower() in [".pdf"]:
            ok, detail = upload_file_document(api_key, dataset_id, p, doc_form, doc_language)
            if ok:
                log(f"[Upload:OK] {p.name}")
            else:
                log(f"[Upload:FAIL] {p.name} / {detail}")
            continue
    
    log("[Upload] done")

def main() -> None:
    """
    사용 방법.
    convert: SOURCE_DIR의 문서를 RESULT_DIR로 변환한다.
    upload: RESULT_DIR의 결과물을 Dify Dataset에 업로드한다.
    
    Usage:
        python3 doc_processor.py convert
        python3 doc_processor.py upload <API_KEY> <DATASET_ID> <doc_form> <doc_language>
    """
    if len(sys.argv) < 2:
        raise SystemExit("usage: doc_processor.py [convert|upload] ...")
    
    cmd = sys.argv[1].strip().lower()
    
    if cmd == "convert":
        convert_all()
        return
    
    if cmd == "upload":
        if len(sys.argv) != 6:
            raise SystemExit("usage: doc_processor.py upload <API_KEY> <DATASET_ID> <doc_form> <doc_language>")
        api_key = sys.argv[2]
        dataset_id = sys.argv[3]
        doc_form = sys.argv[4]
        doc_language = sys.argv[5]
        upload_all(api_key, dataset_id, doc_form, doc_language)
        return
    
    raise SystemExit(f"unknown cmd: {cmd}")

if __name__ == "__main__":
    main()
```

### 4.2 `vision_processor.py` (비전 분석)

```python
#!/usr/bin/env python3
# ============================================================================
# vision_processor.py
# 목적: 
#   - 이미지 파일(JPG, PNG, BMP, WEBP)을 Llama 3.2 Vision 모델로 분석
#   - 분석 결과를 Markdown 문서로 저장
# 
# 주요 특징:
#   - Ollama API (/api/generate 엔드포인트) 사용
#   - Base64 인코딩으로 이미지 전송
#   - 호스트에서 실행 중인 Ollama에 host.docker.internal로 접근
# ============================================================================
import os
import base64
from pathlib import Path
from typing import List
import requests

# ============================================================================
# Ollama API
# - Ollama는 호스트에서 실행한다고 가정한다.
# - Jenkins 컨테이너에서 host.docker.internal:11434 로 접근한다.
# ============================================================================
OLLAMA_API = "http://host.docker.internal:11434/api/generate"

# ============================================================================
# 입력/출력 디렉터리
# ============================================================================
SOURCE_DIR = "/var/knowledges/docs/org"      # 원본 이미지 폴더
RESULT_DIR = "/var/knowledges/docs/result"   # 분석 결과 폴더

# ============================================================================
# 처리 대상 확장자
# ============================================================================
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

def log(msg: str) -> None:
    """
    Jenkins Console Output에서 진행 로그를 명확히 출력한다.
    
    Args:
        msg: 출력할 메시지
    """
    print(msg, flush=True)

def list_images(root_dir: str) -> List[Path]:
    """
    SOURCE_DIR 아래에서 이미지 파일을 수집한다.
    
    Args:
        root_dir: 검색할 루트 디렉터리
    
    Returns:
        이미지 파일 경로 리스트 (정렬됨)
    """
    out: List[Path] = []
    for root, _, files in os.walk(root_dir):
        for name in files:
            p = Path(root) / name
            # 확장자가 IMAGE_EXTS에 포함되는 파일만 수집
            if p.suffix.lower() in IMAGE_EXTS:
                out.append(p)
    return sorted(out)

def analyze_image(image_path: Path) -> str:
    """
    이미지 파일을 Llama 3.2 Vision 모델로 분석해 설명 텍스트를 얻는다.
    출력은 Markdown 문서에 그대로 넣을 수 있는 자연어 설명을 목표로 한다.
    
    Args:
        image_path: 분석할 이미지 파일 경로
    
    Returns:
        이미지 설명 텍스트
    """
    # 이미지를 Base64로 인코딩
    with image_path.open("rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    
    # Ollama API 요청 페이로드
    payload = {
        "model": "llama3.2-vision:latest",  # Vision 모델명
        "prompt": "Describe this image in detail for markdown documentation.",
        "stream": False,  # 스트리밍 비활성화 (전체 응답 한 번에 받기)
        "images": [img_b64],  # Base64 인코딩된 이미지 배열
    }
    
    # API 호출
    resp = requests.post(OLLAMA_API, json=payload, timeout=300)
    resp.raise_for_status()
    
    # 응답에서 설명 텍스트 추출
    data = resp.json()
    return str(data.get("response", "")).strip()

def save_markdown(image_name: str, desc: str) -> Path:
    """
    분석 결과를 RESULT_DIR에 .md로 저장한다.
    파일명은 원본 이미지명에 .md를 붙인다.
    
    Args:
        image_name: 원본 이미지 파일명
        desc: 이미지 설명 텍스트
    
    Returns:
        저장된 Markdown 파일 경로
    """
    os.makedirs(RESULT_DIR, exist_ok=True)
    out_path = Path(RESULT_DIR) / f"{image_name}.md"
    
    # Markdown 문서 형식으로 저장
    # 제목: 이미지 파일명 (Vision Analysis)
    # 본문: Vision 모델이 생성한 설명
    out_path.write_text(
        f"# {image_name} (Vision Analysis)\n\n{desc}\n", 
        encoding="utf-8"
    )
    return out_path

def main() -> None:
    """
    SOURCE_DIR에서 이미지 파일을 찾아 분석한다.
    분석 결과를 RESULT_DIR에 .md로 저장한다.
    
    실행 조건:
        - 호스트에서 ollama serve 실행 중
        - llama3.2-vision 모델 설치됨
        - SOURCE_DIR에 이미지 파일 존재
    """
    log("[Vision] start")
    
    # 이미지 파일 목록 수집
    images = list_images(SOURCE_DIR)
    
    if not images:
        log("[Vision] no images")
        return
    
    # 각 이미지를 순회하며 분석
    for img in images:
        log(f"[Vision] {img.name}")
        
        # Vision 모델로 이미지 분석
        desc = analyze_image(img)
        
        # 설명이 비어있으면 건너뛴다
        if not desc:
            log(f"[Vision:SKIP] {img.name} (empty)")
            continue
        
        # Markdown 파일로 저장
        out = save_markdown(img.name, desc)
        log(f"[Saved] {out}")
    
    log("[Vision] done")

if __name__ == "__main__":
    main()
```

### 4.3 `repo_context_builder.py` (코드 지식화)

```python
#!/usr/bin/env python3
# ============================================================================
# repo_context_builder.py
# 목적: 
#   - Git 레포지토리의 디렉터리 트리와 주요 파일 내용을 context.md로 생성
#   - Dify에 업로드하여 AI가 프로젝트 구조와 설정을 이해하도록 함
# 
# 주요 특징:
#   - os.walk로 디렉터리 트리 생성
#   - .git, node_modules, build 등 불필요한 폴더 제외
#   - README.md, package.json 등 주요 파일 내용 포함
#   - 최대 라인 수 제한으로 과도한 크기 방지
# ============================================================================
import argparse
import os
from pathlib import Path

# ============================================================================
# 트리 생성 시 제외할 디렉터리 목록
# - .git: Git 메타데이터
# - node_modules: NPM 패키지
# - build, dist, out, target: 빌드 산출물
# - IDE 설정 폴더: .idea, .vscode 등
# ============================================================================
EXCLUDE_DIRS = {
    ".git", ".scannerwork", "node_modules", "build", "dist", "out", "target",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv",
}

# ============================================================================
# context.md에 전체 내용을 포함할 주요 파일 목록
# - README: 프로젝트 설명
# - package.json, pom.xml 등: 의존성 정보
# - .env.example: 환경 변수 예시
# ============================================================================
KEY_FILES = [
    "README.md",
    "README.txt",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
    ".env.example",
]

def safe_read_text(path: Path, max_bytes: int) -> str:
    """
    파일을 바이트 단위로 읽고 UTF-8로 복호화한다.
    너무 큰 파일은 max_bytes까지만 읽어 메모리 사용을 제한한다.
    
    Args:
        path: 읽을 파일 경로
        max_bytes: 최대 읽을 바이트 수
    
    Returns:
        UTF-8 디코딩된 문자열 (오류 발생 시 빈 문자열)
    """
    try:
        data = path.read_bytes()
        data = data[:max_bytes]  # 최대 크기 제한
        return data.decode("utf-8", errors="ignore")  # 디코딩 오류 무시
    except Exception:
        return ""

def build_tree(repo_root: Path, max_lines: int = 3000) -> str:
    """
    디렉터리 트리를 텍스트 형식으로 생성한다.
    os.walk로 트리를 순회하고 최대 라인 수를 초과하면 중단한다.
    
    Args:
        repo_root: Git 레포지토리 루트 경로
        max_lines: 최대 라인 수 (기본 3000)
    
    Returns:
        디렉터리 트리 텍스트
    """
    lines = []
    count = 0
    
    # os.walk로 디렉터리 트리 순회
    for root, dirs, files in os.walk(repo_root):
        # 현재 디렉터리의 상대 경로
        rel_root = Path(root).relative_to(repo_root)
        
        # EXCLUDE_DIRS에 포함된 디렉터리는 제외
        # dirs[:] = 를 사용하여 os.walk의 순회 대상을 수정
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        # 들여쓰기 깊이 계산
        depth = len(rel_root.parts)
        indent = "  " * depth
        
        # 디렉터리 이름 출력
        if str(rel_root) == ".":
            lines.append(f"{repo_root.name}/")
        else:
            lines.append(f"{indent}{rel_root.name}/")
        
        count += 1
        if count >= max_lines:
            lines.append("[TRUNCATED] tree lines limit reached")
            break
        
        # 파일 목록 출력 (정렬)
        for f in sorted(files):
            # .DS_Store 등 불필요한 파일 제외
            if f in (".DS_Store",):
                continue
            
            lines.append(f"{indent}  {f}")
            count += 1
            
            if count >= max_lines:
                lines.append("[TRUNCATED] tree lines limit reached")
                break
        
        if count >= max_lines:
            break
    
    return "\n".join(lines) + "\n"

def main() -> int:
    """
    Git 레포지토리의 context.md 파일을 생성한다.
    
    Usage:
        python3 repo_context_builder.py --repo_root <PATH> --out <OUTPUT_PATH>
    
    Returns:
        종료 코드 (0: 성공)
    """
    # 명령행 인자 파싱
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo_root", required=True, help="Git repository root path")
    ap.add_argument("--out", required=True, help="Output context.md file path")
    ap.add_argument("--max_key_file_bytes", type=int, default=30000, 
                    help="Max bytes to read from key files (default: 30000)")
    args = ap.parse_args()
    
    repo_root = Path(args.repo_root).resolve()
    out_path = Path(args.out).resolve()
    
    # Markdown 문서 구성
    parts = []
    parts.append("# Repository Context")
    parts.append("")
    
    # ========================================================================
    # Section 1: Tree (디렉터리 구조)
    # ========================================================================
    parts.append("## Tree")
    parts.append("")
    parts.append("```text")
    parts.append(build_tree(repo_root))
    parts.append("```")
    parts.append("")
    
    # ========================================================================
    # Section 2: Key Files (주요 파일 내용)
    # ========================================================================
    parts.append("## Key Files")
    parts.append("")
    
    # KEY_FILES 목록을 순회하며 파일 내용 포함
    for k in KEY_FILES:
        p = repo_root / k
        
        # 파일이 존재하지 않으면 건너뛴다
        if not p.exists():
            continue
        
        # 파일명을 ### 헤더로 추가
        parts.append(f"### {k}")
        parts.append("")
        
        # 파일 내용을 코드 블록으로 추가
        parts.append("```")
        parts.append(safe_read_text(p, args.max_key_file_bytes))
        parts.append("```")
        parts.append("")
    
    # context.md 파일로 저장
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[Saved] {out_path}")
    
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### 4.4 `sonar_issue_exporter.py` (이슈 추출)

```python
#!/usr/bin/env python3
# ============================================================================
# sonar_issue_exporter.py
# 목적: 
#   - SonarQube API로 정적 분석 이슈를 추출
#   - 페이징 처리로 모든 이슈를 수집
#   - JSON 파일로 저장
# 
# 주요 특징:
#   - /api/issues/search 엔드포인트 사용
#   - Severity, Status 필터 지원
#   - 페이지별 500개씩 수집 (최대 100페이지)
# ============================================================================
import argparse
import json
import sys
import time
from typing import Dict, Any, List, Optional
import urllib.parse
import requests

def eprint(*args):
    """
    표준 에러 출력 (stderr)
    
    Args:
        *args: 출력할 내용
    """
    print(*args, file=sys.stderr)

def parse_csv(s: str) -> List[str]:
    """
    CSV 문자열을 리스트로 정규화한다.
    
    Args:
        s: CSV 문자열 (예: "BLOCKER,CRITICAL,MAJOR")
    
    Returns:
        공백 제거된 문자열 리스트
    """
    items = []
    for x in (s or "").split(","):
        t = x.strip()
        if t:
            items.append(t)
    return items

def sonar_get(session: requests.Session, base: str, path: str, params: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    SonarQube API GET 요청을 수행한다.
    
    Args:
        session: requests.Session 객체 (연결 재사용)
        base: SonarQube 베이스 URL (예: http://sonarqube:9000)
        path: API 엔드포인트 경로 (예: /api/issues/search)
        params: 쿼리 파라미터
        token: SonarQube 인증 토큰
    
    Returns:
        JSON 응답 딕셔너리
    
    Raises:
        HTTPError: HTTP 4xx/5xx 오류 시
    """
    url = base.rstrip("/") + path
    headers = {}
    
    # 토큰이 있으면 Bearer 인증 헤더 추가
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    # API 호출
    r = session.get(url, params=params, headers=headers, timeout=60)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        eprint(f"[ERROR] network/http error: HTTP {r.status_code} : {r.text[:2000]}")
        r.raise_for_status()
    
    return r.json()

def export_issues(args) -> Dict[str, Any]:
    """
    SonarQube 이슈를 페이징 방식으로 전체 수집한다.
    
    Args:
        args: argparse로 파싱된 명령행 인자
    
    Returns:
        이슈 목록과 메타데이터를 포함한 딕셔너리
    """
    # CSV 필터 파싱
    severities = parse_csv(args.severities)  # 예: ["BLOCKER", "CRITICAL"]
    statuses = parse_csv(args.statuses)      # 예: ["OPEN", "REOPENED"]
    
    session = requests.Session()
    page_size = 500  # 한 페이지당 이슈 개수
    p = 1            # 현재 페이지 번호
    all_issues = []  # 전체 이슈 누적 리스트
    
    # 페이징 루프
    while True:
        # API 요청 파라미터 구성
        params = {
            "componentKeys": args.project_key,  # 프로젝트 키
            "types": "BUG,VULNERABILITY,CODE_SMELL",  # 이슈 타입
            "ps": page_size,  # Page Size
            "p": p,           # Page Number
        }
        
        # 필터 추가 (선택사항)
        if severities:
            params["severities"] = ",".join(severities)
        if statuses:
            params["statuses"] = ",".join(statuses)
        
        # SonarQube API 호출
        data = sonar_get(session, args.sonar_host_url, "/api/issues/search", params, args.sonar_token)
        
        # 응답에서 이슈 목록 추출
        issues = data.get("issues") or []
        all_issues.extend(issues)
        
        # 페이징 정보 확인
        paging = data.get("paging") or {}
        total = paging.get("total", len(all_issues))
        page_index = paging.get("pageIndex", p)
        
        # 모든 이슈를 수집했으면 종료
        if len(all_issues) >= total:
            break
        
        # 다음 페이지로 이동
        p = page_index + 1
        
        # API Rate Limit 방지 (50ms 대기)
        time.sleep(0.05)
    
    # 결과 JSON 구성
    out = {
        "generated_at": int(time.time()),               # 생성 시각 (Unix timestamp)
        "sonar_host_url": args.sonar_host_url,          # SonarQube 내부 URL
        "sonar_public_url": args.sonar_public_url,      # SonarQube 브라우저 URL
        "project_key": args.project_key,                # 프로젝트 키
        "filters": {                                     # 적용된 필터
            "severities": severities,
            "statuses": statuses,
        },
        "count": len(all_issues),                       # 이슈 총 개수
        "issues": all_issues,                           # 이슈 목록 (전체)
    }
    
    return out

def main():
    """
    명령행 인자를 파싱하고 SonarQube 이슈를 추출한다.
    
    Usage:
        python3 sonar_issue_exporter.py \
            --sonar-host-url http://sonarqube:9000 \
            --sonar-public-url http://localhost:9000 \
            --sonar-token <TOKEN> \
            --project-key <KEY> \
            --severities "BLOCKER,CRITICAL" \
            --statuses "OPEN,REOPENED" \
            --output sonar_issues.json
    """
    # 명령행 인자 정의
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True, 
                    help="SonarQube internal URL (e.g., http://sonarqube:9000)")
    ap.add_argument("--sonar-public-url", required=False, default="", 
                    help="SonarQube browser URL (e.g., http://localhost:9000)")
    ap.add_argument("--sonar-token", required=False, default="", 
                    help="SonarQube authentication token")
    ap.add_argument("--project-key", required=True, 
                    help="SonarQube project key")
    ap.add_argument("--severities", required=False, default="", 
                    help="Comma-separated severities (e.g., BLOCKER,CRITICAL)")
    ap.add_argument("--statuses", required=False, default="", 
                    help="Comma-separated statuses (e.g., OPEN,REOPENED)")
    ap.add_argument("--output", required=True, 
                    help="Output JSON file path")
    args = ap.parse_args()
    
    # 이슈 추출
    out = export_issues(args)
    
    # JSON 파일로 저장
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    
    print(f"[OK] exported: {args.output} (count={out['count']})")

if __name__ == "__main__":
    main()
```

### 4.5 `dify_sonar_issue_analyzer.py` (LLM 분석)

```python
#!/usr/bin/env python3
# ============================================================================
# dify_sonar_issue_analyzer.py
# 목적: 
#   - SonarQube 이슈를 Dify Workflow(LLM)로 전송하여 원인 분석
#   - 각 이슈마다 코드 스니펫, 룰 정보 수집
#   - LLM 분석 결과를 JSONL 파일로 저장
# 
# 주요 특징:
#   - /api/rules/show로 룰 상세 정보 조회
#   - /api/sources/lines로 코드 스니펫 추출
#   - Dify Workflow /v1/workflows/run 호출
#   - 실패가 1건이라도 있으면 exit code 2로 파이프라인 실패 처리
# ============================================================================
import argparse
import json
import sys
import time
from typing import Dict, Any, List, Optional, Tuple
import urllib.parse
import requests

def eprint(*args):
    """
    표준 에러 출력 (stderr)
    
    Args:
        *args: 출력할 내용
    """
    print(*args, file=sys.stderr)

def safe_json_dumps(obj) -> str:
    """
    객체를 안전하게 JSON 문자열로 변환한다.
    변환 실패 시 str()로 폴백한다.
    
    Args:
        obj: JSON으로 변환할 객체
    
    Returns:
        JSON 문자열 또는 str() 결과
    """
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)

def read_json(path: str) -> Dict[str, Any]:
    """
    JSON 파일을 읽어 딕셔너리로 반환한다.
    
    Args:
        path: JSON 파일 경로
    
    Returns:
        파싱된 딕셔너리
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    """
    JSONL 파일로 저장한다. (한 줄에 하나의 JSON 객체)
    
    Args:
        path: 출력 파일 경로
        rows: 딕셔너리 리스트
    """
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def sonar_auth_headers(token: str) -> Dict[str, str]:
    """
    SonarQube API 인증 헤더를 생성한다.
    
    Args:
        token: SonarQube 인증 토큰
    
    Returns:
        HTTP 헤더 딕셔너리
    """
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

def sonar_get(session: requests.Session, base: str, path: str, params: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    SonarQube API GET 요청을 수행한다.
    
    Args:
        session: requests.Session 객체
        base: SonarQube 베이스 URL
        path: API 엔드포인트 경로
        params: 쿼리 파라미터
        token: 인증 토큰
    
    Returns:
        JSON 응답 딕셔너리
    
    Raises:
        HTTPError: HTTP 4xx/5xx 오류 시
    """
    url = base.rstrip("/") + path
    r = session.get(url, params=params, headers=sonar_auth_headers(token), timeout=60)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        eprint(f"[SONAR] [ERROR] HTTP {r.status_code}: {r.text[:2000]}")
        r.raise_for_status()
    
    return r.json()

def build_issue_url(sonar_public_url: str, project_key: str, issue_key: str) -> str:
    """
    SonarQube UI에서 이슈를 바로 열 수 있는 URL을 조립한다.
    
    Args:
        sonar_public_url: 브라우저 접근용 SonarQube URL (예: http://localhost:9000)
        project_key: 프로젝트 키
        issue_key: 이슈 키
    
    Returns:
        이슈 직접 링크 URL
    """
    if not sonar_public_url:
        return ""
    
    base = sonar_public_url.rstrip("/")
    # SonarQube UI에서 이슈를 바로 여는 URL 형식
    return f"{base}/project/issues?id={urllib.parse.quote(project_key)}&issues={urllib.parse.quote(issue_key)}&open={urllib.parse.quote(issue_key)}"

def fetch_rule_json(session: requests.Session, sonar_host_url: str, sonar_token: str, rule_key: str) -> Dict[str, Any]:
    """
    SonarQube API로 룰 상세 정보를 조회한다.
    
    Args:
        session: requests.Session 객체
        sonar_host_url: SonarQube 내부 URL
        sonar_token: 인증 토큰
        rule_key: 룰 키 (예: java:S1234)
    
    Returns:
        룰 정보 딕셔너리
    """
    if not rule_key:
        return {}
    
    # /api/rules/show 엔드포인트로 룰 조회
    return sonar_get(session, sonar_host_url, "/api/rules/show", {"key": rule_key}, sonar_token)

def fetch_code_snippet(session: requests.Session, sonar_host_url: str, sonar_token: str, component: str, line: Optional[int], context: int = 5) -> str:
    """
    SonarQube API로 코드 라인 범위를 가져온다.
    
    Args:
        session: requests.Session 객체
        sonar_host_url: SonarQube 내부 URL
        sonar_token: 인증 토큰
        component: 컴포넌트 키 (파일 경로)
        line: 이슈 발생 라인 번호
        context: 앞뒤로 가져올 라인 수 (기본 5줄)
    
    Returns:
        코드 스니펫 텍스트 (라인 번호 포함, 이슈 라인은 >> 표시)
    """
    if not component or not line:
        return ""
    
    # 라인 범위 계산 (이슈 라인 기준 ±context)
    start = max(1, int(line) - context)
    end = int(line) + context
    
    # /api/sources/lines 엔드포인트로 코드 조회
    data = sonar_get(session, sonar_host_url, "/api/sources/lines", 
                     {"key": component, "from": start, "to": end}, sonar_token)
    
    # 응답 구조는 SonarQube 버전에 따라 다를 수 있음
    lines = data.get("sources") or data.get("lines") or []
    out_lines = []
    
    # 각 라인을 순회하며 포맷팅
    for item in lines:
        ln = item.get("line")
        code = item.get("code", "")
        # 이슈 라인은 >> 표시, 나머지는 공백 3개
        prefix = ">> " if ln == line else "   "
        out_lines.append(f"{prefix}{ln}: {code}")
    
    return "\n".join(out_lines)

def dify_run_workflow(session: requests.Session, dify_api_base: str, dify_api_key: str, inputs: Dict[str, Any], user: str, response_mode: str, timeout: int) -> Tuple[bool, Dict[str, Any], str]:
    """
    Dify Workflow를 실행한다.
    
    Args:
        session: requests.Session 객체
        dify_api_base: Dify API 베이스 URL (예: http://api:5001/v1)
        dify_api_key: Dify API Key
        inputs: Workflow 입력 변수 딕셔너리
        user: 사용자 식별자
        response_mode: 응답 모드 ("blocking" 또는 "streaming")
        timeout: 타임아웃 (초)
    
    Returns:
        (성공 여부, 응답 딕셔너리, 오류 메시지) 튜플
    """
    url = dify_api_base.rstrip("/") + "/workflows/run"
    headers = {
        "Authorization": f"Bearer {dify_api_key}",
        "Content-Type": "application/json",
    }
    
    # Workflow 실행 페이로드
    payload = {
        "inputs": inputs,          # Workflow 입력 변수
        "response_mode": response_mode,  # blocking: 전체 응답 대기
        "user": user,              # 사용자 식별자
    }
    
    # API 호출
    r = session.post(url, headers=headers, data=json.dumps(payload), timeout=timeout)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        try:
            return False, {}, f"HTTP {r.status_code}: {r.text[:2000]}"
        except Exception:
            return False, {}, f"Invalid JSON response: {r.text[:2000]}"
    
    data = r.json()
    return True, data, ""

def pick_outputs(dify_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dify 응답에서 outputs를 추출한다.
    Dify 버전에 따라 응답 구조가 다를 수 있어 방어적으로 탐색한다.
    
    Args:
        dify_response: Dify API 응답 딕셔너리
    
    Returns:
        outputs 딕셔너리 (없으면 빈 딕셔너리)
    """
    outputs = {}
    
    # blocking 모드 기준으로 outputs는 data.outputs 또는 outputs에 있다
    if "data" in dify_response and isinstance(dify_response["data"], dict):
        outputs = dify_response["data"].get("outputs") or {}
    
    if not outputs:
        outputs = dify_response.get("outputs") or {}
    
    if not isinstance(outputs, dict):
        return {}
    
    return outputs

def main():
    """
    SonarQube 이슈를 Dify Workflow로 분석하고 결과를 JSONL로 저장한다.
    
    Usage:
        python3 dify_sonar_issue_analyzer.py \
            --sonar-host-url http://sonarqube:9000 \
            --sonar-public-url http://localhost:9000 \
            --sonar-token <TOKEN> \
            --dify-api-base http://api:5001/v1 \
            --dify-api-key <KEY> \
            --input sonar_issues.json \
            --output llm_analysis.jsonl \
            --user jenkins \
            --max-issues 10
    """
    # 명령행 인자 정의
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-public-url", required=False, default="")
    ap.add_argument("--sonar-token", required=False, default="")
    ap.add_argument("--dify-api-base", required=True)
    ap.add_argument("--dify-api-key", required=True)
    ap.add_argument("--input", required=True, help="Input JSON file (from sonar_issue_exporter.py)")
    ap.add_argument("--output", required=True, help="Output JSONL file")
    ap.add_argument("--user", required=True, help="Dify user identifier")
    ap.add_argument("--response-mode", required=False, default="blocking")
    ap.add_argument("--timeout", required=False, type=int, default=180)
    ap.add_argument("--max-issues", required=False, type=int, default=0, 
                    help="Max issues to analyze (0 = all)")
    ap.add_argument("--print-first-errors", required=False, type=int, default=5,
                    help="Print first N errors to console")
    args = ap.parse_args()
    
    # 입력 JSON 읽기
    src = read_json(args.input)
    issues = src.get("issues") or []
    project_key = src.get("project_key") or ""
    
    # max-issues 제한 적용
    if args.max_issues and args.max_issues > 0:
        issues = issues[:args.max_issues]
    
    session = requests.Session()
    analyzed = 0  # 분석 성공 카운트
    failed = 0    # 분석 실패 카운트
    rows = []     # JSONL 출력 행
    first_errors = 0  # 콘솔 출력한 오류 개수
    
    # 각 이슈를 순회하며 분석
    for it in issues:
        # 이슈 정보 추출
        issue_key = it.get("key") or ""
        rule_key = it.get("rule") or ""
        component = it.get("component") or ""
        line = it.get("line")
        
        # SonarQube UI 링크 생성
        sonar_issue_url = build_issue_url(args.sonar_public_url, project_key, issue_key)
        
        # 룰 정보 조회
        try:
            rule_json = fetch_rule_json(session, args.sonar_host_url, args.sonar_token, rule_key)
        except Exception as ex:
            rule_json = {}
            eprint(f"[SONAR] [WARN] rule fetch failed: issue={issue_key} rule={rule_key} err={ex}")
        
        # 코드 스니펫 조회
        try:
            snippet = fetch_code_snippet(session, args.sonar_host_url, args.sonar_token, component, line)
        except Exception as ex:
            snippet = ""
            eprint(f"[SONAR] [WARN] snippet fetch failed: issue={issue_key} component={component} line={line} err={ex}")
        
        # Dify Workflow 입력 변수 구성 (7개 고정)
        inputs = {
            "sonar_issue_json": safe_json_dumps(it),           # 이슈 전체 JSON
            "sonar_rule_json": safe_json_dumps(rule_json),     # 룰 상세 JSON
            "code_snippet": snippet,                           # 코드 스니펫
            "sonar_issue_url": sonar_issue_url,                # SonarQube UI 링크
            "sonar_issue_key": issue_key,                      # 이슈 키
            "sonar_project_key": project_key,                  # 프로젝트 키
            "kb_query": f"{rule_key} {it.get('message','')}".strip(),  # Knowledge Base 검색 키워드
        }
        
        # Dify Workflow 실행
        ok, resp, err = dify_run_workflow(
            session=session,
            dify_api_base=args.dify_api_base,
            dify_api_key=args.dify_api_key,
            inputs=inputs,
            user=args.user,
            response_mode=args.response_mode,
            timeout=args.timeout
        )
        
        # 실패 처리
        if not ok:
            failed += 1
            if first_errors < args.print_first_errors:
                first_errors += 1
                eprint(f"[DIFY] [FAIL] issue={issue_key} {err}")
            continue
        
        # 응답에서 outputs 추출
        outputs = pick_outputs(resp)
        title = outputs.get("title") or ""
        description_markdown = outputs.get("description_markdown") or ""
        labels = outputs.get("labels") or ""
        
        # 출력 타입 검증 (최소 안전장치)
        if not isinstance(title, str) or not isinstance(description_markdown, str) or not isinstance(labels, str):
            failed += 1
            if first_errors < args.print_first_errors:
                first_errors += 1
                eprint(f"[DIFY] [FAIL] issue={issue_key} outputs type invalid: {type(outputs)}")
            continue
        
        # JSONL 행 추가
        rows.append({
            "sonar_issue_key": issue_key,
            "sonar_project_key": project_key,
            "sonar_issue_url": sonar_issue_url,
            "title": title,
            "description_markdown": description_markdown,
            "labels": labels,
        })
        
        analyzed += 1
        
        # 중간 저장 (실패 시에도 이미 분석한 결과 보존)
        write_jsonl(args.output, rows)
    
    # 최종 결과 출력
    print(f"[OK] analyzed: {analyzed}, failed: {failed}, output={args.output}")
    
    # 실패가 1개라도 있으면 exit code 2로 파이프라인 실패 처리
    if failed > 0:
        sys.exit(2)

if __name__ == "__main__":
    main()
```

### 4.6 `gitlab_issue_creator.py` (이슈 등록 및 중복 방지)

```python
#!/usr/bin/env python3
# ============================================================================
# gitlab_issue_creator.py
# 목적: 
#   - LLM 분석 결과를 GitLab Issue로 등록
#   - 중복 방지 메커니즘 (Label 기반)
#   - 생성/스킵/실패 결과를 JSON으로 저장
# 
# 주요 특징:
#   - sonar_issue_key 기반 고유 Label 생성
#   - 동일 Label을 가진 열린(opened) 이슈가 있으면 스킵
#   - Title/Description 길이 제한 처리
# ============================================================================
import argparse
import json
import sys
import time
import urllib.parse
from typing import Dict, Any, List, Tuple
import requests

def eprint(*args):
    """
    표준 에러 출력 (stderr)
    
    Args:
        *args: 출력할 내용
    """
    print(*args, file=sys.stderr)

def read_jsonl(path: str) -> List[Dict[str, Any]]:
    """
    JSONL 파일을 읽어 딕셔너리 리스트로 반환한다.
    
    Args:
        path: JSONL 파일 경로
    
    Returns:
        딕셔너리 리스트 (각 줄이 하나의 딕셔너리)
    """
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                rows.append(json.loads(t))
    return rows

def write_json(path: str, obj: Dict[str, Any]) -> None:
    """
    JSON 파일로 저장한다.
    
    Args:
        path: 출력 파일 경로
        obj: 저장할 딕셔너리
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def gitlab_headers(token: str) -> Dict[str, str]:
    """
    GitLab API 인증 헤더를 생성한다.
    
    Args:
        token: GitLab Personal Access Token
    
    Returns:
        HTTP 헤더 딕셔너리
    """
    # GitLab PAT는 PRIVATE-TOKEN 헤더가 가장 단순하다
    return {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }

def project_id_from_path(project_path: str) -> str:
    """
    GitLab 프로젝트 경로를 URL 인코딩한다.
    GitLab API에서 project id 자리에 URL-encoded full path를 허용한다.
    
    Args:
        project_path: 프로젝트 경로 (예: root/dscore-ttc-sample)
    
    Returns:
        URL 인코딩된 프로젝트 경로
    """
    return urllib.parse.quote(project_path, safe="")

def gitlab_get(session: requests.Session, api_base: str, token: str, path: str, params: Dict[str, Any]) -> Any:
    """
    GitLab API GET 요청을 수행한다.
    
    Args:
        session: requests.Session 객체
        api_base: GitLab API 베이스 URL (예: http://gitlab:8929/api/v4)
        token: Personal Access Token
        path: API 엔드포인트 경로
        params: 쿼리 파라미터
    
    Returns:
        JSON 응답 (딕셔너리 또는 리스트)
    
    Raises:
        HTTPError: HTTP 4xx/5xx 오류 시
    """
    url = api_base.rstrip("/") + path
    r = session.get(url, headers=gitlab_headers(token), params=params, timeout=60)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        eprint(f"[GITLAB] [ERROR] GET {url} HTTP {r.status_code}: {r.text[:2000]}")
        r.raise_for_status()
    
    return r.json()

def gitlab_post(session: requests.Session, api_base: str, token: str, path: str, payload: Dict[str, Any]) -> Any:
    """
    GitLab API POST 요청을 수행한다.
    
    Args:
        session: requests.Session 객체
        api_base: GitLab API 베이스 URL
        token: Personal Access Token
        path: API 엔드포인트 경로
        payload: 요청 본문 (JSON)
    
    Returns:
        JSON 응답 (딕셔너리)
    
    Raises:
        HTTPError: HTTP 4xx/5xx 오류 시
    """
    url = api_base.rstrip("/") + path
    r = session.post(url, headers=gitlab_headers(token), data=json.dumps(payload), timeout=60)
    
    # HTTP 오류 처리
    if r.status_code >= 400:
        eprint(f"[GITLAB] [ERROR] POST {url} HTTP {r.status_code}: {r.text[:2000]}")
        r.raise_for_status()
    
    return r.json()

def extract_key_label(labels_csv: str) -> str:
    """
    labels CSV에서 sonar_issue_key- 로 시작하는 라벨을 추출한다.
    중복 방지를 위한 고유 식별자 역할을 한다.
    
    Args:
        labels_csv: CSV 형식의 라벨 문자열 (예: "sonarqube,sonar_issue_key-ABC123")
    
    Returns:
        sonar_issue_key- 로 시작하는 라벨 (없으면 빈 문자열)
    """
    labels = []
    for x in (labels_csv or "").split(","):
        t = x.strip()
        if t:
            labels.append(t)
    
    # sonar_issue_key- 로 시작하는 라벨 찾기
    for lb in labels:
        if lb.startswith("sonar_issue_key-"):
            return lb
    
    return ""

def truncate(s: str, max_len: int) -> str:
    """
    문자열을 최대 길이로 자른다.
    
    Args:
        s: 원본 문자열
        max_len: 최대 길이
    
    Returns:
        잘린 문자열 (길이 초과 시 끝에 ... 추가)
    """
    if s is None:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "..."

def exists_issue_with_label(session: requests.Session, api_base: str, token: str, project_id: str, label: str) -> bool:
    """
    GitLab에서 특정 라벨을 가진 열린(opened) 이슈가 존재하는지 확인한다.
    
    Args:
        session: requests.Session 객체
        api_base: GitLab API 베이스 URL
        token: Personal Access Token
        project_id: 프로젝트 ID (URL 인코딩됨)
        label: 검색할 라벨
    
    Returns:
        해당 라벨을 가진 열린 이슈가 존재하면 True
    """
    if not label:
        return False
    
    # GitLab Issues API로 라벨 필터 조회
    # state=opened: 열린 이슈만
    # labels=<label>: 특정 라벨을 가진 이슈
    # per_page=1: 1개만 조회 (존재 여부만 확인)
    data = gitlab_get(
        session, api_base, token,
        f"/projects/{project_id}/issues",
        {
            "state": "opened",
            "labels": label,
            "per_page": 1,
            "page": 1,
        }
    )
    
    # 리스트가 비어있지 않으면 이슈가 존재한다
    return isinstance(data, list) and len(data) > 0

def main():
    """
    LLM 분석 결과를 GitLab Issue로 등록한다.
    중복 방지를 위해 sonar_issue_key 기반 라벨을 사용한다.
    
    Usage:
        python3 gitlab_issue_creator.py \
            --gitlab-api-base http://gitlab:8929/api/v4 \
            --gitlab-token <TOKEN> \
            --project-path root/dscore-ttc-sample \
            --input llm_analysis.jsonl \
            --output gitlab_issues_created.json
    """
    # 명령행 인자 정의
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-api-base", required=True)
    ap.add_argument("--gitlab-token", required=True)
    ap.add_argument("--project-path", required=True, 
                    help="GitLab project path (e.g., root/dscore-ttc-sample)")
    ap.add_argument("--input", required=True, 
                    help="Input JSONL file (from dify_sonar_issue_analyzer.py)")
    ap.add_argument("--output", required=True, 
                    help="Output JSON file")
    ap.add_argument("--fail-on-any-failed", action="store_true",
                    help="Exit with code 2 if any issue creation failed")
    args = ap.parse_args()
    
    # JSONL 파일 읽기
    rows = read_jsonl(args.input)
    
    session = requests.Session()
    pid = project_id_from_path(args.project_path)
    
    created = []   # 생성된 이슈 목록
    skipped = []   # 중복으로 스킵된 이슈 목록
    failed = []    # 실패한 이슈 목록
    
    # 각 행을 순회하며 GitLab Issue 생성
    for r in rows:
        sonar_issue_key = r.get("sonar_issue_key") or ""
        title = r.get("title") or ""
        desc = r.get("description_markdown") or ""
        labels_csv = r.get("labels") or ""
        
        # 방어적 truncate (GitLab 제한 대비)
        # Title: 240자 제한
        # Description: 200,000자 제한
        title = truncate(str(title), 240)
        desc = str(desc)
        if len(desc) > 200000:
            desc = desc[:199999] + "\n...\n_truncated_\n"
        
        # 중복 방지용 고유 라벨 추출
        key_label = extract_key_label(labels_csv)
        
        try:
            # 중복 체크: 동일 라벨을 가진 열린 이슈가 있으면 스킵
            if key_label and exists_issue_with_label(session, args.gitlab_api_base, args.gitlab_token, pid, key_label):
                skipped.append({
                    "sonar_issue_key": sonar_issue_key,
                    "reason": "dedup_label_exists",
                    "label": key_label
                })
                continue
            
            # GitLab Issue 생성
            payload = {
                "title": title,
                "description": desc,
                "labels": labels_csv,
            }
            
            out = gitlab_post(session, args.gitlab_api_base, args.gitlab_token, 
                             f"/projects/{pid}/issues", payload)
            
            # 생성 성공
            created.append({
                "sonar_issue_key": sonar_issue_key,
                "issue_iid": out.get("iid"),      # Internal ID (프로젝트 내 번호)
                "issue_id": out.get("id"),        # Global ID
                "web_url": out.get("web_url"),    # 브라우저 URL
            })
            
        except Exception as ex:
            # 생성 실패
            failed.append({
                "sonar_issue_key": sonar_issue_key,
                "title": truncate(title, 120),
                "err": str(ex),
            })
    
    # 결과 JSON 구성
    result = {
        "generated_at": int(time.time()),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "counts": {
            "created": len(created),
            "skipped": len(skipped),
            "failed": len(failed),
        },
    }
    
    # JSON 파일로 저장
    write_json(args.output, result)
    
    print(f"[OK] created={len(created)} skipped={len(skipped)} failed={len(failed)} output={args.output}")
    
    # --fail-on-any-failed 옵션이 있고 실패가 1개라도 있으면 exit code 2
    if args.fail_on_any_failed and len(failed) > 0:
        sys.exit(2)

if __name__ == "__main__":
    main()
```

### 4.7 `domain_knowledge_builder.py` (웹 수집 및 정제)

```python
#!/usr/bin/env python3
# ============================================================================
# domain_knowledge_builder.py
# 목적: 
#   - Crawl4AI로 웹사이트 콘텐츠 수집
#   - BeautifulSoup로 HTML 정제 (메뉴, 사이드바, URL 노이즈 제거)
#   - 깨끗한 Markdown 파일로 저장
# 
# 주요 특징:
#   - Phase 1: 루트 URL에서 내부 링크 수집
#   - Phase 2: 각 URL 방문하여 본문만 추출
#   - URL, 각주, 위키 편집 링크 등 완전 제거
#   - 최대 50개 페이지로 제한 (안전장치)
# ============================================================================
import asyncio
import os
import sys
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

# ============================================================================
# 출력 디렉터리
# ============================================================================
RESULT_DIR = "/var/knowledges/docs/result"

def log(msg: str) -> None:
    """
    Jenkins Console Output에서 진행 로그를 명확히 출력한다.
    
    Args:
        msg: 출력할 메시지
    """
    print(msg, flush=True)

def refine_content(html_content: str) -> str:
    """
    메뉴, 사이드바, URL 정보를 완벽히 소거하는 정제 함수.
    
    정제 과정:
    1. 본문 후보 영역 탐색 (div.mw-parser-output, article, .post-content, main, #content)
    2. UI 노이즈 제거 (nav, footer, aside, sidebar, menu 등)
    3. URL 및 각주 정보 완전 삭제
       - [문구](URL) 형태를 문구로만 변환
       - 일반 URL 제거
       - 위키 각주 제거
    4. 불필요한 개행 정리
    
    Args:
        html_content: 원본 HTML 문자열
    
    Returns:
        정제된 텍스트 (Markdown 본문으로 사용 가능)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # ========================================================================
    # 1. 본문 후보 영역 탐색
    # - 위키피디아: div.mw-parser-output
    # - 일반 블로그: article, .post-content, main, #content
    # ========================================================================
    content_area = None
    for selector in ['div.mw-parser-output', 'article', '.post-content', 'main', '#content']:
        content_area = soup.select_one(selector)
        if content_area:
            break
    
    # 후보 영역을 찾지 못하면 body 전체 사용
    if not content_area:
        content_area = soup.body
    
    if not content_area:
        return ""
    
    # ========================================================================
    # 2. UI 노이즈 제거
    # - nav, footer, aside: 네비게이션 및 푸터
    # - .sidebar, .menu: 사이드바 및 메뉴
    # - script, style: 스크립트 및 스타일
    # - .mw-editsection, .navbox: 위키피디아 전용 요소
    # ========================================================================
    for tag in content_area.select('nav, footer, aside, .sidebar, .menu, script, style, .mw-editsection, .navbox'):
        tag.decompose()
    
    # 텍스트 추출 (개행으로 구분)
    text = content_area.get_text(separator='\n')
    
    # ========================================================================
    # 3. URL 및 각주 정보 완전 삭제
    # - [문구](URL) -> 문구
    # - http://... 또는 https://... -> 제거
    # - [1], [2], [편집] -> 제거
    # ========================================================================
    # [문구](URL) 형태를 문구로만 변환
    text = re.sub(r'\[([^\]]+)\]\(https?://\S+\)', r'\1', text)
    
    # 일반 URL 제거
    text = re.sub(r'https?://\S+', '', text)
    
    # 위키 각주 및 편집 링크 제거
    text = re.sub(r'\[\d+\]|\[편집\]', '', text)
    
    # ========================================================================
    # 4. 불필요한 개행 정리
    # - 연속된 개행을 2개로 통일
    # ========================================================================
    return re.sub(r'\n\s*\n', '\n\n', text).strip()

async def build_knowledge(root_url: str) -> None:
    """
    웹 지식 수집 및 정제 메인 함수.
    
    Phase 1: URL 수집
    - 루트 URL에서 내부 링크를 수집한다.
    - 동일 도메인 내의 링크만 수집한다.
    - action 파라미터가 있는 URL은 제외한다.
    
    Phase 2: 정밀 수집 및 저장
    - 각 URL을 방문하여 HTML을 수집한다.
    - refine_content로 깨끗한 텍스트를 추출한다.
    - 200자 미만의 짧은 콘텐츠는 제외한다.
    - 안전을 위해 최대 50개 페이지로 제한한다.
    
    Args:
        root_url: 수집 시작 URL (예: https://techblog.woowahan.com/)
    """
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    # Crawl4AI AsyncWebCrawler 컨텍스트 시작
    async with AsyncWebCrawler() as crawler:
        log(f"[Crawl] Phase 1: URL 수집 시작 - {root_url}")
        
        # ====================================================================
        # Phase 1: URL 수집
        # ====================================================================
        # 루트 URL 크롤링
        result = await crawler.arun(url=root_url)
        
        # 도메인 추출 (예: techblog.woowahan.com)
        base_domain = urlparse(root_url).netloc
        
        # 내부 링크만 수집
        # - base_domain이 URL에 포함되어야 함
        # - action= 파라미터가 있는 URL 제외 (위키 편집 링크 등)
        urls = {
            urljoin(root_url, l['href']) 
            for l in result.links.get("internal", []) 
            if base_domain in urljoin(root_url, l['href']) and "action=" not in l['href']
        }
        
        # 루트 URL도 포함
        urls.add(root_url)
        
        log(f"[Crawl] 수집된 URL 개수: {len(urls)}")
        
        # ====================================================================
        # Phase 2: 정밀 수집 및 저장 (최대 50개 페이지)
        # ====================================================================
        log("[Crawl] Phase 2: 콘텐츠 수집 및 정제 시작")
        
        for url in list(urls)[:50]:  # 안전장치: 최대 50개
            try:
                # URL 크롤링 (캐시 비활성화)
                res = await crawler.arun(url=url, bypass_cache=True)
                
                if res.success and res.html:
                    # HTML 정제
                    clean_text = refine_content(res.html)
                    
                    # 너무 짧은 콘텐츠는 제외
                    if len(clean_text) < 200:
                        log(f"[Skip] {url} (too short)")
                        continue
                    
                    # 파일명 안전하게 생성
                    # URL을 파일명으로 변환 (/, . 등을 _로 대체)
                    safe_name = url.split("//")[-1].replace("/", "_").replace(".", "_")[:100]
                    output_path = Path(RESULT_DIR) / f"web_{safe_name}.md"
                    
                    # Markdown 형식으로 저장
                    # 제목: Source URL
                    # 본문: 정제된 텍스트
                    output_path.write_text(
                        f"# Source: {url}\n\n{clean_text}", 
                        encoding="utf-8"
                    )
                    
                    log(f"[Success] {url}")
                else:
                    log(f"[Fail] {url} (fetch failed)")
                    
            except Exception as e:
                log(f"[Error] {url} - {str(e)}")
        
        log("[Crawl] 웹 지식 수집 완료")

if __name__ == "__main__":
    """
    사용 방법:
        python3 domain_knowledge_builder.py <ROOT_URL>
    
    예시:
        python3 domain_knowledge_builder.py https://techblog.woowahan.com/
    """
    if len(sys.argv) > 1:
        asyncio.run(build_knowledge(sys.argv[1]))
    else:
        print("Usage: domain_knowledge_builder.py <ROOT_URL>")
        sys.exit(1)
```

---

## 5. Dify Workflow 구성 절차 (상세)

### 5.1 Workflow 개요

**목표:**

SonarQube 이슈를 입력받아 LLM이 원인 분석 및 해결 방안을 작성하는 Workflow를 구성한다.

**입력 변수 (7개 고정):**

1. `sonar_issue_json` - 이슈 전체 JSON (severity, message 등)
2. `sonar_rule_json` - 룰 상세 JSON (설명, 예제 코드 등)
3. `code_snippet` - 이슈 발생 코드 스니펫 (라인 번호 포함)
4. `sonar_issue_url` - SonarQube UI 링크
5. `sonar_issue_key` - 이슈 고유 키
6. `sonar_project_key` - 프로젝트 키
7. `kb_query` - Knowledge Base 검색용 키워드

**출력 변수 (3개 고정):**

1. `title` - GitLab Issue 제목
2. `description_markdown` - GitLab Issue 본문 (Markdown)
3. `labels` - GitLab Issue 라벨 (CSV, 중복 방지용 key 포함)

### 5.2 Workflow 노드 구성

**권장 구성:**

1. **Start (시작)** - 입력 변수 7개 정의
2. **Knowledge Retrieval (지식 검색)** - kb_query로 Dify Dataset 검색
3. **LLM (원인 분석)** - 이슈 + 룰 + 스니펫 + 검색 결과 → 분석
4. **Code (후처리)** - Title/Description/Labels 정규화
5. **End (종료)** - 출력 변수 3개 반환

### 5.3 Start 노드 설정

**입력 변수 정의:**

| 변수명 | 타입 | 설명 |
| --- | --- | --- |
| sonar_issue_json | String | 이슈 전체 JSON |
| sonar_rule_json | String | 룰 상세 JSON |
| code_snippet | String | 코드 스니펫 |
| sonar_issue_url | String | SonarQube UI 링크 |
| sonar_issue_key | String | 이슈 키 (중복 방지용) |
| sonar_project_key | String | 프로젝트 키 |
| kb_query | String | Knowledge Base 검색 키워드 |

### 5.4 Knowledge Retrieval 노드 설정

**Dataset 선택:**

* Job #1, #4, #6으로 업로드한 Dataset을 선택한다.
* 문서, 코드, 웹 지식을 모두 검색 대상으로 한다.

**Query 설정:**

```
{{#1734567890.kb_query#}}
```

**Retrieval Settings:**

* Top K: 3 (상위 3개 결과 반환)
* Score Threshold: 0.3 (관련성 임계값)
* Rerank Model: (선택사항) 활성화 시 정확도 향상

### 5.5 LLM 노드 설정

**Model 선택:**

* Claude Sonnet 4.5 (추천)
* GPT-4o
* 또는 로컬 Ollama 모델

**System Prompt (예시):**

```
You are a senior software engineer analyzing code quality issues.

Your task:
1. Analyze the SonarQube issue using the provided rule description, code snippet, and knowledge base context.
2. Explain the root cause in clear, actionable language.
3. Provide a specific solution with code examples if applicable.
4. Format the output in Markdown suitable for a GitLab issue.

Guidelines:
- Be concise but thorough
- Use Korean language
- Include SonarQube issue link at the bottom
- Add relevant labels (comma-separated)
```

**User Prompt (예시):**

```
# SonarQube Issue Analysis

## Issue Information
{{#1734567890.sonar_issue_json#}}

## Rule Description
{{#1734567890.sonar_rule_json#}}

## Code Snippet
```
{{#1734567890.code_snippet#}}
```

## Related Knowledge
{{#1734567891.result#}}

## SonarQube Link
{{#1734567890.sonar_issue_url#}}

---

Please analyze this issue and provide:
1. Title: Brief, actionable title (max 100 chars)
2. Description: Detailed analysis in Markdown format
3. Labels: Comma-separated labels including "sonarqube,sonar_issue_key-{{#1734567890.sonar_issue_key#}}"
```

**Output Variables:**

LLM 응답을 파싱하여 다음 변수를 추출한다.

* `llm_response` - 전체 응답 텍스트

### 5.6 Code 노드 설정 (후처리)

**목적:**

LLM 응답에서 Title, Description, Labels를 정규 표현식 또는 JSON 파싱으로 추출한다.

**Python 코드 예시:**

```python
import json
import re

def main(llm_response: str, sonar_issue_key: str) -> dict:
    """
    LLM 응답을 파싱하여 GitLab Issue 필드 추출
    
    Args:
        llm_response: LLM 전체 응답
        sonar_issue_key: SonarQube 이슈 키
    
    Returns:
        {
            "title": str,
            "description_markdown": str,
            "labels": str
        }
    """
    # 기본값
    title = "Code Quality Issue"
    description = llm_response
    labels = f"sonarqube,sonar_issue_key-{sonar_issue_key}"
    
    # Title 추출 (## Title 또는 **Title:** 패턴)
    title_match = re.search(r'(?:##\s*Title|Title:)\s*(.+)', llm_response)
    if title_match:
        title = title_match.group(1).strip()[:240]
    
    # Description 추출 (## Description 이후 전체)
    desc_match = re.search(r'## Description\s+(.*)', llm_response, re.DOTALL)
    if desc_match:
        description = desc_match.group(1).strip()
    
    # Labels 추출 (## Labels 또는 **Labels:** 패턴)
    labels_match = re.search(r'(?:##\s*Labels|Labels:)\s*(.+)', llm_response)
    if labels_match:
        labels = labels_match.group(1).strip()
    
    # sonar_issue_key 라벨이 없으면 강제 추가
    if f"sonar_issue_key-{sonar_issue_key}" not in labels:
        labels += f",sonar_issue_key-{sonar_issue_key}"
    
    return {
        "title": title,
        "description_markdown": description,
        "labels": labels
    }
```

### 5.7 End 노드 설정

**출력 변수 매핑:**

| 출력 변수 | 값 |
| --- | --- |
| title | {{#1734567893.title#}} |
| description_markdown | {{#1734567893.description_markdown#}} |
| labels | {{#1734567893.labels#}} |

### 5.8 Workflow 테스트

**테스트 입력 예시:**

```json
{
  "sonar_issue_json": "{\"key\":\"AY123\",\"severity\":\"MAJOR\",\"message\":\"Remove this unused variable\"}",
  "sonar_rule_json": "{\"key\":\"java:S1481\",\"name\":\"Unused local variables should be removed\"}",
  "code_snippet": "   10: public void calculate() {\n>> 11:   int unused = 5;\n   12:   return;\n   13: }",
  "sonar_issue_url": "http://localhost:9000/project/issues?id=test&issues=AY123",
  "sonar_issue_key": "AY123",
  "sonar_project_key": "test-project",
  "kb_query": "java:S1481 unused variable"
}
```

**기대 출력:**

```json
{
  "title": "[MAJOR] 사용하지 않는 지역 변수 제거 필요",
  "description_markdown": "## 분석\n변수 `unused`가 선언되었으나 사용되지 않습니다...",
  "labels": "sonarqube,java,code-smell,sonar_issue_key-AY123"
}
```

---

## 6. Jenkins Pipeline 구성 (6개 Job, 상세 주석)

파일이 너무 커서 섹션 6-9는 다음 파일에 계속 작성하겠습니다.
### 6.1 Job #1: DSCORE-Knowledge-Sync (문서형 Dataset)

**목적:**

로컬 문서 (PDF, DOCX, XLSX, TXT, PPTX)를 Markdown/PDF로 변환하고 Dify 문서형 Dataset에 업로드한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    // 환경 변수 정의
    // - SCRIPTS_DIR: Python 스크립트 경로 (컨테이너 기준)
    // - DOC_FORM: Dify Dataset 문서 형식 (text_model = 문서형)
    // - DOC_LANGUAGE: 문서 언어 (Korean)
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'text_model'
        DOC_LANGUAGE = 'Korean'
    }
    
    stages {
        // ====================================================================
        // Stage 1: 로컬 문서 변환
        // - /var/knowledges/docs/org의 원본 문서를 변환
        // - 변환 결과는 /var/knowledges/docs/result에 저장
        // - doc_processor.py의 convert 명령 실행
        // ====================================================================
        stage('1. Convert Documents') {
            steps {
                echo "[Convert] 로컬 문서 변환 시작"
                sh "python3 ${SCRIPTS_DIR}/doc_processor.py convert"
            }
        }
        
        // ====================================================================
        // Stage 2: Dify 업로드
        // - /var/knowledges/docs/result의 .md와 .pdf 파일을 업로드
        // - Jenkins Credentials에서 API Key와 Dataset ID를 로드
        // - doc_processor.py의 upload 명령 실행
        // ====================================================================
        stage('2. Upload to Dify') {
            steps {
                withCredentials([
                    string(credentialsId: 'dify-knowledge-key', variable: 'DIFY_API_KEY'),
                    string(credentialsId: 'dify-dataset-id', variable: 'DIFY_DATASET_ID')
                ]) {
                    echo "[Upload] Dify 지식베이스 전송 시작"
                    sh '''
                    set -e
                    # doc_processor.py 규격:
                    # upload <API_KEY> <DATASET_ID> <doc_form> <doc_language>
                    python3 ${SCRIPTS_DIR}/doc_processor.py upload "$DIFY_API_KEY" "$DIFY_DATASET_ID" "$DOC_FORM" "$DOC_LANGUAGE"
                    echo "[Upload] 완료"
                    '''
                }
            }
        }
    }
}
```

**사용 방법:**

1. `/var/knowledges/docs/org`에 PDF, DOCX, XLSX 파일 배치
2. Jenkins에서 "Build Now" 실행
3. Console Output에서 변환 및 업로드 로그 확인

### 6.2 Job #2: DSCORE-Knowledge-Sync-QA (Q&A형 Dataset)

**목적:**

Q&A 형식 문서를 Dify Q&A형 Dataset에 업로드한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    // Q&A형 Dataset 전용 환경 변수
    // - DOC_FORM: qa_model (Q&A형)
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'qa_model'
        DOC_LANGUAGE = 'Korean'
    }
    
    stages {
        stage('1. Convert Documents') {
            steps {
                echo "[Convert] Q&A 문서 변환 시작"
                sh "python3 ${SCRIPTS_DIR}/doc_processor.py convert"
            }
        }
        
        stage('2. Upload to Dify QA Dataset') {
            steps {
                // Q&A형 Dataset 전용 Credentials 사용
                // - dify-knowledge-key-qa: Q&A Dataset API Key
                // - dify-dataset-id-qa: Q&A Dataset UUID
                withCredentials([
                    string(credentialsId: 'dify-knowledge-key-qa', variable: 'DIFY_API_KEY'),
                    string(credentialsId: 'dify-dataset-id-qa', variable: 'DIFY_DATASET_ID')
                ]) {
                    echo "[Upload] Q&A Dataset 전송 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/doc_processor.py upload "$DIFY_API_KEY" "$DIFY_DATASET_ID" "$DOC_FORM" "$DOC_LANGUAGE"
                    echo "[Upload] 완료"
                    '''
                }
            }
        }
    }
}
```

### 6.3 Job #3: DSCORE-Knowledge-Sync-Vision (이미지 분석)

**목적:**

이미지 파일을 Llama 3.2 Vision 모델로 분석하고 결과를 Dify Dataset에 업로드한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'text_model'
        DOC_LANGUAGE = 'Korean'
    }
    
    stages {
        // ====================================================================
        // Stage 1: 이미지 Vision 분석
        // - /var/knowledges/docs/org의 이미지 파일을 분석
        // - Ollama API (host.docker.internal:11434) 호출
        // - 분석 결과를 /var/knowledges/docs/result에 .md로 저장
        // ====================================================================
        stage('1. Vision Analysis') {
            steps {
                echo "[Vision] 이미지 분석 시작"
                sh "python3 ${SCRIPTS_DIR}/vision_processor.py"
            }
        }
        
        stage('2. Upload Vision Results') {
            steps {
                withCredentials([
                    string(credentialsId: 'dify-knowledge-key', variable: 'DIFY_API_KEY'),
                    string(credentialsId: 'dify-dataset-id', variable: 'DIFY_DATASET_ID')
                ]) {
                    echo "[Upload] Vision 분석 결과 전송 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/doc_processor.py upload "$DIFY_API_KEY" "$DIFY_DATASET_ID" "$DOC_FORM" "$DOC_LANGUAGE"
                    echo "[Upload] 완료"
                    '''
                }
            }
        }
    }
}
```

**사전 요구사항:**

* 호스트에서 `ollama serve` 실행 중
* `ollama pull llama3.2-vision` 명령으로 모델 설치 완료

### 6.4 Job #4: DSCORE-Code-Knowledge-Sync (코드 컨텍스트)

**목적:**

Git 레포지토리의 디렉터리 구조와 주요 파일 내용을 추출하여 Dify Dataset에 업로드한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    // 파라미터 정의
    // - REPO_URL: Git 레포지토리 URL (예: https://github.com/user/repo.git)
    parameters {
        string(name: 'REPO_URL', defaultValue: '', description: 'Git repository URL')
    }
    
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        WORKSPACE_CODES = '/var/knowledges/codes'
        RESULT_DIR = '/var/knowledges/docs/result'
        DOC_FORM = 'text_model'
        DOC_LANGUAGE = 'Korean'
    }
    
    stages {
        // ====================================================================
        // Stage 1: Git Clone
        // - REPO_URL이 빈 문자열이면 스킵
        // - /var/knowledges/codes 디렉터리에 클론
        // - 이미 존재하면 삭제 후 재클론
        // ====================================================================
        stage('1. Git Clone') {
            when { expression { params.REPO_URL != '' } }
            steps {
                echo "[Clone] Git 레포지토리 클론 시작: ${params.REPO_URL}"
                sh '''
                set -e
                # 기존 디렉터리 삭제
                rm -rf ${WORKSPACE_CODES}/*
                # Git 클론
                git clone "${REPO_URL}" ${WORKSPACE_CODES}/repo
                echo "[Clone] 완료"
                '''
            }
        }
        
        // ====================================================================
        // Stage 2: 코드 컨텍스트 빌드
        // - repo_context_builder.py 실행
        // - 디렉터리 트리 + README/package.json 등 주요 파일 내용 추출
        // - context.md 파일 생성
        // ====================================================================
        stage('2. Build Context') {
            steps {
                echo "[Build] 레포지토리 컨텍스트 생성 시작"
                sh '''
                set -e
                python3 ${SCRIPTS_DIR}/repo_context_builder.py \
                    --repo_root ${WORKSPACE_CODES}/repo \
                    --out ${RESULT_DIR}/context.md
                echo "[Build] 완료"
                '''
            }
        }
        
        // ====================================================================
        // Stage 3: Dify 업로드
        // - context.md 파일을 Dify Dataset에 업로드
        // - AI가 프로젝트 구조와 설정을 학습
        // ====================================================================
        stage('3. Upload Context') {
            steps {
                withCredentials([
                    string(credentialsId: 'dify-knowledge-key', variable: 'DIFY_API_KEY'),
                    string(credentialsId: 'dify-dataset-id', variable: 'DIFY_DATASET_ID')
                ]) {
                    echo "[Upload] 코드 컨텍스트 전송 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/doc_processor.py upload "$DIFY_API_KEY" "$DIFY_DATASET_ID" "$DOC_FORM" "$DOC_LANGUAGE"
                    echo "[Upload] 완료"
                    '''
                }
            }
        }
    }
}
```

### 6.5 Job #5: DSCORE-Quality-Issue-Workflow (정적 분석 자동화)

**목적:**

SonarQube 정적 분석 결과를 LLM으로 분석하고 GitLab Issue로 자동 등록한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    // 파라미터 정의 (5개)
    parameters {
        // SonarQube 프로젝트 키 (필수)
        string(name: 'SONAR_PROJECT_KEY', defaultValue: 'dscore-ttc-sample', 
               description: 'SonarQube project key')
        // GitLab 프로젝트 경로 (필수)
        string(name: 'GITLAB_PROJECT_PATH', defaultValue: 'root/dscore-ttc-sample', 
               description: 'GitLab project path (e.g., root/dscore-ttc-sample)')
        // 심각도 필터 (선택)
        string(name: 'SEVERITIES', defaultValue: 'BLOCKER,CRITICAL,MAJOR', 
               description: 'Comma-separated severities to filter')
        // 상태 필터 (선택)
        string(name: 'STATUSES', defaultValue: 'OPEN,REOPENED', 
               description: 'Comma-separated statuses to filter')
        // 최대 분석 이슈 수 (안전장치)
        string(name: 'MAX_ISSUES', defaultValue: '10', 
               description: 'Maximum issues to analyze (0 = all)')
    }
    
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        WORK_DIR = '/var/jenkins_home/workspace/quality-workflow'
        
        // SonarQube URL (컨테이너 내부 접근용 / 브라우저 접근용)
        SONAR_HOST_URL = 'http://sonarqube:9000'
        SONAR_PUBLIC_URL = 'http://localhost:9000'
        
        // GitLab API (컨테이너 내부 접근용)
        GITLAB_API_BASE = 'http://gitlab:8929/api/v4'
        
        // Dify API (컨테이너 내부 접근용)
        DIFY_API_BASE = 'http://api:5001/v1'
        
        // Dify Workflow 사용자 ID
        DIFY_USER = 'jenkins'
    }
    
    stages {
        // ====================================================================
        // Stage 1: 작업 디렉터리 준비
        // - 이전 실행 결과 정리
        // - 출력 파일들을 저장할 디렉터리 생성
        // ====================================================================
        stage('1. Prepare Workspace') {
            steps {
                echo "[Prepare] 작업 디렉터리 초기화"
                sh "mkdir -p ${WORK_DIR} && rm -rf ${WORK_DIR}/*"
            }
        }
        
        // ====================================================================
        // Stage 2: SonarQube 이슈 추출
        // - /api/issues/search 엔드포인트로 이슈 수집
        // - 페이징 처리로 모든 이슈 수집 (최대 500개/페이지)
        // - sonar_issues.json 파일로 저장
        // ====================================================================
        stage('2. Export SonarQube Issues') {
            steps {
                withCredentials([
                    string(credentialsId: 'sonarqube-token', variable: 'SONAR_TOKEN')
                ]) {
                    echo "[Sonar] 이슈 추출 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/sonar_issue_exporter.py \
                        --sonar-host-url "${SONAR_HOST_URL}" \
                        --sonar-public-url "${SONAR_PUBLIC_URL}" \
                        --sonar-token "${SONAR_TOKEN}" \
                        --project-key "${SONAR_PROJECT_KEY}" \
                        --severities "${SEVERITIES}" \
                        --statuses "${STATUSES}" \
                        --output "${WORK_DIR}/sonar_issues.json"
                    '''
                }
            }
        }
        
        // ====================================================================
        // Stage 3: LLM 분석 (Dify Workflow)
        // - 각 이슈마다 Dify Workflow 호출
        // - 룰 정보 + 코드 스니펫 + Knowledge Base 검색
        // - llm_analysis.jsonl 파일로 저장 (한 줄에 하나의 분석 결과)
        // ====================================================================
        stage('3. Analyze with LLM') {
            steps {
                withCredentials([
                    string(credentialsId: 'sonarqube-token', variable: 'SONAR_TOKEN'),
                    string(credentialsId: 'dify-knowledge-key', variable: 'DIFY_API_KEY')
                ]) {
                    echo "[Dify] LLM 분석 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/dify_sonar_issue_analyzer.py \
                        --sonar-host-url "${SONAR_HOST_URL}" \
                        --sonar-public-url "${SONAR_PUBLIC_URL}" \
                        --sonar-token "${SONAR_TOKEN}" \
                        --dify-api-base "${DIFY_API_BASE}" \
                        --dify-api-key "${DIFY_API_KEY}" \
                        --input "${WORK_DIR}/sonar_issues.json" \
                        --output "${WORK_DIR}/llm_analysis.jsonl" \
                        --user "${DIFY_USER}" \
                        --max-issues "${MAX_ISSUES}"
                    '''
                }
            }
        }
        
        // ====================================================================
        // Stage 4: GitLab Issue 생성
        // - llm_analysis.jsonl을 읽어 각 행을 GitLab Issue로 등록
        // - 중복 방지: sonar_issue_key 기반 Label로 기존 이슈 확인
        // - gitlab_issues_created.json 파일로 결과 저장
        // ====================================================================
        stage('4. Create GitLab Issues') {
            steps {
                withCredentials([
                    string(credentialsId: 'gitlab-access-token', variable: 'GITLAB_TOKEN')
                ]) {
                    echo "[GitLab] 이슈 생성 시작"
                    sh '''
                    set -e
                    python3 ${SCRIPTS_DIR}/gitlab_issue_creator.py \
                        --gitlab-api-base "${GITLAB_API_BASE}" \
                        --gitlab-token "${GITLAB_TOKEN}" \
                        --project-path "${GITLAB_PROJECT_PATH}" \
                        --input "${WORK_DIR}/llm_analysis.jsonl" \
                        --output "${WORK_DIR}/gitlab_issues_created.json"
                    '''
                }
            }
        }
    }
}
```

**실행 예시:**

1. Jenkins에서 "Build with Parameters" 클릭
2. SONAR_PROJECT_KEY: `dscore-ttc-sample`
3. GITLAB_PROJECT_PATH: `root/dscore-ttc-sample`
4. SEVERITIES: `BLOCKER,CRITICAL`
5. "Build" 클릭

### 6.6 Job #6: DSCORE-Web-Knowledge-Sync (웹 콘텐츠 수집)

**목적:**

웹사이트 콘텐츠를 수집하고 정제하여 Dify Dataset에 업로드한다.

**Jenkinsfile:**

```groovy
pipeline {
    agent any
    
    // 파라미터 정의
    // - ROOT_URL: 수집 시작 URL (예: https://techblog.woowahan.com/)
    parameters {
        string(name: 'ROOT_URL', defaultValue: '', description: 'Root URL to crawl')
    }
    
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        RESULT_DIR = '/var/knowledges/docs/result'
        DIFY_DOC_FORM = "text_model"
    }
    
    stages {
        // ====================================================================
        // Stage 1: 로컬 문서 변환 및 result 폴더 초기화
        // - 기존 변환 로직 실행
        // - result 폴더의 기존 파일 정리
        // ====================================================================
        stage("1. Local Document Conversion") {
            steps {
                echo "[Convert] 로컬 문서 변환 및 기존 result 폴더 초기화"
                sh "python3 ${SCRIPTS_DIR}/doc_processor.py convert"
            }
        }
        
        // ====================================================================
        // Stage 2: 웹 스크래핑 및 정제
        // - Crawl4AI로 ROOT_URL의 내부 링크 수집
        // - 각 URL 방문하여 본문 추출
        // - URL, 각주, 위키 편집 링크 등 완전 제거
        // - web_*.md 파일로 저장
        // ====================================================================
        stage("2. Web Scraping & Refinement") {
            when { expression { params.ROOT_URL != '' } }
            steps {
                withEnv(["TARGET_URL=${params.ROOT_URL}"]) {
                    echo "[Crawl] 웹 지식 수집 및 URL 노이즈 제거 시작"
                    sh "python3 ${SCRIPTS_DIR}/domain_knowledge_builder.py \"${TARGET_URL}\""
                }
            }
        }
        
        // ====================================================================
        // Stage 3: Manual Approval (수동 승인)
        // - 수집 결과를 수동으로 확인하는 단계
        // - /var/knowledges/docs/result 폴더의 web_*.md 파일 확인
        // - URL 노이즈가 완전히 제거되었는지 검증
        // ====================================================================
        stage("3. Manual Approval") {
            steps {
                script {
                    // 수집된 깨끗한 텍스트를 확인하고 승인하는 단계
                    input message: "지식 정제가 완료되었습니다. /var/knowledges/docs/result 폴더를 확인 후 승인하시겠습니까?", ok: "승인 및 업로드"
                }
            }
        }
        
        // ====================================================================
        // Stage 4: Dify 업로드
        // - web_*.md 파일을 Dify Dataset에 업로드
        // - doc_processor.py의 upload 명령 사용 (기존 .md 업로드 로직)
        // ====================================================================
        stage("4. Final Upload to Dify") {
            steps {
                withCredentials([
                    string(credentialsId: "dify-knowledge-key", variable: "DIFY_API_KEY"),
                    string(credentialsId: "dify-dataset-id", variable: "DIFY_DATASET_ID")
                ]) {
                    echo "[Upload] Dify 지식베이스 전송 시작"
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$DIFY_DATASET_ID\" \"$DIFY_API_KEY\" \"$DIFY_DOC_FORM\""
                }
            }
        }
    }
}
```

**사용 방법:**

1. Jenkins에서 "Build with Parameters" 클릭
2. ROOT_URL: `https://techblog.woowahan.com/`
3. "Build" 클릭
4. Manual Approval 단계에서 `docker exec -it jenkins ls -al /var/knowledges/docs/result` 로 파일 확인
5. URL 노이즈가 제거되었는지 확인 후 "승인 및 업로드" 클릭

---

## 7. 샘플 프로젝트 구성 및 테스트

### 7.1 샘플 Git 레포지토리 생성

**GitLab에서 프로젝트 생성:**

1. GitLab 접속 (`http://localhost:8929`)
2. "New project" -> "Create blank project"
3. Project name: `dscore-ttc-sample`
4. Visibility: Private
5. "Create project" 클릭

**샘플 코드 추가:**

```java
// BadCode.java
public class BadCode {
    public void unusedVariable() {
        int unused = 5;  // SonarQube will detect this
        System.out.println("Hello");
    }
    
    public String nullPointerRisk(String input) {
        return input.toLowerCase();  // Potential NullPointerException
    }
}
```

**Git Push:**

```bash
git init
git add .
git commit -m "Initial commit with code smells"
git remote add origin http://localhost:8929/root/dscore-ttc-sample.git
git push -u origin main
```

### 7.2 SonarQube 프로젝트 생성

1. SonarQube 접속 (`http://localhost:9000`)
2. "Projects" -> "Create Project"
3. Project key: `dscore-ttc-sample`
4. Display name: `DSCORE TTC Sample`
5. "Set Up" 클릭
6. "Locally" 선택
7. Generate Token -> 복사
8. 분석 명령 실행:

```bash
mvn sonar:sonar \
  -Dsonar.projectKey=dscore-ttc-sample \
  -Dsonar.host.url=http://localhost:9000 \
  -Dsonar.login=<YOUR_TOKEN>
```

### 7.3 통합 테스트 시나리오

**시나리오: 문서 업로드 → 코드 분석 → 이슈 자동 등록**

**Step 1: 문서 업로드 (Job #1)**

```bash
# 1. 샘플 문서 배치
docker exec -it jenkins sh -c 'echo "# 프로젝트 가이드\n\n이 프로젝트는..." > /var/knowledges/docs/org/README.md'

# 2. Job #1 실행
# Jenkins UI에서 DSCORE-Knowledge-Sync 실행
```

**Step 2: 코드 컨텍스트 업로드 (Job #4)**

```bash
# Jenkins UI에서 DSCORE-Code-Knowledge-Sync 실행
# REPO_URL: http://gitlab:8929/root/dscore-ttc-sample.git
```

**Step 3: 정적 분석 및 이슈 등록 (Job #5)**

```bash
# Jenkins UI에서 DSCORE-Quality-Issue-Workflow 실행
# SONAR_PROJECT_KEY: dscore-ttc-sample
# GITLAB_PROJECT_PATH: root/dscore-ttc-sample
```

**Step 4: 결과 확인**

1. GitLab Issues 확인: `http://localhost:8929/root/dscore-ttc-sample/-/issues`
2. SonarQube 링크가 포함된 Issue 확인
3. Label에 `sonar_issue_key-*` 포함 확인

---

## 8. 트러블슈팅

### 8.1 문서 변환 관련

**문제: PDF 텍스트가 추출되지 않는다**

**원인:**
- PDF가 이미지 기반 스캔본이다
- 암호화된 PDF다

**해결:**
1. OCR 처리 필요 시 `pytesseract` 추가
2. 암호화 해제 후 재시도

**문제: PPTX -> PDF 변환 실패**

**원인:**
- LibreOffice가 설치되지 않았다
- 폰트 문제

**해결:**
```bash
docker exec -it jenkins which soffice
# 없으면 Dockerfile.jenkins 재빌드
docker compose up -d --build --force-recreate jenkins
```

### 8.2 Dify 업로드 관련

**문제: 400 invalid_param(doc_form mismatch)**

**원인:**
- Dataset의 doc_form과 요청의 doc_form이 다르다

**해결:**
1. Dataset 상세 조회로 doc_form 확인
2. Pipeline 환경 변수 수정 (`DOC_FORM`)

**문제: 401 Unauthorized**

**원인:**
- API Key가 잘못되었다
- API Key가 만료되었다

**해결:**
1. Dify에서 API Key 재발급
2. Jenkins Credentials 업데이트

### 8.3 Vision 분석 관련

**문제: Ollama API 연결 실패**

**원인:**
- Ollama가 실행되지 않았다
- host.docker.internal 접근 불가

**해결:**
```bash
# 호스트에서 Ollama 실행 확인
ps aux | grep ollama
ollama serve

# Jenkins 컨테이너에서 연결 테스트
docker exec -it jenkins curl http://host.docker.internal:11434/api/tags
```

**문제: llama3.2-vision 모델이 없다**

**해결:**
```bash
ollama pull llama3.2-vision
```

### 8.4 SonarQube 관련

**문제: 이슈가 추출되지 않는다**

**원인:**
- 프로젝트 키가 잘못되었다
- SonarQube 분석이 실행되지 않았다

**해결:**
1. SonarQube UI에서 프로젝트 존재 확인
2. 프로젝트 키 정확히 입력 (`대소문자 구분`)

**문제: 코드 스니펫이 빈 문자열이다**

**원인:**
- 이슈에 line 정보가 없다
- 컴포넌트 키가 잘못되었다

**해결:**
- /api/sources/lines 직접 호출하여 응답 확인
- 이슈 JSON의 component, line 필드 확인

### 8.5 GitLab 관련

**문제: 이슈 생성 시 403 Forbidden**

**원인:**
- Personal Access Token 권한 부족
- 프로젝트 접근 권한 없음

**해결:**
1. PAT 재발급 (`api`, `write_repository` 권한 필요)
2. 프로젝트 Visibility 확인

**문제: 중복 방지가 작동하지 않는다**

**원인:**
- Label 형식이 다르다
- 대소문자 문제

**해결:**
```python
# gitlab_issue_creator.py에서 Label 확인
print(f"Checking label: sonar_issue_key-{sonar_issue_key}")
```

### 8.6 웹 스크래핑 관련

**문제: Crawl4AI 설치 오류**

**원인:**
- Playwright 브라우저가 설치되지 않았다
- 시스템 라이브러리 부족

**해결:**
```bash
# Jenkins 컨테이너 내부에서
docker exec -it jenkins python3 -m playwright install --with-deps chromium

# 재빌드
docker compose up -d --build --force-recreate jenkins
```

**문제: 웹 페이지 접속 실패**

**원인:**
- URL이 잘못되었다
- 웹사이트가 크롤링을 차단한다

**해결:**
1. 브라우저에서 URL 직접 확인
2. robots.txt 확인
3. User-Agent 변경 시도

---

## 9. E2E 테스트 자동화 구성 (Playwright + Roo Code)

### 9.1 개요 및 목표

**진짜 목표:**

**실제 개발 중인 프로젝트(웹앱)**의 E2E 테스트를 AI가 자동으로 작성하고 Jenkins CI/CD에 통합한다.

**명확한 구분:**

* ❌ Jenkins/SonarQube 인프라를 테스트하는 것이 아니다
* ✅ 개발 중인 실제 웹앱(예: React, Vue, Next.js 앱)을 테스트한다

**예시:**

* 개발 중인 웹앱: `http://localhost:3000` (React 앱)
* AI에게 명령: "이 웹앱의 로그인 기능 테스트 작성해줘"
* AI 동작: 브라우저로 `http://localhost:3000` 접속 → 화면 분석 → 테스트 코드 생성
* 결과: `tests/login.spec.ts` 파일 생성
* Jenkins 통합: Job #7로 등록하여 CI/CD 파이프라인에서 실행

### 9.2 사전 요구사항

**필수 소프트웨어:**

1. **macOS:** 본 가이드는 macOS 기준
2. **VS Code:** 개발 환경
3. **Node.js:** Playwright 실행 런타임 (18.x 이상)
4. **Ollama + qwen3-coder:30b:** 로컬 AI 모델
5. **개발 중인 웹앱:** 테스트 대상 (예: localhost:3000에서 실행 중)

**Ollama 모델 확인:**

```bash
ollama list | grep qwen3-coder
```

출력 예시:
```
qwen3-coder:30b    ...    30 GB    ...
```

### 9.3 VS Code 및 Node.js 설치

**VS Code 설치:**

1. https://code.visualstudio.com/ 접속
2. macOS용 .zip 다운로드
3. Applications 폴더로 이동
4. VS Code 실행

**Node.js 설치:**

```bash
# 설치 확인
node -v
npm -v

# 미설치 시 https://nodejs.org/ 에서 LTS 버전 설치
```

**권장 버전:**

* Node.js 18.x 이상
* npm 9.x 이상

### 9.4 Roo Code Extension 설치

**9.4.1 Roo Code 설치**

1. VS Code 실행
2. Extensions 탭 (Cmd+Shift+X)
3. "Roo Code" 검색
4. Install 클릭

**중요:**

* 로그인 불필요
* Ollama 로컬 연결만으로 사용 가능

**9.4.2 AI 모델 연결 (qwen3-coder:30b)**

1. 왼쪽 사이드바의 Roo Code 아이콘 클릭
2. 하단 **설정(톱니바퀴)** 클릭
3. 다음과 같이 설정:

| 항목 | 값 |
| --- | --- |
| API Provider | Ollama |
| Base URL | http://localhost:11434 |
| Model ID | qwen3-coder:30b |

4. Done 클릭
5. 테스트: "안녕" 입력하여 응답 확인

### 9.5 Playwright 프로젝트 구성

**9.5.1 테스트 디렉터리 생성**

```bash
# 프로젝트 루트에 test 디렉터리 생성
mkdir -p /Users/luuuuunatic/Developer/dscore-ttc/data/test
cd /Users/luuuuunatic/Developer/dscore-ttc/data/test

# VS Code로 폴더 열기
code .
```

**9.5.2 Playwright 초기화**

VS Code 내장 터미널 (Ctrl + `)에서 실행:

```bash
# Playwright 프로젝트 초기화 (TypeScript)
npm init playwright@latest -- --yes --typescript

# 브라우저 엔진 및 시스템 라이브러리 설치
npx playwright install --with-deps
```

생성된 구조:
```
data/test/
├── package.json
├── playwright.config.ts
├── tests/
│   └── example.spec.ts
└── tests-examples/
```

**9.5.3 playwright.config.ts 수정**

**핵심:** `baseURL`을 **개발 중인 웹앱 주소**로 설정한다.

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  
  // 타임아웃
  timeout: 30000,
  expect: {
    timeout: 5000
  },
  
  // 재시도 (불안정한 테스트 대비)
  retries: 2,
  
  // 병렬 실행 워커
  workers: 1,
  
  // 리포터
  reporter: 'html',
  
  // ⭐ 중요: 개발 중인 웹앱 주소로 설정
  use: {
    baseURL: 'http://localhost:3000',  // 예: React 앱
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
```

### 9.6 Playwright MCP 서버 설정

**왜 필요한가:**

Playwright MCP는 Roo Code(AI)가 **실제 브라우저를 열어** 화면을 분석하고 Selector를 파악할 수 있게 하는 브릿지다.

**9.6.1 MCP 설정 파일 열기**

1. Roo Code 채팅창 우측 상단 **🔌 아이콘** 클릭
2. "Configure MCP Servers" 클릭
3. `roo_mcp_settings.json` 파일 열림

**9.6.2 Playwright MCP 추가**

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"]
    }
  }
}
```

**9.6.3 연결 확인**

1. `roo_mcp_settings.json` 저장
2. VS Code 재시작 (Cmd+Q 후 재실행)
3. Roo Code 채팅창 🔌 아이콘 클릭
4. `playwright` 서버 옆 **초록불** 확인

**연결 실패 시:**

```bash
# 수동 실행 테스트
npx @playwright/mcp@latest

# Node.js 버전 확인 (18.x 이상 필요)
node -v
```

### 9.7 AI 기반 테스트 자동 생성

**9.7.1 개발 중인 웹앱 실행**

먼저 테스트 대상 웹앱을 실행한다.

```bash
# 예: React 앱
cd /path/to/your/webapp
npm run dev
# localhost:3000에서 실행 중
```

**9.7.2 Roo Code에게 테스트 작성 명령**

Roo Code 채팅창에 다음과 같이 입력:

```
Playwright MCP를 사용해서 http://localhost:3000 에 접속해봐.
메인 페이지에 "로그인" 버튼이 있는지 확인하고,
클릭했을 때 /login 페이지로 이동하는지 검증하는
tests/main_page.spec.ts 테스트를 작성해줘.
```

**AI의 작동 과정:**

1. **MCP 권한 요청:** VS Code 상단에 "Allow Roo Code to use Playwright MCP?" 알림
   → **반드시 "Allow" 클릭**
2. **브라우저 실행:** AI가 Chromium 브라우저를 Headless 모드로 실행
3. **화면 분석:** `http://localhost:3000` 접속하여 DOM 구조 분석
4. **Selector 파악:** "로그인" 버튼의 CSS Selector 또는 텍스트 파악
5. **코드 생성:** `tests/main_page.spec.ts` 파일 자동 생성

**생성된 코드 예시:**

```typescript
import { test, expect } from '@playwright/test';

test('메인 페이지 로그인 버튼 테스트', async ({ page }) => {
  // 메인 페이지 접속
  await page.goto('/');
  
  // "로그인" 버튼 확인
  const loginButton = page.getByRole('button', { name: '로그인' });
  await expect(loginButton).toBeVisible();
  
  // 버튼 클릭
  await loginButton.click();
  
  // /login 페이지로 이동 확인
  await expect(page).toHaveURL('/login');
});
```

### 9.8 테스트 실행 및 검증

**9.8.1 UI 모드로 실행**

```bash
npx playwright test --ui
```

**장점:**

* 브라우저 동작을 시각적으로 확인
* 스텝별 재생
* 실패 지점 즉시 파악

**9.8.2 헤드리스 모드로 실행**

```bash
npx playwright test
```

출력 예시:
```
Running 1 test using 1 worker

  ✓  tests/main_page.spec.ts:3:1 › 메인 페이지 로그인 버튼 테스트 (2s)

  1 passed (3s)
```

**9.8.3 HTML 리포트 확인**

```bash
npx playwright show-report
```

브라우저가 열리며 상세 리포트 확인 가능

### 9.9 테스트 실패 시 AI 디버깅

**시나리오: Selector가 변경되어 실패**

1. 테스트 실패 로그:
```
Error: locator.toBeVisible: Timeout 5000ms exceeded.
```

2. 에러 로그 복사

3. Roo Code에게 요청:
```
이 에러 발생했어:
[에러 로그 붙여넣기]

MCP로 다시 http://localhost:3000 접속해서
"로그인" 버튼의 Selector가 바뀌었는지 확인하고 코드 고쳐줘.
```

4. AI가 브라우저를 다시 열어 현재 DOM 분석

5. 수정된 코드 제안:
```typescript
// 변경 전
const loginButton = page.getByRole('button', { name: '로그인' });

// 변경 후 (AI가 새로운 Selector 파악)
const loginButton = page.locator('a[href="/login"]');
```

6. 수정 후 재실행

### 9.10 추가 테스트 케이스 예시

**9.10.1 폼 입력 테스트**

```
MCP로 http://localhost:3000/login 페이지 접속해서
이메일/비밀번호 입력하고 로그인 버튼 클릭하는
tests/login_form.spec.ts 테스트를 작성해줘.
```

**9.10.2 API 응답 대기 테스트**

```
로그인 후 /dashboard 페이지로 이동하고
사용자 이름이 화면에 표시되는지 확인하는
tests/dashboard.spec.ts 테스트를 작성해줘.
```

**9.10.3 모바일 뷰포트 테스트**

```
iPhone 13 화면 크기에서 메뉴가 햄버거 아이콘으로
표시되는지 확인하는 테스트를 작성해줘.
```

### 9.11 Jenkins CI/CD 통합 (선택사항)

생성된 Playwright 테스트를 Jenkins Job으로 등록하여 자동 실행한다.

**Job #7: E2E-Test (신규 Pipeline)**

```groovy
pipeline {
    agent any
    
    environment {
        TEST_DIR = '/Users/luuuuunatic/Developer/dscore-ttc/data/test'
    }
    
    stages {
        stage('E2E Test') {
            steps {
                dir("${TEST_DIR}") {
                    sh '''
                    npm install
                    npx playwright test
                    '''
                }
            }
        }
    }
    
    post {
        always {
            publishHTML([
                allowMissing: false,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: "${TEST_DIR}/playwright-report",
                reportFiles: 'index.html',
                reportName: 'E2E Test Report'
            ])
        }
    }
}
```

**실행 주기:**

* Git Push 후 자동 실행 (Webhook 설정)
* 매일 새벽 3시 자동 실행 (Cron)
* 수동 실행

### 9.12 사후 관리

**테스트 업데이트:**

1. 웹앱 UI 변경 시 Roo Code에게 재분석 요청
2. 새로운 기능 추가 시 추가 테스트 작성
3. 정기적으로 테스트 실행하여 회귀 감지

**권장 테스트 실행 주기:**

* 코드 변경 후: 즉시 실행
* 배포 전: 필수 실행
* 주 1회: 정기 검증

### 9.13 트러블슈팅 (E2E 테스트)

**문제 1: Playwright MCP 초록불이 안 들어온다**

**해결:**
```bash
# Node.js 버전 확인 및 업데이트
node -v  # 18.x 이상 필요

# MCP 수동 실행 테스트
npx -y @playwright/mcp@latest

# VS Code 완전 재시작
```

**문제 2: AI가 브라우저를 열지 못한다**

**해결:**
1. VS Code 상단 알림에서 "Allow" 클릭 확인
2. 브라우저 재설치:
```bash
npx playwright install --with-deps
```

**문제 3: 테스트가 타임아웃으로 실패**

**원인:** 웹앱이 느리거나 네트워크 지연

**해결:**
```typescript
// playwright.config.ts
timeout: 60000,  // 30초 -> 60초로 증가
```

**문제 4: 개발 중인 웹앱이 실행되지 않는다**

**확인:**
```bash
# 웹앱 프로세스 확인
lsof -i :3000

# 웹앱 로그 확인
# (해당 웹앱의 로그 확인 방법 참조)
```

---

**문서 끝**