# DSCORE-TTC 통합 구축 가이드 (Windows)

> **이 문서는 루트 `readme.md`(macOS/Linux 기준)의 Windows 11 환경 대응판이다.**
> Docker 컨테이너 내부에서 실행되는 코드(Python 스크립트, Jenkinsfile, docker-compose.yaml, Dockerfile 등)는 OS와 무관하게 동일하므로, 해당 부분은 루트 `readme.md`의 Section을 그대로 참조한다.
> 이 문서는 **Windows 환경에서만 달라지는 설치, 경로, 명령어, 보안 설정**에 집중한다.

---

## 1. 개요 및 문서 범위

이 문서는 **폐쇄망 환경의 단일 Windows 머신에서 동작하는, 로컬 LLM 기반 온프레미스 QAOps 시스템**을 처음부터 끝까지 구축하는 가이드다.
각 섹션의 순서대로 따라 하면 외부 인터넷 없이도 아래 5개 Phase가 모두 동작하는 환경을 완성할 수 있다.

- 모든 서비스는 Docker 컨테이너로 로컬에서 실행된다.
- AI 추론은 Ollama를 통해 로컬 GPU/CPU에서 수행된다.
- 외부 인터넷 연결 없이 전체 파이프라인이 동작한다.

### 1.1 사전 요구사항

| 항목 | 상세 |
| --- | --- |
| OS | Windows 11 (64-bit) Pro, Enterprise, 또는 Education, 버전 23H2(빌드 22631) 이상 |
| CPU | 64-bit 프로세서, SLAT(Second Level Address Translation) 지원, BIOS에서 가상화(VT-x/AMD-V) 활성화 필수 |
| RAM | 최소 16 GB (GitLab + Ollama 동시 실행 시 32 GB 권장) |
| 디스크 | 최소 50 GB 여유 공간 (SSD 권장) |
| GPU (선택) | NVIDIA GPU + 최신 드라이버 권장 (Ollama가 자동 감지하여 GPU 가속 사용) |

### 1.2 필수 소프트웨어 설치 (상세)

아래 순서대로 설치한다. 각 단계의 완료를 확인한 뒤 다음으로 넘어간다.

#### Step 1: WSL 2 (Windows Subsystem for Linux 2)

Docker Desktop이 WSL 2 백엔드를 사용하므로 반드시 먼저 설치한다.

1. **PowerShell을 관리자 권한으로 실행한다.**
   - 시작 메뉴에서 `PowerShell`을 검색하고 **"관리자로 실행"** 을 선택한다.

2. **WSL 2를 설치한다.**

```powershell
wsl --install
```

이 명령은 WSL 기능 활성화, 가상 머신 플랫폼 활성화, Linux 커널 업데이트, Ubuntu 배포판 설치를 한 번에 수행한다.

3. **PC를 재부팅한다.**

```powershell
Restart-Computer
```

4. **재부팅 후 Ubuntu 터미널이 자동으로 열리면 사용자 이름과 비밀번호를 설정한다.**

5. **WSL 2가 기본 버전인지 확인한다.**

```powershell
wsl --list --verbose
```

`VERSION` 컬럼이 `2`인지 확인한다. 만약 `1`이면 다음을 실행한다:

```powershell
wsl --set-default-version 2
```

6. **WSL을 최신 버전으로 업데이트한다.**

```powershell
wsl --update
```

> **참고:** WSL 최소 버전 2.1.5 이상이어야 Docker Desktop이 정상 동작한다. Enhanced Container Isolation을 사용하려면 2.6 이상이 필요하다.

#### Step 2: Docker Desktop for Windows

1. **Docker Desktop 설치 파일을 다운로드한다.**
   - https://www.docker.com/products/docker-desktop/ 에서 "Download for Windows" 클릭

2. **설치 마법사를 실행한다.**
   - `Docker Desktop Installer.exe`를 더블클릭하여 실행한다.
   - **"Use WSL 2 instead of Hyper-V" 옵션이 반드시 체크**되어 있는지 확인한다.
   - 설치 완료 후 **PC를 재부팅**한다.

3. **Docker Desktop 초기 설정을 확인한다.**
   - Docker Desktop을 실행한다 (시스템 트레��에 고래 아이콘 확인).
   - `Settings (⚙)` → `General`:
     - **"Use the WSL 2 based engine"** 이 체크되어 있는지 확인한다.
     - **"Start Docker Desktop when you log in"** 을 체크한다 (PC 재부팅 후에도 자동 실행).
   - `Settings` → `Resources` → `WSL Integration`:
     - **Ubuntu** (또는 설치한 배포판) 토글이 활성화되어 있는지 확인한다.
   - `Settings` → `Resources` → `Advanced`:
     - Memory: **8 GB 이상** (16 GB 권장)
     - CPU: **4코어 이상** 권장
     - Disk image size: **80 GB 이상** 권장

4. **설치를 검증한다.**

```powershell
docker --version
docker compose version
docker run hello-world
```

세 명령 모두 정상 출력되면 성공���다.

> **성능 팁:** Docker 바인드 마운트의 파일 시스템 성능을 최적화하려면 소스 코드와 데이터를 Windows 파일 시스템(`C:\`)이 아닌 **WSL Linux 파일 시스템**(`\\wsl$\Ubuntu\home\...`)에 저장하는 것이 좋다. 다만 이 가이드에서는 접근 편의를 위해 Windows 경로를 사용한다.

#### Step 3: Git for Windows

1. **Git을 설치한다.**
   - https://git-scm.com/download/win 에서 다운로드 후 설치한다.
   - 설치 옵션에서 **"Git from the command line and also from 3rd-party software"** 를 선택한다.
   - 줄 끝 처리(Line ending): **"Checkout as-is, commit Unix-style line endings"** 선택 권장

2. **CRLF 자동 변환을 설정한다.**

Windows는 줄바꿈에 `CRLF`를 사용하지만, Docker 컨테이너(Linux)는 `LF`를 사용한다. 이 설정이 없으면 셸 스크립트가 `\r` 문자로 인해 실행 실패할 수 있다.

```powershell
git config --global core.autocrlf input
```

3. **설치를 검증한다.**

```powershell
git --version
git config --global core.autocrlf
```

`input`이 출력되면 정상이다.

#### Step 4: Ollama (로컬 LLM 서버)

Ollama는 로컬 GPU/CPU에서 LLM을 실행하는 서버다. 이 프로젝트에서는 DeepEval 심판 LLM, Vision 분석, Zero-Touch QA Brain, 코드 분석 등에 사용한다.

1. **Ollama를 설치한다.**
   - https://ollama.com/download/windows 에서 `OllamaSetup.exe`를 다운로드하여 실행한다.
   - 관리자 권한이 필요하지 않다 (사용자 계정에 설치된다).

2. **GPU 지원 확인:**
   - **NVIDIA GPU:** 최신 드라이버가 설치되어 있으면 Ollama가 자동으로 GPU를 감지한다. CUDA를 별도로 설치할 필요 없다.
   - **AMD GPU:** ROCm 지원이 자동 활성화된다.
   - **GPU 없음:** CPU만으로도 동작하지만 추론 속도가 현저히 느리다.

3. **Ollama 서비스를 시작하고 모델을 다운로드한다.**

Ollama는 설치 후 시스템 트레이에 자동으로 상주한다. 새 PowerShell 창을 열어 모델을 다운로드한다:

```powershell
# 필수 모델 다운로드 (프로젝트에서 사용하는 모델)
ollama pull qwen3-coder:30b
ollama pull llama3.2-vision

# Ollama API 서버 동작 확인
curl http://localhost:11434/api/tags
```

> **참고:** `qwen3-coder:30b` 모델은 약 18 GB, `llama3.2-vision`은 약 4 GB의 디스크 공간이 필요하다. RAM은 모델 파라미터에 비례하여 사용된다 (30B 모델은 약 20 GB VRAM 또는 RAM 필요).

4. **Ollama API 포트 확인:**

Ollama는 기본적으로 `http://localhost:11434`에서 서비스한다. Docker 컨테이너에서는 `http://host.docker.internal:11434`로 접근한다.

#### Step 5: 터미널 환경

이 가이드의 모든 명령은 **PowerShell** 또는 **Git Bash**에서 실행한다.

- **PowerShell 7 (권장):** https://github.com/PowerShell/PowerShell/releases 에서 최신 버전 설치. `mkdir -p` 등 Linux 호환 명령 지원.
- **Git Bash:** Git for Windows 설치 시 함께 설치됨. `bash` 명령 호환.
- **Windows Terminal (권장):** Microsoft Store에서 설치. PowerShell, Git Bash, WSL Ubuntu를 탭으로 전환 가능.

---

### 1.3 시스템 구성

> 이 섹션의 내용은 루트 `readme.md`의 Section 1 "시스템 구성" ~ "최종 산출물"과 동일하다. Phase 1~5 구성, Jenkins Pipeline 8개 목록, 최종 산출물은 루트 가이드를 참조한다.

---

## 2. 고정 전제 및 주소 체계

> 이 섹션의 내용은 루트 `readme.md`의 Section 2와 동일하다. 호스트 브라우저 URL, 컨테이너 내부 URL, 공유 볼륨 경로 모두 OS와 무관하다.

**Windows 환경에서의 `<PROJECT_ROOT>` 경로 예시:**

| 환경 | 경로 예시 |
| --- | --- |
| Windows 기본 | `C:\Users\<Username>\Documents\dscore-ttc` |
| 권장 (짧은 경로) | `C:\dscore-ttc` |

> **주의:** 경로에 한글이나 공백이 포함되면 Docker 볼륨 마운트가 실패할 수 있다. **영문 경로**를 사용한다.

---

## 3. 호스트 환경 설정 및 QAOps 인프라 설치

이 섹션에서는 Docker Compose를 사용하여 Jenkins, SonarQube, GitLab, Langfuse, Dify를 한꺼번에 컨테이너로 구동한다.

> **Docker Compose란?** 여러 컨테이너를 하나의 YAML 파일로 정의하고, `docker compose up` 한 줄로 기동/중지할 수 있는 도구이다.

작업 순서: 3.1 hosts 파일 정리 → 3.2 폴더 생성 → 3.3 Docker 네트워크 → 3.4 docker-compose.yaml 배치 → 3.5 Dockerfile 배치 → 3.6 기동 → 3.7 Dify 연결

### 3.1 `hosts` 파일 정리 (Windows)

Windows의 hosts 파일에 `127.0.0.1 gitlab` 라인이 있으면 GitLab의 external_url 설정과 브라우저 리다이렉트가 충돌하여 UI 접속이 실패할 수 있다.

**hosts 파일 위치:** `C:\Windows\System32\drivers\etc\hosts`

1. **PowerShell을 관리자 권한으로 실행한다.**

2. **현재 hosts 파일에 gitlab 관련 항목이 있는지 확인한다.**

```powershell
Select-String -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Pattern "gitlab"
```

3. **항목이 발견되면 관리자 권한으로 메모장을 열어 해당 라인을 삭제한다.**

```powershell
Start-Process -FilePath notepad.exe -Verb RunAs -ArgumentList "$env:SystemRoot\System32\drivers\etc\hosts"
```

메모장이 관리자 권한으로 열리면 `127.0.0.1 gitlab` 라인을 찾아 삭제하고 저장한다.

> **UAC 프롬프트:** "이 앱이 디바이스를 변경할 수 있도록 허용하시겠습니까?" 팝업이 뜨면 **"예"** 를 클릭한다.

4. **DNS 캐시를 초기화한다.**

```powershell
ipconfig /flushdns
```

`DNS 확인자 캐시를 플러시했습니다.` 메시지가 나오면 성공이다.

> **참고:** Docker Desktop은 `host.docker.internal`과 `gateway.docker.internal` 항목을 hosts 파일에 자동으로 추가한다. 이 항목은 삭제하지 않는다.

### 3.2 프로젝트 디렉터리 구성

Docker 볼륨 마운트에 사용할 폴더 구조를 미리 생성한다.

1. **PowerShell**을 열고 `<PROJECT_ROOT>`로 이동한다.

```powershell
# 프로젝트 루트 생성 (예시)
mkdir C:\dscore-ttc
cd C:\dscore-ttc
```

2. **필수 폴더를 일괄 생성한다.**

```powershell
# 핵심 인프라용 폴더
mkdir -p data\jenkins\scripts
mkdir -p data\knowledges\docs\org
mkdir -p data\knowledges\docs\result
mkdir -p data\knowledges\codes
mkdir -p data\knowledges\qa_reports
mkdir -p data\gitlab
mkdir -p data\sonarqube
mkdir -p data\postgres-sonar

# AI 에이전트 평가 시스템용 (Section 5.1에서 사용)
mkdir -p data\jenkins\scripts\eval_runner\adapters
mkdir -p data\jenkins\scripts\eval_runner\configs
mkdir -p data\jenkins\scripts\eval_runner\tests
mkdir -p data\knowledges\eval\data
mkdir -p data\knowledges\eval\reports
mkdir -p data\postgres-langfuse
```

> **참고:** PowerShell 7에서는 `mkdir -p`가 지원된다. Windows PowerShell 5.1에서는 `New-Item -ItemType Directory -Force -Path` 명령을 사용한다. Git Bash를 사용하면 macOS/Linux와 동일한 `mkdir -p` 명령이 그대로 동작한다.

### 3.3 Docker 네트워크 생성 (필수)

모든 서비스가 서로 통신할 수 있도록 공유 Docker 네트워크를 생성한다.

```powershell
docker network create devops-net
docker network ls
```

`devops-net`이 목록에 보이면 성공이다.

### 3.4 DevOps 스택 (`docker-compose.yaml`) 배치

> 이 파일의 내용은 루트 `readme.md`의 Section 3.4와 **동일하다.** `<PROJECT_ROOT>\docker-compose.yaml`로 저장한다.

**Windows 주의사항:**
- YAML 파일은 반드시 **UTF-8 (BOM 없음)** 인코딩, **LF** 줄바꿈으로 저장한다.
- VS Code에서 저장할 때 하단 상태표시줄에서 `CRLF`를 클릭하여 `LF`로 변경한다.
- 볼륨 마운트 경로의 `./data/...`는 Docker Desktop이 Windows 경로를 자동으로 변환하므로 그대로 사용한다.

### 3.5 Jenkins 커스텀 이미지 (`Dockerfile.jenkins`) 배치

> 이 파일의 내용은 루트 `readme.md`의 Section 3.5와 **동일하다.** `<PROJECT_ROOT>\Dockerfile.jenkins`로 저장한다.

**Windows 주의사항:**
- Dockerfile도 반드시 **UTF-8 (BOM 없음)** 인코딩, **LF** 줄바꿈으로 저장한다.
- `CRLF`로 저장하면 빌드 중 `\r: not found` 에러가 발생한다.

### 3.6 QAOps 스택 기동 및 상태 확인

1. **PowerShell에서 `<PROJECT_ROOT>`로 이동하여 빌드 및 기동한다.**

```powershell
cd C:\dscore-ttc
docker compose up -d --build --force-recreate
```

> **초기 빌드 시간:** Jenkins 커스텀 이미지 빌드에 10~20분이 소요될 수 있다. 인터넷 속도와 PC 성능에 따라 다르다.

2. **컨테이너 상태를 확인한다.**

```powershell
docker compose ps
```

`postgres-sonar`, `sonarqube`, `gitlab`, `db-langfuse`, `langfuse-server`, `jenkins`가 모두 `Up` 상태여야 한다.

3. **GitLab 초기 기동을 확인한다** (3~5분 소요).

```powershell
docker exec -it gitlab gitlab-ctl status
```

주요 프로세스가 `run:` 상태면 정상이다.

4. **Windows Defender 방화벽 알림:**

Docker Desktop이 네트워크 접근 허용을 요청하면 **"액세스 허용"** 을 클릭한다. 차단하면 컨테이너 간 통신이 실패한다.

### 3.7 Dify 스택 설정 및 devops-net 연결 (필수)

> 이 섹션의 절차는 루트 `readme.md`의 Section 3.7과 **동일하다.** 아래는 Windows 경로 기준 명령이다.

1. **`<PROJECT_ROOT>`에서 Dify를 클론한다.**

```powershell
cd C:\dscore-ttc
git clone https://github.com/langgenius/dify.git
```

2. **Dify Docker 폴더로 이동하여 환경변수 파일을 복사한다.**

```powershell
cd C:\dscore-ttc\dify\docker
cp .env.example .env
```

3. **`docker-compose.override.yaml`을 생성한다.**

루트 `readme.md` Section 3.7의 override YAML 내용을 `C:\dscore-ttc\dify\docker\docker-compose.override.yaml`로 저장한다.

4. **Dify를 기동한다.**

```powershell
docker compose up -d
```

> **WSL 2 관련 참고:** Dify Docker Compose의 API URL이 WSL과 Windows에서 중복 포워딩되면 프론트엔드 접속이 실패할 수 있다. 이 경우 `.env` 파일에서 `CONSOLE_API_URL`과 `SERVICE_API_URL`이 `http://localhost`로 설정되어 있는지 확인한다.

5. **네트워크 연결을 확인한다.**

```powershell
docker exec -it jenkins sh -c "curl -sS -o /dev/null -w '%{http_code}\n' http://api:5001/ || true"
```

HTTP 응답 코드(예: `404`)가 출력되면 정상이다.

---

## 4. 초기 설정 및 인증 토큰 발급

> 이 섹션의 내용은 루트 `readme.md`의 Section 4와 **동일하다.** Dify, Jenkins, GitLab, SonarQube 모두 웹 UI에서 조작하므로 OS와 무관하다.

**Windows에서의 Jenkins 초기 비밀번호 확인:**

```powershell
docker exec -it jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

나머지 절차(Dify 지식베이스 준비, Credentials 등록, 토큰 발급)는 루트 가이드 Section 4.1 ~ 4.4를 참조한다.

---

## 5. 기능별 파이프라인 상세구현

> 이 섹션의 내용은 루트 `readme.md`의 Section 5와 **동일하다.** Python 스크립트, Jenkinsfile, Dify Workflow 설정은 모두 Docker 컨테이너 또는 Jenkins 웹 UI에서 실행되므로 OS에 의존하지 않는다.

각 파이프라인의 상세 코드와 설정은 루트 가이드의 해당 섹션을 참조한다:

| 하위 섹션 | 파이프라인 | 루트 가이드 참조 |
| --- | --- | --- |
| 5.1 | DSCORE-TTC AI평가 | `readme.md` Section 5.1 |
| 5.2 | DSCORE-TTC 웹스크래핑 | `readme.md` Section 5.2 |
| 5.3 | DSCORE-TTC 지식주입 (문서) | `readme.md` Section 5.3 |
| 5.4 | DSCORE-TTC 지식주입 (Q&A) | `readme.md` Section 5.4 |
| 5.5 | DSCORE-TTC 코드 사전학습 | `readme.md` Section 5.5 |
| 5.6 | DSCORE-TTC 코드 정적분석 | `readme.md` Section 5.6 |
| 5.7 | DSCORE-TTC 코드 정적분석 결과분석 및 이슈등록 | `readme.md` Section 5.7 |
| 5.8 | DSCORE-ZeroTouch-QA | `readme.md` Section 5.8 |

**단, Section 5.8.4 (Jenkins 에이전트 구축)만 Windows 환경에 맞게 아래에서 별도로 안내한다.**

---

### 5.8.4 Jenkins 인프라 및 에이전트 구축 가이드 (Windows Local)

E2E 테스트는 실제 브라우저 화면이 필요하지만 Docker 컨테이너에는 GUI가 없다. 이를 해결하기 위해 Windows PC를 Jenkins 에이전트(노드)로 직접 연결하여 화면이 보이는(Headed) 테스트 환경을 구축한다.

#### 5.8.4.1 Java 17 및 디렉터리 세팅 (Windows)

Jenkins 에이전트는 Java 17 이상에서 동작한다 (Jenkins 2.463 이후 필수).

1. **Eclipse Temurin JDK 17을 설치한다.**

   - https://adoptium.net/temurin/releases/ 에서 **Windows x64 .msi** 설치 파일 다운로드 (JDK 17 또는 21)
   - 설치 마법사에서 **"Set JAVA_HOME variable"** 옵션을 체크한다.
   - 설치 경로 예시: `C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot`

2. **환경변수를 확인한다.**

```powershell
java -version
echo $env:JAVA_HOME
```

`openjdk version "17.x.x"` 및 JAVA_HOME 경로가 출력되면 성공이다. 출력이 없으면 수동으로 환경변수를 설정한다:

```powershell
# 시스템 환경변수 수동 설정 (관리자 PowerShell)
[System.Environment]::SetEnvironmentVariable("JAVA_HOME", "C:\Program Files\Eclipse Adoptium\jdk-17.0.13+11", "Machine")
[System.Environment]::SetEnvironmentVariable("Path", "$env:Path;C:\Program Files\Eclipse Adoptium\jdk-17.0.13+11\bin", "Machine")
```

설정 후 PowerShell을 닫고 다시 열어 적용한다.

3. **에이전트 및 테스트 실행 워크스페이스를 생성한다.**

```powershell
mkdir -p C:\jenkins_agent
mkdir -p C:\automation\local_qa
```

#### 5.8.4.2 Jenkins Master 노드 생성 (Jenkins UI)

Jenkins 웹 UI(`http://localhost:8080`)에서 다음 절차를 수행한다.

1. `Jenkins 관리` > `Nodes` > `New Node` 선택
2. 노드 이름: `windows-local-agent` (Permanent Agent)
3. 설정값:
   - **Number of executors:** `1` (UI 테스트 충돌 방지를 위해 단일 실행자 사용)
   - **Remote root directory:** `C:\jenkins_agent`
   - **Labels:** `windows-ui-tester`
   - **Usage:** `Only build jobs with label expressions matching this node`
   - **Launch method:** `Launch agent by connecting it to the controller` (Inbound Agent)

> **주의:** 루트 `readme.md`에서는 Label이 `mac-ui-tester`이지만, Windows 환경에서는 `windows-ui-tester`로 설정한다. Jenkinsfile에서 `agent { label 'mac-ui-tester' }` 부분을 `agent { label 'windows-ui-tester' }`로 변경해야 한다.

#### 5.8.4.3 에이전트 연결 및 Windows 보안 설정

1. **Jenkins UI에서 에이전트 연결 명령을 확인한다.**

   노드 생성 후 Jenkins가 제공하는 연결 명령을 확인한다:
   `Jenkins 관리` → `Nodes` → `windows-local-agent` → 상태 페이지에서 `java -jar agent.jar ...` 명령을 복사한다.

2. **에이전트를 실행한다.**

```powershell
cd C:\jenkins_agent

# Jenkins가 제공한 명령 실행 (예시)
java -jar agent.jar -url http://localhost:8080/ -secret <에이전트시크릿> -name windows-local-agent -workDir "C:\jenkins_agent"
```

`Connected` 메시지가 출력되면 정상이다.

3. **에이전트를 Windows 서비스로 등록한다 (선택사항).**

   PC 재부팅 시에도 자동으로 에이전트가 실행되게 하려면 NSSM(Non-Sucking Service Manager)을 사용하여 Windows 서비스로 등록할 수 있다.

```powershell
# NSSM 설치 (winget 사용)
winget install nssm

# 서비스 등록
nssm install JenkinsAgent "C:\Program Files\Eclipse Adoptium\jdk-17.0.13+11\bin\java.exe" "-jar C:\jenkins_agent\agent.jar -url http://localhost:8080/ -secret <시크릿> -name windows-local-agent -workDir C:\jenkins_agent"
nssm start JenkinsAgent
```

4. **Windows 보안 설정:**

macOS와 달리 Windows에서는 별도의 "화면 기록" 또는 "접근성" 권한을 부여할 필요가 없다. 단, 다음 사항을 확인한다:

| 항목 | 확인 방법 | 미설정 시 증상 |
| --- | --- | --- |
| **Windows Defender SmartScreen** | 에이전트 실행 시 "Windows가 PC를 보호했습니다" 팝업 → "추가 정보" → "실행" | agent.jar 실행 차단 |
| **방화벽 인바운드 규칙** | 설정 → 네트워크 → Windows 방화벽 → 고급 설정 → Java 허용 | Jenkins Master와 통신 차단 |
| **화면 보호기 / 절전 모드 해제** | 설정 → 시스템 → 전원 → "화면 끄기: 없음", "절전 모드: 없음" | Headed 테스트 중 화면 잠김으로 테스트 실패 |
| **UAC (사용자 계정 컨트롤)** | 에이전트 실행 계정에 관리자 권한 필요 없음. 단, Playwright 브라우저가 차단되면 UAC 레벨 조정 | 브라우저 실행 차단 |

5. **Playwright 브라우저 설치 (에이전트 PC에서):**

```powershell
pip install playwright
python -m playwright install chromium
```

---

## 6. 샘플 프로젝트 구성 및 테스트

> 이 섹션의 내용은 루트 `readme.md`의 Section 6과 **동일하다.** GitLab, SonarQube, Jenkins 조작은 모두 웹 브라우저에서 수행한다.

**Windows에서의 Git Push 시 참고:**

```powershell
# Git Bash 또는 PowerShell에서 동일하게 실행
git init
git add .
git commit -m "Initial commit with code smells"
git remote add origin http://localhost:8929/root/dscore-ttc-sample.git
git push -u origin main
```

나머지 절차는 루트 가이드 Section 6.1 ~ 6.3을 참조한다.

---

## 7. 트러블슈팅

> 루트 `readme.md` Section 7의 공통 트러블슈팅(문서 변환, Dify 업로드, Vision, SonarQube, GitLab, 웹 스크래핑)은 Docker 내부 문제이므로 OS와 무관하게 동일하다. 루트 가이드 Section 7.1 ~ 7.6을 참조한다.

아래는 **Windows 환경에서만 발생하는 추가 트러블슈팅**이다.

### 7.W1 WSL 2 관련

**문제: `wsl --install` 실행 시 "가상 머신 플랫폼" 오류**

**원인:**
- BIOS/UEFI에서 하드웨어 가상화(Intel VT-x / AMD-V)가 비활성화되어 있다.

**해결:**
1. PC를 재부팅하여 BIOS/UEFI에 진입한다 (부팅 시 `F2`, `F10`, `DEL` 등 제조사별 키).
2. `Advanced` → `CPU Configuration`에서 `Intel Virtualization Technology` 또는 `SVM Mode`를 `Enabled`로 변경한다.
3. 저장 후 재부팅하여 `wsl --install`을 다시 실행한다.

**문제: WSL 2 배포판이 시작되지 않는다 ("WslRegisterDistribution failed")**

**해결:**
```powershell
# WSL 커널 강제 업데이트
wsl --update --web-download

# 가상 머신 플랫폼 기능 수동 활성화
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# PC 재부팅 후 다시 시도
Restart-Computer
```

### 7.W2 Docker Desktop 관련

**문제: Docker Desktop 시작 시 "Docker Desktop - WSL distro terminated abruptly"**

**해결:**
```powershell
# WSL 강제 종료 및 재시작
wsl --shutdown
# Docker Desktop을 다시 실행한다.
```

**문제: `docker compose up` 시 볼륨 마운트 오류 ("Error response from daemon: mkdir ... permission denied")**

**원인:**
- Docker Desktop의 파일 공유 설정에서 프로젝트 드라이브가 허용되지 않았다.

**해결:**
1. Docker Desktop → Settings → Resources → File sharing
2. 프로젝트가 위치한 드라이브(예: `C:\`)가 목록에 포함되어 있는지 확인한다.
3. 없으면 추가하고 Docker Desktop을 재시작한다.

**문제: 컨테이너에서 `host.docker.internal` 접근 불가 (Ollama 연결 실패)**

**원인:**
- Docker Desktop이 hosts 파일의 `host.docker.internal` 항목을 잘못된 IP로 설정했다.

**해결:**
```powershell
# 현재 설정 확인
Select-String -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Pattern "host.docker.internal"

# 잘못된 IP가 있으면 hosts 파일을 관리자 메모장으로 열어 수정
# host.docker.internal 항목을 삭제하고 Docker Desktop을 재시작하면 올바른 IP로 재생성된다.
```

### 7.W3 CRLF 줄바꿈 관련

**문제: Docker 빌드 또는 컨테이너 실행 시 `\r: not found` 또는 `bad interpreter` 에러**

**원인:**
- Windows에서 생성한 파일이 CRLF(`\r\n`) 줄바꿈을 사용하여 Linux 컨테이너에서 인식하지 못한다.

**해결:**
1. Git 설정 확인:
```powershell
git config --global core.autocrlf input
```

2. 기존 파일의 줄바꿈 변환 (Git Bash에서 실행):
```bash
find . -name "*.sh" -o -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "Dockerfile*" | xargs dos2unix
```

또는 VS Code에서 해당 파일을 열고 하단 상태표시줄의 `CRLF`를 클릭하여 `LF`로 변경 후 저장한다.

3. `.gitattributes` 파일을 프로젝트 루트에 추가하여 자동 처리한다:
```
* text=auto eol=lf
*.sh text eol=lf
*.py text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
Dockerfile* text eol=lf
Jenkinsfile text eol=lf
```

### 7.W4 Ollama GPU 관련

**문제: Ollama가 GPU를 인식하지 못한다 (CPU만 사용)**

**해결:**
```powershell
# GPU 인식 상태 확인
ollama ps

# NVIDIA 드라이버 확인
nvidia-smi
```

GPU가 표시되지 않으면:
1. https://www.nvidia.com/drivers 에서 최신 드라이버를 다운로드하여 설치한다.
2. PC를 재부팅한다.
3. Ollama를 재시작한다 (시스템 트레이 아이콘 → Quit → 재실행).

### 7.W5 Jenkins 에이전트 관련

**문제: Jenkins 에이전트가 연결되지 않는다**

**해결:**
1. Java 버전 확인:
```powershell
java -version
# 17 이상이어야 한다
```

2. 방화벽 확인:
```powershell
# Jenkins 포트(8080, 50000)가 열려 있는지 확인
Test-NetConnection -ComputerName localhost -Port 8080
Test-NetConnection -ComputerName localhost -Port 50000
```

3. Docker 포트 매핑 확인 (docker-compose.yaml에 `50000:50000` 포함되어 있는지):
```powershell
docker port jenkins
```

### 7.W6 Windows 절전/화면 잠금 관련

**문제: Headed E2E 테스트 중 화면이 꺼지거나 잠겨서 테스트가 실패한다**

**해결:**
```powershell
# PowerShell로 절전 모드 비활성화 (관리자)
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0

# 화면 보호기 비활성화
# 설정 → 개인 설정 → 잠금 화면 → 화면 보호기 설정 → "없음" 선택
```

---

## 부록: macOS 가이드와의 차이점 요약

| 항목 | macOS (루트 readme.md) | Windows (이 문서) |
| --- | --- | --- |
| 필수 SW | Docker Desktop, Git, Terminal | WSL 2, Docker Desktop (WSL backend), Git for Windows, PowerShell 7 |
| Ollama 설치 | `brew install ollama` 또는 공식 앱 | `OllamaSetup.exe` (네이티브, WSL 불필요) |
| Java 설치 | `brew install openjdk@17` | Eclipse Temurin JDK 17 MSI 설치 |
| hosts 파일 | `/etc/hosts`, `sudo` 편집 | `C:\Windows\System32\drivers\etc\hosts`, 관리자 메모장 |
| DNS 캐시 초기화 | `sudo dscacheutil -flushcache` | `ipconfig /flushdns` |
| 경로 구분자 | `/` (슬래시) | `\` (백슬래시), Docker 내부는 `/` |
| 프로젝트 경로 예시 | `~/Developer/dscore-ttc` | `C:\dscore-ttc` |
| Jenkins 에이전트 Label | `mac-ui-tester` | `windows-ui-tester` |
| 보안 권한 | macOS 화면 기록 + 접근성 | Windows Defender SmartScreen + 방화벽 + 절전 해제 |
| 줄바꿈 | LF (기본) | CRLF → LF 변환 필수 (`core.autocrlf input`) |
| 파일 인코딩 | UTF-8 (기본) | UTF-8 BOM 없음으로 저장 필수 |
