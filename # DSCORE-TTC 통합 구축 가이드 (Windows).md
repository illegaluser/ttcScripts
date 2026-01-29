# DSCORE-TTC 통합 구축 가이드 (Windows)

---

## 0. 개요 및 문서 범위

이 문서는 **Windows 11 온프레미스 PC** 환경에서 **DevOps + Test Management 시스템**을 처음부터 끝까지 구축하는 완전한 가이드입니다.

### 시스템 구성

**Phase 1: DevOps 인프라 구축 (Section 1-3)**
1.  **WSL 2 기반 Docker Desktop** 위에서 Jenkins, SonarQube, GitLab, Dify 구성
2.  Windows 호스트 파일 수정 및 Docker 네트워크/볼륨 설정
3.  초기 인증 토큰 발급 및 연동

**Phase 2: 지식 관리 자동화 (Section 4-6)**
1.  문서/이미지/코드/웹을 Dify Knowledge Base에 자동 업로드
2.  Jenkins Pipeline으로 지식 동기화 자동화
3.  **Ollama Vision 모델**을 활용한 이미지 분석 자동화

**Phase 3: 품질 분석 자동화 (Section 5-6)**
1.  SonarQube 정적 분석 결과를 Dify Workflow(LLM)로 진단
2.  **상세한 프롬프트 엔지니어링**을 통해 원인 분석 및 해결책 제시
3.  진단 결과를 GitLab Issue로 자동 등록 (중복 방지 포함)

**Phase 4: E2E 테스트 자동화 (Section 9-11)**
1.  **Method A:** Playwright + Roo Code (개발자 중심, 코드 생성)
2.  **Method B:** Zero-Touch QA (비개발자 중심, 자연어 시나리오, Self-Healing)
3.  생성된 테스트를 Jenkins CI/CD 파이프라인에 통합

### 0.1 필수 인프라 설치 (Windows 호스트)

구축 전 다음 항목들이 반드시 설치되어 있어야 합니다.

1.  **WSL 2 (필수):**
    * **PowerShell(관리자)** 실행 후 입력: `wsl --install`
    * 설치 완료 후 반드시 **재부팅**하십시오.
2.  **Docker Desktop for Windows:**
    * [Docker Desktop 다운로드](https://www.docker.com/products/docker-desktop/) 및 설치.
    * 설치 시 **"Use WSL 2 based engine"** 옵션 체크 (기본값).
    * **설정 확인:** Settings > Resources > **File sharing** 메뉴에서 프로젝트 폴더 드라이브(C:)가 체크되어 있는지 확인.
3.  **Git for Windows:**
    * [Git 다운로드](https://git-scm.com/download/win) 및 설치.
    * **중요 설정:** 설치 후 터미널에서 `git config --global core.autocrlf input` 실행 (줄바꿈 문자 변환 문제 방지).
4.  **Ollama (로컬 LLM 엔진):**
    * [Ollama for Windows 다운로드](https://ollama.com/download/windows) 및 설치.
    * 설치 후 트레이 아이콘 확인 및 PowerShell에서 모델 다운로드:
        * `ollama pull llama3.2-vision` (이미지 분석용)
        * `ollama pull qwen3-coder:30b` (코드 작성용)
5.  **터미널:** **PowerShell 7** 또는 **Git Bash** 사용 권장.

---

## 1. 고정 전제 및 주소 체계

### 1.1 호스트 브라우저 접속 URL (고정)

1.  **Jenkins:** `http://localhost:8080`
2.  **SonarQube:** `http://localhost:9000`
3.  **GitLab:** `http://localhost:8929`

### 1.2 컨테이너 내부 접근 URL (고정)

*컨테이너 내부 통신은 Docker 네트워크를 따르므로 변경되지 않습니다.*

1.  **Jenkins → Dify API:** `http://api:5001` 또는 `http://api:5001/v1`
2.  **Jenkins → SonarQube:** `http://sonarqube:9000`
3.  **Jenkins → GitLab:** `http://gitlab:8929`
4.  **Jenkins → Ollama:** `http://host.docker.internal:11434` (Docker Desktop 자동 포워딩)

### 1.3 공유 볼륨 및 경로 (Windows 호스트 기준)

* **프로젝트 루트:** 예: `C:\Users\Username\Documents\dscore-ttc`
* **주의:** 가이드의 명령어는 PowerShell/Git Bash 호환을 위해 `/` 경로 구분자를 주로 사용합니다.

---

## 2. 호스트 환경 설정 및 Docker 인프라 구성

### 2.1 Hosts 파일 수정 (Windows)

**문제 원인:** `127.0.0.1 gitlab` 라인이 존재할 경우 GitLab의 external URL 리다이렉트 흐름이 꼬여 UI 접속 문제가 발생할 수 있습니다.

1.  **메모장(Notepad)**을 **관리자 권한**으로 실행합니다.
2.  파일 열기: `C:\Windows\System32\drivers\etc\hosts` (모든 파일 보기 선택)
3.  `127.0.0.1 gitlab` 라인이 있다면 삭제하고 저장합니다.
4.  **DNS 캐시 초기화** (PowerShell 관리자):
    ```powershell
    ipconfig /flushdns
    ```

### 2.2 프로젝트 디렉터리 구성

PowerShell에서 프로젝트 루트로 이동하여 실행합니다.

```powershell
# 프로젝트 루트 생성
mkdir dscore-ttc
cd dscore-ttc

# 필수 데이터 폴더 구조 생성
mkdir -p data/jenkins/scripts
mkdir -p data/knowledges/docs/org
mkdir -p data/knowledges/docs/result
mkdir -p data/knowledges/codes
mkdir -p data/knowledges/qa_reports
mkdir -p data/gitlab/config
mkdir -p data/gitlab/logs
mkdir -p data/gitlab/data
mkdir -p data/sonarqube/data
mkdir -p data/sonarqube/extensions
mkdir -p data/sonarqube/logs
mkdir -p data/postgres-sonar
```

### 2.3 Docker 네트워크 생성

```powershell
docker network create devops-net
# "network already exists" 에러는 무시하십시오.
```

### 2.4 DevOps 스택 (`docker-compose.yaml`) 배치

파일: `docker-compose.yaml`

```yaml
networks:
  devops-net:
    external: true
services:
  # 1) SonarQube DB
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
  # 2) SonarQube
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
  # 3) GitLab CE
  gitlab:
    image: gitlab/gitlab-ce:latest
    container_name: gitlab
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
  # 4) Jenkins (Custom Image)
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

### 2.5 Jenkins 커스텀 이미지 (`Dockerfile.jenkins`) 배치

파일: `Dockerfile.jenkins`
**중요:** VS Code 우측 하단에서 줄바꿈 형식을 **LF**로 설정 후 저장하십시오.

```dockerfile
# DSCORE-TTC 통합 Jenkins 이미지
FROM jenkins/jenkins:lts-jdk21
USER root

# 1. 시스템 의존성 설치 (Playwright 브라우저 실행용 라이브러리 포함)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl jq \
    libgtk-3-0 libasound2 libdbus-glib-1-2 libx11-xcb1 \
    poppler-utils \
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# 2. 필수 파이썬 패키지 설치
RUN pip3 install --no-cache-dir --break-system-packages \
    requests \
    tenacity \
    beautifulsoup4 lxml html2text \
    pypdf pdf2image pillow \
    python-docx \
    python-pptx \
    pandas openpyxl \
    pymupdf \
    crawl4ai playwright ollama

# 3. Playwright 브라우저 엔진 및 시스템 라이브러리 설치 (핵심)
RUN python3 -m playwright install --with-deps chromium

ENV TZ=Asia/Seoul

RUN mkdir -p /var/jenkins_home/scripts \
    /var/jenkins_home/knowledges \
    && chown -R jenkins:jenkins /var/jenkins_home

USER jenkins
```

### 2.6 DevOps 스택 기동

PowerShell에서 실행:
```powershell
docker compose up -d --build --force-recreate
```

### 2.7 Dify 스택 설정 (WSL 2 호환)

1.  Dify 폴더로 이동 (예: `dify/docker`).
2.  환경 변수 복사 (PowerShell):
    ```powershell
    Copy-Item .env.example .env
    ```
3.  `docker-compose.override.yaml` 생성:
    ```yaml
    services:
      api:
        networks:
          - default
          - devops-net
      worker:
        networks:
          - default
          - devops-net
      nginx:
        networks:
          - default
          - devops-net
    networks:
      devops-net:
        external: true
    ```
4.  실행: `docker compose up -d`

---

## 3. 초기 설정 및 인증 토큰 발급

### 3.1 Dify 지식베이스 준비 (문서형 1개 + Q&A형 1개)

**왜 Dataset을 2개로 분리해야 하는가**

1.  Dify 문서 생성 API는 요청의 `doc_form` 값이 Dataset의 `doc_form` 과 일치해야 합니다.
2.  `doc_form` 값은 `text_model`(일반 문서), `qa_model`(Q&A) 중 하나입니다.
3.  따라서 **문서 업로드용 Dataset**과 **Q&A 업로드용 Dataset**을 분리하여 생성하고, 각각의 ID를 관리해야 오류가 발생하지 않습니다.

**Dify API Key 발급 (문서형/QA형 공통)**

1.  Dify 콘솔(`http://localhost:5001`)에 로그인합니다.
2.  상단 또는 좌측 메뉴에서 **"API Keys"** 또는 **"API Access"** 메뉴를 엽니다.
3.  **"Create API Key"** 또는 **"New Key"** 버튼을 선택합니다.
4.  키 이름을 입력하고 생성 버튼을 누릅니다.
5.  화면에 표시되는 API Key를 즉시 복사하여 저장합니다.

**Dataset UUID 확인 (문서형/QA형 각각)**

1.  Dify 콘솔에서 **Knowledge** 메뉴로 이동합니다.
2.  **문서형 Dataset**을 하나 생성합니다 (예: `DSCORE_DOCS`).
3.  **Q&A형 Dataset**을 하나 생성합니다 (예: `DSCORE_QA`).
4.  각 Dataset의 상세 화면으로 진입합니다.
5.  브라우저 주소창 URL 경로에서 `/datasets/` 뒤에 있는 **UUID**를 확인합니다.
    * 예: `http://localhost/datasets/{UUID}/documents`
6.  각각의 UUID를 저장합니다.

### 3.2 Jenkins 초기 설정

1.  `http://localhost:8080` 접속.
2.  **Unlock Jenkins:** PowerShell에서 아래 명령어로 비밀번호 확인 및 입력.
    ```powershell
    docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
    ```
3.  **"Install suggested plugins"** 진행.
4.  **필수 플러그인 설치:** Manage Jenkins -> Plugins -> Available -> `SonarQube Scanner` 검색 및 설치.

### 3.3 Jenkins Credentials 등록 절차 (UI 상세)

**경로:** Jenkins 관리 -> Credentials -> System -> Global credentials (unrestricted) -> Add Credentials
**종류(Kind):** **Secret text**

| Credential ID (고정) | 설명 |
| --- | --- |
| `gitlab-access-token` | GitLab PAT (`api`, `read_repository`, `write_repository` 권한) |
| `sonarqube-token` | SonarQube User Token (Security > Generate Tokens) |
| `dify-knowledge-key` | Dify **문서형** API Key |
| `dify-dataset-id` | Dify **문서형** Dataset UUID |
| `dify-knowledge-key-qa` | Dify **Q&A형** API Key |
| `dify-dataset-id-qa` | Dify **Q&A형** Dataset UUID |

**추가 설정:** Manage Jenkins -> Configure System -> Global properties -> Environment variables 체크 -> Name: `SONAR_TOKEN`, Value: (SonarQube Token 값) 추가.

### 3.4 토큰 발급 절차 상세

1.  **GitLab PAT (`http://localhost:8929`):** 프로필 > Preferences > Access Tokens > Scopes: `api`, `read_repository`, `write_repository`.
2.  **SonarQube Token (`http://localhost:9000`):** 프로필 > Security > Generate Tokens.

---

## 4. 파이썬 스크립트 배치 (전체 코드)

호스트의 `data/jenkins/scripts/` 폴더에 아래 8개 파일을 생성합니다.
**주의:** 저장 시 인코딩은 **UTF-8**, 줄바꿈은 **LF**여야 합니다.

### 4.1 `doc_processor.py`

```python
#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple
import requests
import fitz

SOURCE_DIR = "/var/knowledges/docs/org"
RESULT_DIR = "/var/knowledges/docs/result"
DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://api:5001/v1")

def log(msg: str) -> None:
    print(msg, flush=True)

def safe_read_text(path: Path, max_bytes: int = 5_000_000) -> str:
    try:
        data = path.read_bytes()
        data = data[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def pdf_to_markdown(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    parts = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        text = text.strip()
        if text:
            parts.append(f"## Page {i}\n\n{text}\n")
    doc.close()
    if not parts:
        return ""
    title = pdf_path.name
    body = "\n".join(parts).strip()
    return f"# {title}\n\n{body}\n"

def docx_to_markdown(docx_path: Path) -> str:
    try:
        from docx import Document
    except Exception:
        return ""
    try:
        d = Document(str(docx_path))
        lines = []
        for p in d.paragraphs:
            t = (p.text or "").strip()
            if t:
                lines.append(t)
        if not lines:
            return ""
        return f"# {docx_path.name}\n\n" + "\n\n".join(lines) + "\n"
    except Exception:
        return ""

def excel_to_markdown(xls_path: Path) -> str:
    try:
        import pandas as pd
    except Exception:
        return ""
    try:
        sheets = pd.read_excel(str(xls_path), sheet_name=None)
        if not sheets:
            return ""
        out = [f"# {xls_path.name}\n"]
        for sheet_name, df in sheets.items():
            out.append(f"## Sheet: {sheet_name}\n")
            out.append(df.to_markdown(index=False))
            out.append("\n")
        return "\n".join(out).strip() + "\n"
    except Exception:
        return ""

def pptx_to_pdf(pptx_path: Path, out_dir: Path) -> Optional[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_pdf = out_dir / (pptx_path.stem + ".pdf")
    cmd = [
        "soffice", "--headless", "--nologo", "--nolockcheck", "--norestore",
        "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception:
        return None
    if expected_pdf.exists():
        return expected_pdf
    return None

def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def convert_one(src_path: Path) -> None:
    ext = src_path.suffix.lower()
    if ext == ".pdf":
        md = pdf_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    if ext == ".docx":
        md = docx_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    if ext in [".xlsx", ".xls"]:
        md = excel_to_markdown(src_path)
        if md:
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    if ext == ".txt":
        text = safe_read_text(src_path)
        if text.strip():
            md = f"# {src_path.name}\n\n{text.strip()}\n"
            out = Path(RESULT_DIR) / f"{src_path.name}.md"
            write_text(out, md)
            log(f"[Saved] {out}")
        return
    if ext == ".pptx":
        pdf = pptx_to_pdf(src_path, Path(RESULT_DIR))
        if pdf:
            log(f"[Saved] {pdf}")
        return

def convert_all() -> None:
    log("[Convert] start")
    os.makedirs(RESULT_DIR, exist_ok=True)
    src_root = Path(SOURCE_DIR)
    for root, _, files in os.walk(src_root):
        for name in files:
            p = Path(root) / name
            ext = p.suffix.lower()
            if ext not in [".pdf", ".docx", ".xlsx", ".xls", ".txt", ".pptx"]:
                continue
            log(f"[Convert] {p.name}")
            convert_one(p)
    log("[Convert] done")

def dify_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}

def get_dataset_doc_form(api_key: str, dataset_id: str) -> str:
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    r = requests.get(url, headers=dify_headers(api_key), timeout=60)
    r.raise_for_status()
    data = r.json()
    return str(data.get("doc_form", ""))

def ensure_doc_form_matches(api_key: str, dataset_id: str, expected_doc_form: str) -> None:
    actual = get_dataset_doc_form(api_key, dataset_id)
    if actual != expected_doc_form:
        raise SystemExit(f"[FAIL] Dataset doc_form mismatch. dataset={actual} / request={expected_doc_form}")

def upload_text_document(api_key: str, dataset_id: str, name: str, text: str, doc_form: str, doc_language: str) -> Tuple[bool, str]:
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-text"
    payload = {
        "name": name,
        "text": text,
        "indexing_technique": "economy",
        "doc_form": doc_form,
        "doc_language": doc_language,
        "process_rule": {
            "mode": "automatic",
            "rules": {"remove_extra_spaces": True},
            "remove_urls_emails": False
        },
    }
    r = requests.post(url, headers={**dify_headers(api_key), "Content-Type": "application/json"}, json=payload, timeout=300)
    if r.status_code >= 400:
        return False, f"{r.status_code} / {r.text}"
    return True, "OK"

def upload_file_document(api_key: str, dataset_id: str, file_path: Path, doc_form: str, doc_language: str) -> Tuple[bool, str]:
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-file"
    data = {
        "name": file_path.name,
        "indexing_technique": "economy",
        "doc_form": doc_form,
        "doc_language": doc_language,
        "process_rule": {
            "mode": "automatic",
            "rules": {"remove_extra_spaces": True},
            "remove_urls_emails": False
        },
    }
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f), "data": (None, json.dumps(data))}
        r = requests.post(url, headers=dify_headers(api_key), files=files, timeout=600)
    if r.status_code >= 400:
        return False, f"{r.status_code} / {r.text}"
    return True, "OK"

def upload_all(api_key: str, dataset_id: str, doc_form: str, doc_language: str) -> None:
    log("[Upload] start")
    ensure_doc_form_matches(api_key, dataset_id, doc_form)
    result_root = Path(RESULT_DIR)
    if not result_root.exists():
        log("[Upload] result dir not found")
        return
    for p in sorted(result_root.glob("*")):
        if p.is_dir(): continue
        if p.suffix.lower() == ".md":
            text = safe_read_text(p)
            ok, detail = upload_text_document(api_key, dataset_id, p.name, text, doc_form, doc_language)
            if ok: log(f"[Upload:OK] {p.name}")
            else: log(f"[Upload:FAIL] {p.name} / {detail}")
            continue
        if p.suffix.lower() in [".pdf"]:
            ok, detail = upload_file_document(api_key, dataset_id, p, doc_form, doc_language)
            if ok: log(f"[Upload:OK] {p.name}")
            else: log(f"[Upload:FAIL] {p.name} / {detail}")
            continue
    log("[Upload] done")

def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: doc_processor.py [convert|upload] ...")
    cmd = sys.argv[1].strip().lower()
    if cmd == "convert":
        convert_all()
        return
    if cmd == "upload":
        if len(sys.argv) != 6:
            raise SystemExit("usage: upload <API_KEY> <DATASET_ID> <doc_form> <doc_language>")
        upload_all(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        return
    raise SystemExit(f"unknown cmd: {cmd}")

if __name__ == "__main__":
    main()
```

### 4.2 `vision_processor.py` (비전 분석)

```python
#!/usr/bin/env python3
import os
import base64
from pathlib import Path
from typing import List
import requests

OLLAMA_API = "[http://host.docker.internal:11434/api/generate](http://host.docker.internal:11434/api/generate)"
SOURCE_DIR = "/var/knowledges/docs/org"
RESULT_DIR = "/var/knowledges/docs/result"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

def log(msg: str) -> None:
    print(msg, flush=True)

def list_images(root_dir: str) -> List[Path]:
    out: List[Path] = []
    for root, _, files in os.walk(root_dir):
        for name in files:
            p = Path(root) / name
            if p.suffix.lower() in IMAGE_EXTS:
                out.append(p)
    return sorted(out)

def analyze_image(image_path: Path) -> str:
    with image_path.open("rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "model": "llama3.2-vision:latest",
        "prompt": "Describe this image in detail for markdown documentation.",
        "stream": False,
        "images": [img_b64],
    }
    resp = requests.post(OLLAMA_API, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("response", "")).strip()

def save_markdown(image_name: str, desc: str) -> Path:
    os.makedirs(RESULT_DIR, exist_ok=True)
    out_path = Path(RESULT_DIR) / f"{image_name}.md"
    out_path.write_text(f"# {image_name} (Vision Analysis)\n\n{desc}\n", encoding="utf-8")
    return out_path

def main() -> None:
    log("[Vision] start")
    images = list_images(SOURCE_DIR)
    if not images:
        log("[Vision] no images")
        return
    for img in images:
        log(f"[Vision] {img.name}")
        desc = analyze_image(img)
        if not desc:
            log(f"[Vision:SKIP] {img.name} (empty)")
            continue
        out = save_markdown(img.name, desc)
        log(f"[Saved] {out}")
    log("[Vision] done")

if __name__ == "__main__":
    main()
```

### 4.3 `repo_context_builder.py` (코드 지식화)

```python
#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

EXCLUDE_DIRS = {".git", ".scannerwork", "node_modules", "build", "dist", "out", "target", ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv"}
KEY_FILES = ["README.md", "README.txt", "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "requirements.txt", "pyproject.toml", "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml", ".env.example"]

def safe_read_text(path: Path, max_bytes: int) -> str:
    try:
        data = path.read_bytes()
        data = data[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def build_tree(repo_root: Path, max_lines: int = 3000) -> str:
    lines = []
    count = 0
    for root, dirs, files in os.walk(repo_root):
        rel_root = Path(root).relative_to(repo_root)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        depth = len(rel_root.parts)
        indent = "  " * depth
        if str(rel_root) == ".": lines.append(f"{repo_root.name}/")
        else: lines.append(f"{indent}{rel_root.name}/")
        count += 1
        if count >= max_lines: break
        for f in sorted(files):
            if f in (".DS_Store",): continue
            lines.append(f"{indent}  {f}")
            count += 1
            if count >= max_lines: break
        if count >= max_lines: break
    return "\n".join(lines) + "\n"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo_root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max_key_file_bytes", type=int, default=30000)
    args = ap.parse_args()
    repo_root = Path(args.repo_root).resolve()
    out_path = Path(args.out).resolve()
    parts = ["# Repository Context", "", "## Tree", "", "```text", build_tree(repo_root), "```", "", "## Key Files", ""]
    for k in KEY_FILES:
        p = repo_root / k
        if not p.exists(): continue
        parts.extend([f"### {k}", "", "```", safe_read_text(p, args.max_key_file_bytes), "```", ""])
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[Saved] {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### 4.4 `sonar_issue_exporter.py` (이슈 추출)

```python
#!/usr/bin/env python3
import argparse
import json
import sys
import time
from typing import Dict, Any, List
import requests

def eprint(*args): print(*args, file=sys.stderr)
def parse_csv(s: str) -> List[str]: return [x.strip() for x in (s or "").split(",") if x.strip()]

def sonar_get(session: requests.Session, base: str, path: str, params: Dict[str, Any], token: str) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    headers = {}
    if token: headers["Authorization"] = f"Bearer {token}"
    r = session.get(url, params=params, headers=headers, timeout=60)
    if r.status_code >= 400:
        eprint(f"[ERROR] HTTP {r.status_code} : {r.text[:2000]}")
        r.raise_for_status()
    return r.json()

def export_issues(args) -> Dict[str, Any]:
    severities = parse_csv(args.severities)
    statuses = parse_csv(args.statuses)
    session = requests.Session()
    page_size = 500
    p = 1
    all_issues = []
    while True:
        params = {"componentKeys": args.project_key, "types": "BUG,VULNERABILITY,CODE_SMELL", "ps": page_size, "p": p}
        if severities: params["severities"] = ",".join(severities)
        if statuses: params["statuses"] = ",".join(statuses)
        data = sonar_get(session, args.sonar_host_url, "/api/issues/search", params, args.sonar_token)
        issues = data.get("issues") or []
        all_issues.extend(issues)
        paging = data.get("paging") or {}
        if len(all_issues) >= paging.get("total", len(all_issues)): break
        p = paging.get("pageIndex", p) + 1
        time.sleep(0.05)
    return {"generated_at": int(time.time()), "sonar_host_url": args.sonar_host_url, "sonar_public_url": args.sonar_public_url, "project_key": args.project_key, "count": len(all_issues), "issues": all_issues}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-public-url", required=False, default="")
    ap.add_argument("--sonar-token", required=False, default="")
    ap.add_argument("--project-key", required=True)
    ap.add_argument("--severities", required=False, default="")
    ap.add_argument("--statuses", required=False, default="")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    out = export_issues(args)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OK] exported: {args.output} (count={out['count']})")

if __name__ == "__main__":
    main()
```

### 4.5 `dify_sonar_issue_analyzer.py` (LLM 분석)

```python
#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.parse
import requests

def eprint(*args): print(*args, file=sys.stderr)
def safe_json_dumps(obj) -> str:
    try: return json.dumps(obj, ensure_ascii=False)
    except: return str(obj)

def sonar_get(session, base, path, params, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = session.get(base.rstrip("/") + path, params=params, headers=headers, timeout=60)
    if r.status_code >= 400: r.raise_for_status()
    return r.json()

def build_issue_url(public_url, project_key, issue_key):
    if not public_url: return ""
    return f"{public_url.rstrip('/')}/project/issues?id={urllib.parse.quote(project_key)}&issues={urllib.parse.quote(issue_key)}&open={urllib.parse.quote(issue_key)}"

def fetch_code_snippet(session, host, token, component, line, context=5):
    if not component or not line: return ""
    start = max(1, int(line) - context)
    data = sonar_get(session, host, "/api/sources/lines", {"key": component, "from": start, "to": int(line)+context}, token)
    lines = data.get("sources") or data.get("lines") or []
    out = []
    for item in lines:
        ln = item.get("line")
        prefix = ">> " if ln == line else "   "
        out.append(f"{prefix}{ln}: {item.get('code','')}")
    return "\n".join(out)

def dify_run_workflow(session, base, key, inputs, user, response_mode, timeout):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"inputs": inputs, "response_mode": response_mode, "user": user}
    r = session.post(base.rstrip("/")+"/workflows/run", headers=headers, data=json.dumps(payload), timeout=timeout)
    if r.status_code >= 400: return False, {}, r.text
    return True, r.json(), ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sonar-host-url", required=True)
    ap.add_argument("--sonar-public-url", default="")
    ap.add_argument("--sonar-token", default="")
    ap.add_argument("--dify-api-base", required=True)
    ap.add_argument("--dify-api-key", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--max-issues", type=int, default=0)
    args = ap.parse_args()
    
    with open(args.input, "r", encoding="utf-8") as f: src = json.load(f)
    issues = src.get("issues", [])
    if args.max_issues > 0: issues = issues[:args.max_issues]
    
    session = requests.Session()
    rows = []
    failed = 0
    
    for it in issues:
        issue_key = it.get("key")
        rule_key = it.get("rule")
        component = it.get("component")
        try:
            rule_json = sonar_get(session, args.sonar_host_url, "/api/rules/show", {"key": rule_key}, args.sonar_token)
        except: rule_json = {}
        try:
            snippet = fetch_code_snippet(session, args.sonar_host_url, args.sonar_token, component, it.get("line"))
        except: snippet = ""
        
        inputs = {
            "sonar_issue_json": safe_json_dumps(it),
            "sonar_rule_json": safe_json_dumps(rule_json),
            "code_snippet": snippet,
            "sonar_issue_url": build_issue_url(args.sonar_public_url, src.get("project_key"), issue_key),
            "sonar_issue_key": issue_key,
            "sonar_project_key": src.get("project_key"),
            "kb_query": f"{rule_key} {it.get('message','')}".strip(),
        }
        
        ok, resp, err = dify_run_workflow(session, args.dify_api_base, args.dify_api_key, inputs, args.user, "blocking", 180)
        if not ok:
            eprint(f"[FAIL] {issue_key} {err}")
            failed += 1
            continue
            
        out = resp.get("data", {}).get("outputs") or resp.get("outputs") or {}
        rows.append({
            "sonar_issue_key": issue_key,
            "title": out.get("title", ""),
            "description_markdown": out.get("description_markdown", ""),
            "labels": out.get("labels", "")
        })
        with open(args.output, "w", encoding="utf-8") as f:
            for r in rows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
            
    if failed > 0: sys.exit(2)

if __name__ == "__main__":
    main()
```

### 4.6 `gitlab_issue_creator.py` (이슈 등록)

```python
#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.parse
import requests

def gitlab_post(session, base, token, path, payload):
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    r = session.post(base.rstrip("/")+path, headers=headers, data=json.dumps(payload), timeout=60)
    if r.status_code >= 400: r.raise_for_status()
    return r.json()

def gitlab_get(session, base, token, path, params):
    headers = {"PRIVATE-TOKEN": token}
    r = session.get(base.rstrip("/")+path, headers=headers, params=params, timeout=60)
    if r.status_code >= 400: r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitlab-api-base", required=True)
    ap.add_argument("--gitlab-token", required=True)
    ap.add_argument("--project-path", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    
    rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
            
    session = requests.Session()
    pid = urllib.parse.quote(args.project_path, safe="")
    created = []
    
    for r in rows:
        key = r.get("sonar_issue_key")
        labels = r.get("labels", "")
        key_label = next((x for x in labels.split(",") if x.strip().startswith("sonar_issue_key-")), "")
        
        # 중복 체크
        if key_label:
            exist = gitlab_get(session, args.gitlab_api_base, args.gitlab_token, f"/projects/{pid}/issues", {"state":"opened", "labels": key_label})
            if exist: continue
            
        payload = {"title": r.get("title")[:240], "description": r.get("description_markdown"), "labels": labels}
        try:
            res = gitlab_post(session, args.gitlab_api_base, args.gitlab_token, f"/projects/{pid}/issues", payload)
            created.append({"key": key, "iid": res.get("iid")})
        except Exception as e:
            print(f"[FAIL] {key} {e}")
            
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(created, f, indent=2)

if __name__ == "__main__":
    main()
```

### 4.7 `domain_knowledge_builder.py` (웹 수집)

```python
#!/usr/bin/env python3
import asyncio
import os
import sys
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

RESULT_DIR = "/var/knowledges/docs/result"

def refine_content(html_content: str) -> str:
    soup = BeautifulSoup(html_content, 'html.parser')
    content_area = None
    for selector in ['div.mw-parser-output', 'article', '.post-content', 'main', '#content']:
        content_area = soup.select_one(selector)
        if content_area: break
    if not content_area: content_area = soup.body
    if not content_area: return ""
    for tag in content_area.select('nav, footer, aside, .sidebar, .menu, script, style, .mw-editsection, .navbox'):
        tag.decompose()
    text = content_area.get_text(separator='\n')
    text = re.sub(r'\[([^\]]+)\]\(https?://\S+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\[\d+\]|\[편집\]', '', text)
    return re.sub(r'\n\s*\n', '\n\n', text).strip()

async def build_knowledge(root_url: str) -> None:
    os.makedirs(RESULT_DIR, exist_ok=True)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=root_url)
        base_domain = urlparse(root_url).netloc
        urls = {urljoin(root_url, l['href']) for l in result.links.get("internal", []) if base_domain in urljoin(root_url, l['href'])}
        urls.add(root_url)
        for url in list(urls)[:50]:
            try:
                res = await crawler.arun(url=url, bypass_cache=True)
                if res.success and res.html:
                    clean = refine_content(res.html)
                    if len(clean) < 200: continue
                    safe_name = url.split("//")[-1].replace("/", "_").replace(".", "_")[:100]
                    (Path(RESULT_DIR) / f"web_{safe_name}.md").write_text(f"# Source: {url}\n\n{clean}", encoding="utf-8")
                    print(f"[Success] {url}")
            except Exception as e:
                print(f"[Error] {url} - {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) > 1: asyncio.run(build_knowledge(sys.argv[1]))
    else: sys.exit(1)
```

---

## 5. Dify Workflow 구성 절차 (상세 복원)

### 5.1 Workflow 개요

**목표:**
SonarQube 이슈를 입력받아 LLM이 원인 분석 및 해결 방안을 작성하는 Workflow를 구성한다.

**입력 변수 (7개 고정):**
1.  `sonar_issue_json` (String) - 이슈 전체 JSON (severity, message 등)
2.  `sonar_rule_json` (String) - 룰 상세 JSON (설명, 예제 코드 등)
3.  `code_snippet` (String) - 이슈 발생 코드 스니펫 (라인 번호 포함)
4.  `sonar_issue_url` (String) - SonarQube UI 링크
5.  `sonar_issue_key` (String) - 이슈 고유 키
6.  `sonar_project_key` (String) - 프로젝트 키
7.  `kb_query` (String) - Knowledge Base 검색용 키워드

**출력 변수 (3개 고정):**
1.  `title` - GitLab Issue 제목
2.  `description_markdown` - GitLab Issue 본문 (Markdown)
3.  `labels` - GitLab Issue 라벨 (CSV, 중복 방지용 key 포함)

### 5.2 Workflow 노드 구성

**권장 구성:**
1.  **Start (시작):** 입력 변수 7개 정의
2.  **Knowledge Retrieval (지식 검색):** `kb_query`로 Dify Dataset 검색
3.  **LLM (원인 분석):** 이슈 + 룰 + 스니펫 + 검색 결과 → 분석
4.  **Code (후처리):** Title/Description/Labels 정규화
5.  **End (종료):** 출력 변수 3개 반환

### 5.3 Start 노드 설정

모든 입력 변수(`sonar_issue_json` 등 7개)를 **Text(String)** 타입으로 정의합니다.

### 5.4 Knowledge Retrieval 노드 설정

* **Dataset 선택:** Job #1, #4, #6으로 업로드한 Dataset을 모두 선택합니다.
* **Query 설정:** `{{#start.kb_query#}}`
* **Retrieval Settings:**
    * Top K: 3
    * Score Threshold: 0.3

### 5.5 LLM 노드 설정 (전체 내용)

* **Model:** Claude 3.5 Sonnet, GPT-4o, 또는 성능 좋은 로컬 모델.
* **Context:** Knowledge Retrieval 노드의 출력(`{{#knowledge_retrieval.result#}}`)을 포함.

**System Prompt (전체):**
```text
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

**User Prompt (전체):**
```text
# SonarQube Issue Analysis

## Issue Information
{{#start.sonar_issue_json#}}

## Rule Description
{{#start.sonar_rule_json#}}

## Code Snippet
{{#start.code_snippet#}}

## Related Knowledge
{{#knowledge_retrieval.result#}}

## SonarQube Link
{{#start.sonar_issue_url#}}
---

Please analyze this issue and provide:
1. Title: Brief, actionable title (max 100 chars)
2. Description: Detailed analysis in Markdown format
3. Labels: Comma-separated labels including "sonarqube,sonar_issue_key-{{#start.sonar_issue_key#}}"
```

### 5.6 Code 노드 설정 (전체 코드)

LLM 응답에서 Title, Description, Labels를 추출하는 **Python 코드**입니다.

```python
import json
import re

def main(llm_response: str, sonar_issue_key: str) -> dict:
    """
    LLM 응답을 파싱하여 GitLab Issue 필드 추출
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
* `title` : `{{#code.title#}}`
* `description_markdown` : `{{#code.description_markdown#}}`
* `labels` : `{{#code.labels#}}`

---

## 6. Jenkins Pipeline 구성 (7개 Job 전체)

Jenkins Dashboard > New Item > Pipeline 선택 후 아래 Script들을 입력합니다.

### 6.1 Job #1: DSCORE-Knowledge-Sync
```groovy
pipeline {
    agent any
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'text_model'
        DOC_LANGUAGE = 'Korean'
    }
    stages {
        stage('Convert') { steps { sh "python3 ${SCRIPTS_DIR}/doc_processor.py convert" } }
        stage('Upload') {
            steps {
                withCredentials([string(credentialsId: 'dify-knowledge-key', variable: 'KEY'), string(credentialsId: 'dify-dataset-id', variable: 'ID')]) {
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$KEY\" \"$ID\" \"$DOC_FORM\" \"$DOC_LANGUAGE\""
                }
            }
        }
    }
}
```

### 6.2 Job #2: DSCORE-Knowledge-Sync-QA
```groovy
pipeline {
    agent any
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'qa_model'
        DOC_LANGUAGE = 'Korean'
    }
    stages {
        stage('Convert') { steps { sh "python3 ${SCRIPTS_DIR}/doc_processor.py convert" } }
        stage('Upload') {
            steps {
                withCredentials([string(credentialsId: 'dify-knowledge-key-qa', variable: 'KEY'), string(credentialsId: 'dify-dataset-id-qa', variable: 'ID')]) {
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$KEY\" \"$ID\" \"$DOC_FORM\" \"$DOC_LANGUAGE\""
                }
            }
        }
    }
}
```

### 6.3 Job #3: DSCORE-Knowledge-Sync-Vision
```groovy
pipeline {
    agent any
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DOC_FORM = 'text_model'
    }
    stages {
        stage('Vision Analysis') { steps { sh "python3 ${SCRIPTS_DIR}/vision_processor.py" } }
        stage('Upload') {
            steps {
                withCredentials([string(credentialsId: 'dify-knowledge-key', variable: 'KEY'), string(credentialsId: 'dify-dataset-id', variable: 'ID')]) {
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$KEY\" \"$ID\" \"$DOC_FORM\" \"Korean\""
                }
            }
        }
    }
}
```

### 6.4 Job #4: DSCORE-Code-Knowledge-Sync
```groovy
pipeline {
    agent any
    parameters { string(name: 'REPO_URL', defaultValue: '', description: 'Git repository URL') }
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        WORKSPACE_CODES = '/var/knowledges/codes'
        RESULT_DIR = '/var/knowledges/docs/result'
        DOC_FORM = 'text_model'
    }
    stages {
        stage('Clone') {
            when { expression { params.REPO_URL != '' } }
            steps { sh "REPO_NAME=\$(basename \"${params.REPO_URL}\" .git) && rm -rf ${WORKSPACE_CODES}/* && git clone \"${params.REPO_URL}\" ${WORKSPACE_CODES}/\$REPO_NAME" }
        }
        stage('Build Context') {
            steps { sh "REPO_NAME=\$(basename \"${params.REPO_URL}\" .git) && rm -rf ${RESULT_DIR}/* && python3 ${SCRIPTS_DIR}/repo_context_builder.py --repo_root ${WORKSPACE_CODES}/\$REPO_NAME --out ${RESULT_DIR}" }
        }
        stage('Upload') {
            steps {
                withCredentials([string(credentialsId: 'dify-knowledge-key', variable: 'KEY'), string(credentialsId: 'dify-dataset-id', variable: 'ID')]) {
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$KEY\" \"$ID\" \"$DOC_FORM\" \"Korean\""
                }
            }
        }
    }
}
```

### 6.5 Job #5: DSCORE-Quality-Issue-Workflow
```groovy
pipeline {
    agent any
    parameters {
        string(name: 'SONAR_PROJECT_KEY', defaultValue: 'dscore-ttc-sample')
        string(name: 'GITLAB_PROJECT_PATH', defaultValue: 'root/dscore-ttc-sample')
        string(name: 'MAX_ISSUES', defaultValue: '10')
    }
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        WORK_DIR = '/var/jenkins_home/workspace/quality-workflow'
        SONAR_HOST = 'http://sonarqube:9000'
        SONAR_PUB = 'http://localhost:9000'
        GITLAB_API = 'http://gitlab:8929/api/v4'
        DIFY_API = 'http://api:5001/v1'
        DIFY_USER = 'jenkins'
    }
    stages {
        stage('Prepare') { steps { sh "mkdir -p ${WORK_DIR} && rm -rf ${WORK_DIR}/*" } }
        stage('Export') {
            steps {
                withCredentials([string(credentialsId: 'sonarqube-token', variable: 'TOK')]) {
                    sh "python3 ${SCRIPTS_DIR}/gitlab_issue_creator.py --gitlab-host-url \"http://gitlab:8929\" --gitlab-token \"\$G_TOK\" --gitlab-project \"${params.GITLAB_PROJECT_PATH}\" --input \"${WORK_DIR}/llm_analysis.jsonl\" --output \"${WORK_DIR}/gitlab_issues_created.json\""
                }
            }
        }
        stage('Analyze') {
            steps {
                withCredentials([string(credentialsId: 'sonarqube-token', variable: 'S_TOK'), string(credentialsId: 'dify-knowledge-key', variable: 'D_KEY')]) {
                    sh "python3 ${SCRIPTS_DIR}/dify_sonar_issue_analyzer.py --sonar-host-url \"${SONAR_HOST}\" --sonar-public-url \"${SONAR_PUB}\" --sonar-token \"$S_TOK\" --dify-api-base \"${DIFY_API}\" --dify-api-key \"$D_KEY\" --input \"${WORK_DIR}/sonar_issues.json\" --output \"${WORK_DIR}/llm_analysis.jsonl\" --user \"${DIFY_USER}\" --max-issues \"${params.MAX_ISSUES}\""
                }
            }
        }
        stage('Create Issues') {
            steps {
                withCredentials([string(credentialsId: 'gitlab-access-token', variable: 'G_TOK')]) {
                    sh "python3 ${SCRIPTS_DIR}/gitlab_issue_creator.py --gitlab-api-base \"${GITLAB_API}\" --gitlab-token \"$G_TOK\" --project-path \"${params.GITLAB_PROJECT_PATH}\" --input \"${WORK_DIR}/llm_analysis.jsonl\" --output \"${WORK_DIR}/gitlab_issues_created.json\""
                    sh "python3 ${SCRIPTS_DIR}/gitlab_issue_creator.py --gitlab-host-url \"http://gitlab:8929\" --gitlab-token \"\$G_TOK\" --gitlab-project \"${params.GITLAB_PROJECT_PATH}\" --input \"${WORK_DIR}/llm_analysis.jsonl\" --output \"${WORK_DIR}/gitlab_issues_created.json\""
                }
            }
        }
    }
}
```

### 6.6 Job #6: DSCORE-Web-Knowledge-Sync
```groovy
pipeline {
    agent any
    parameters { string(name: 'ROOT_URL') }
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        DIFY_DOC_FORM = 'text_model'
    }
    stages {
        stage('Scrape') {
            when { expression { params.ROOT_URL != '' } }
            steps { sh "python3 ${SCRIPTS_DIR}/domain_knowledge_builder.py \"${params.ROOT_URL}\"" }
        }
        stage('Upload') {
            steps {
                withCredentials([string(credentialsId: 'dify-knowledge-key', variable: 'KEY'), string(credentialsId: 'dify-dataset-id', variable: 'ID')]) {
                    sh "python3 ${SCRIPTS_DIR}/doc_processor.py upload \"$KEY\" \"$ID\" \"$DIFY_DOC_FORM\" \"Korean\""
                }
            }
        }
    }
}
```

---

## 7. 샘플 프로젝트 구성

Git Bash 또는 PowerShell에서 실행:

```powershell
mkdir dscore-sample
cd dscore-sample
git init
# (BadCode.java 파일 생성 등 생략 - 로컬 파일 생성)
git add .
git commit -m "Initial commit with bugs"
git remote add origin http://localhost:8929/root/dscore-ttc-sample.git
git push -u origin main
```

---

## 8. 트러블슈팅

### 8.1 Docker Volume 권한
* **증상:** Permission denied.
* **해결:** Docker Desktop 설정 > File sharing에 프로젝트 드라이브(C:) 추가.

### 8.2 Ollama 연결 실패
* **증상:** Jenkins에서 `Connection refused` (11434 포트).
* **해결:** Windows 방화벽 인바운드 허용, `OLLAMA_HOST=0.0.0.0` 설정 후 재시작. `host.docker.internal` 사용 확인.

---

## 9. E2E 테스트 자동화 - Method A: Playwright + Roo Code

Windows 로컬 환경에서 진행합니다.

1.  **Node.js & VS Code 설치.**
2.  **프로젝트 초기화:** `npm init playwright@latest`.
3.  **VS Code Roo Code 확장 설치** 및 Ollama 연결 (`qwen3-coder:30b`).
4.  **Playwright MCP 설정 (`roo_mcp_settings.json`):**
    ```json
    {
      "mcpServers": {
        "playwright": {
          "command": "npx.cmd",
          "args": ["-y", "@playwright/mcp@latest"]
        }
      }
    }
    ```
5.  **테스트 생성:** Roo Code에게 "로그인 페이지 테스트 작성해줘"라고 명령.

---

## 10. E2E 테스트 자동화 - Method B: Zero-Touch QA (자동화용)

### 10.1 `autonomous_qa.py` (Zero-Touch QA 에이전트 전체 코드)

`data/jenkins/scripts/autonomous_qa.py`에 저장합니다.

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
import ollama
from playwright.sync_api import sync_playwright

DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "[http://host.docker.internal:11434](http://host.docker.internal:11434)")
DEFAULT_MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder:30b")
DEFAULT_HEADLESS = os.getenv("HEADLESS", "1") == "1"
DEFAULT_SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "300"))
DEFAULT_TIMEOUT_MS = int(os.getenv("DEFAULT_TIMEOUT_MS", "10000"))
FAST_TIMEOUT_MS = int(os.getenv("FAST_TIMEOUT_MS", "1000"))
HEAL_MODE = os.getenv("HEAL_MODE", "on")
MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))
CANDIDATE_TOP_N = int(os.getenv("CANDIDATE_TOP_N", "8"))

def now_iso() -> str: return datetime.now().isoformat(timespec="seconds")
def log(msg: str) -> None: print(f"[AutoQA] {msg}", flush=True)
def ensure_dir(path: str) -> None: os.makedirs(path, exist_ok=True)
def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=2)
def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def extract_json_array(text: str) -> str:
    t = text.strip().replace("```json", "").replace("```", "").strip()
    return t[t.find("["):t.rfind("]") + 1]

def extract_json_object(text: str) -> str:
    t = text.strip().replace("```json", "").replace("```", "").strip()
    return t[t.find("{"):t.rfind("}") + 1]

def similarity(a: str, b: str) -> float:
    if not a or not b: return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

@dataclass
class IntentTarget:
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    text: Optional[str] = None
    placeholder: Optional[str] = None
    testid: Optional[str] = None
    selector: Optional[str] = None
    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "IntentTarget":
        return IntentTarget(role=d.get("role"), name=d.get("name"), label=d.get("label"), text=d.get("text"), placeholder=d.get("placeholder"), testid=d.get("testid"), selector=d.get("selector"))

class LocatorResolver:
    def __init__(self, page, fast_timeout_ms: int):
        self.page = page
        self.fast_timeout_ms = fast_timeout_ms
    def _try_visible(self, locator) -> bool:
        try:
            locator.first.wait_for(state="visible", timeout=self.fast_timeout_ms)
            return True
        except: return False
    def resolve(self, target: IntentTarget):
        if target.role and target.name:
            loc = self.page.get_by_role(target.role, name=target.name)
            if self._try_visible(loc): return loc
        if target.label:
            loc = self.page.get_by_label(target.label)
            if self._try_visible(loc): return loc
        if target.text:
            loc = self.page.get_by_text(target.text, exact=False)
            if self._try_visible(loc): return loc
        if target.placeholder:
            loc = self.page.get_by_placeholder(target.placeholder)
            if self._try_visible(loc): return loc
        if target.testid:
            loc = self.page.locator(f"[data-testid='{target.testid}']")
            if self._try_visible(loc): return loc
        if target.selector:
            loc = self.page.locator(target.selector)
            if self._try_visible(loc): return loc
        raise RuntimeError(f"Target not resolved: {target}")

def collect_accessibility_candidates(page) -> List[Dict[str, str]]:
    snapshot = page.accessibility.snapshot()
    results = []
    def walk(node):
        if node.get("role") and node.get("name"): results.append({"role": node["role"], "name": node["name"]})
        for child in node.get("children", []) or []: walk(child)
    if snapshot: walk(snapshot)
    dedup = {}
    for r in results: dedup[f"{r['role']}|{r['name']}"] = r
    return list(dedup.values())

def filter_candidates_by_action(action, candidates):
    if action == "click": return [c for c in candidates if c.get("role") in {"button", "link", "menuitem", "tab", "checkbox", "radio"}]
    if action == "fill": return [c for c in candidates if c.get("role") in {"textbox", "searchbox", "combobox"}]
    return candidates

def rank_candidates(query, target_role, candidates):
    scored = []
    for c in candidates:
        s = similarity(query, c.get("name", ""))
        if target_role and c.get("role") == target_role: s += 0.10
        scored.append({"role": c.get("role", ""), "name": c.get("name", ""), "score": round(s, 3)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

def build_llm_heal_prompt(action, failed_target, error_text, page_url, ranked_candidates):
    candidates_json = json.dumps(ranked_candidates[:CANDIDATE_TOP_N], ensure_ascii=False, indent=2)
    return f"""[Self-Healing Request]
Action: {action}
Failed Target: {json.dumps(failed_target, ensure_ascii=False)}
Error: {error_text}
URL: {page_url}
Candidates: {candidates_json}
Suggest new target JSON object."""

def build_html_report(rows):
    html = "<html><body><h2>Zero-Touch QA Report</h2><table><tr><th>Step</th><th>Action</th><th>Status</th><th>Healing</th></tr>"
    for r in rows:
        html += f"<tr><td>{r.get('step')}</td><td>{r.get('action')}</td><td>{r.get('status')}</td><td>{r.get('heal_stage')}</td></tr>"
    html += "</table></body></html>"
    return html

class ZeroTouchAgent:
    def __init__(self, url, srs_text, out_dir, ollama_host, model):
        self.url = url
        self.srs_text = srs_text
        self.out_dir = out_dir
        self.client = ollama.Client(host=ollama_host)
        self.model = model
        ensure_dir(out_dir)
        self.path_scenario = os.path.join(out_dir, "test_scenario.json")
        self.path_healed = os.path.join(out_dir, "test_scenario.healed.json")
        self.path_log = os.path.join(out_dir, "run_log.jsonl")
        self.path_report = os.path.join(out_dir, "index.html")

    def plan_scenario(self):
        log("Planning scenario...")
        prompt = f"Convert SRS to JSON scenario array: {self.srs_text}\nTarget URL: {self.url}"
        res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}])
        scenario = json.loads(extract_json_array(res["message"]["content"]))
        write_json(self.path_scenario, scenario)
        return scenario

    def _execute_action(self, page, resolver, step):
        action = step.get("action")
        if action == "navigate":
            page.goto(step.get("value") or self.url)
            return
        if action == "wait":
            page.wait_for_timeout(int(step.get("value", 1500)))
            return
        if action in ["click", "fill", "check"]:
            target = IntentTarget.from_dict(step.get("target") or {})
            loc = resolver.resolve(target)
            if action == "click": loc.first.click(timeout=DEFAULT_TIMEOUT_MS)
            elif action == "fill": loc.first.fill(str(step.get("value", "")), timeout=DEFAULT_TIMEOUT_MS)
            elif action == "check": loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)

    def _heal_step(self, page, resolver, step, action, error_text):
        original_target = step.get("target") or {}
        query = original_target.get("name") or original_target.get("text") or original_target.get("label") or ""
        
        # 1. Fallback
        fallbacks = step.get("fallback_targets") or []
        for fb in fallbacks:
            step["target"] = fb
            try:
                self._execute_action(page, resolver, step)
                return True, "fallback"
            except: pass
            
        # 2. Candidate Search & 3. LLM Heal (Simplified logic for brevity in this full dump)
        if HEAL_MODE == "on":
            try:
                candidates = collect_accessibility_candidates(page)
                candidates = filter_candidates_by_action(action, candidates)
                ranked = rank_candidates(query, original_target.get("role"), candidates)
                prompt = build_llm_heal_prompt(action, original_target, error_text, page.url, ranked)
                res = self.client.chat(model=self.model, messages=[{"role": "user", "content": prompt}])
                heal_obj = json.loads(extract_json_object(res["message"]["content"]))
                step["target"] = heal_obj.get("target")
                self._execute_action(page, resolver, step)
                return True, "llm_heal"
            except: pass
            
        return False, "heal_failed"

    def execute(self, scenario):
        log("Executing scenario...")
        rows = []
        healed_scenario = json.loads(json.dumps(scenario))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=DEFAULT_HEADLESS, slow_mo=DEFAULT_SLOW_MO_MS)
            page = browser.new_page()
            resolver = LocatorResolver(page, FAST_TIMEOUT_MS)
            for step in healed_scenario:
                action = step.get("action")
                heal_stage = "none"
                status = "PASS"
                try:
                    self._execute_action(page, resolver, step)
                except Exception as e:
                    if action in ["click", "fill", "check"]:
                        ok, heal_stage = self._heal_step(page, resolver, step, action, str(e))
                        if not ok: status = "FAIL"
                    else: status = "FAIL"
                rows.append({"step": step.get("step"), "action": action, "status": status, "heal_stage": heal_stage})
            browser.close()
        return rows, healed_scenario

    def run(self):
        scenario = self.plan_scenario()
        rows, healed = self.execute(scenario)
        write_json(self.path_healed, healed)
        with open(self.path_report, "w") as f: f.write(build_html_report(rows))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--srs_file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ollama_host", default=DEFAULT_OLLAMA_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    args = parser.parse_args()
    
    with open(args.srs_file, "r", encoding="utf-8") as f: srs = f.read()
    agent = ZeroTouchAgent(args.url, srs, args.out, args.ollama_host, args.model)
    agent.run()
```

### 10.6 Job #7: DSCORE-ZeroTouch-QA

```groovy
pipeline {
    agent any
    parameters {
        string(name: 'TARGET_URL', defaultValue: '[http://host.docker.internal:3000](http://host.docker.internal:3000)')
        file(name: 'SRS_FILE', description: '자연어 요구사항 파일(.txt)')
        string(name: 'MODEL_NAME', defaultValue: 'qwen3-coder:30b')
        choice(name: 'HEAL_MODE', choices: ['on', 'off'])
    }
    environment {
        SCRIPTS_DIR = '/var/jenkins_home/scripts'
        REPORT_DIR  = "/var/knowledges/qa_reports/${BUILD_NUMBER}"
        OLLAMA_HOST = '[http://host.docker.internal:11434](http://host.docker.internal:11434)'
    }
    stages {
        stage('Run Agent') {
            steps {
                sh "mkdir -p ${REPORT_DIR}"
                withFileParameter(name: 'SRS_FILE', variable: 'UPLOADED_SRS') {
                    sh """
                    export OLLAMA_HOST="${env.OLLAMA_HOST}"
                    python3 ${SCRIPTS_DIR}/autonomous_qa.py \
                        --url "${params.TARGET_URL}" \
                        --srs_file "${UPLOADED_SRS}" \
                        --out "${REPORT_DIR}" \
                        --model "${params.MODEL_NAME}"
                    """
                }
            }
        }
    }
    post {
        always {
            publishHTML([reportDir: "${REPORT_DIR}", reportFiles: 'index.html', reportName: 'Zero-Touch QA Report'])
            archiveArtifacts artifacts: "${REPORT_DIR}/**/*"
        }
    }
}
```

---

## 11. E2E 방식 비교

| 항목 | 방식 A (Roo Code) | 방식 B (Zero-Touch QA) |
| :--- | :--- | :--- |
| **주체** | 개발자 (IDE) | QA/기획자 (Jenkins) |
| **입력** | 채팅 명령 | SRS 텍스트 파일 |
| **코드** | TypeScript 생성됨 | JSON 시나리오 (Code-less) |
| **실행** | 로컬 VS Code | Jenkins 컨테이너 |
| **유지보수**| 코드 직접 수정 | 시나리오 재생성 또는 자동 치유 |
| **추천** | 정교한 기능 테스트 | 회귀 테스트, 스모크 테스트 |

---