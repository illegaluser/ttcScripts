import os
import sys
import json
import shutil
import subprocess
import requests
import pandas as pd
import fitz  # PyMuPDF(pymupdf)가 제공하는 모듈 이름은 fitz다.
from docx import Document

# ---------------------------------------------------------
# 고정 환경값
# ---------------------------------------------------------

# Dify API는 Jenkins 컨테이너 내부에서 Dify 컨테이너(api)로 접근한다.
# - docker-compose.override.yaml에서 api 서비스가 devops-net에 붙어 있어야 한다.
# - 파이프라인이 컨테이너 안에서 실행되므로 http://api:5001/v1로 고정한다.
# - 별도 프록시/도메인 설정을 섞지 말고 이 베이스만 사용한다.
# - readme.md의 네트워크 체계(컨테이너 내부 고정 주소)에 맞춰야 Dify 업로드가 실패하지 않는다.
DIFY_API_BASE = "http://api:5001/v1"

# 지식 원본 폴더
# - 사용자가 여기에 원본 문서파일을 넣는다.
# - Jenkins와 호스트가 공유하는 볼륨(/var/knowledges/docs/org)로 고정
# - readme.md Section 1.3 “공유 볼륨” 경로와 동일하게 맞춰야 파이프라인이 동일 경로를 참조한다.
SOURCE_DIR = "/var/knowledges/docs/org"

# 지식 결과 폴더
# - 변환 결과(MD, PDF)가 여기에 생성된다.
# - 이후 업로드 단계(upload mode)에서 이 폴더를 스캔한다.
# - convert → upload 두 단계로 나뉜 Jenkins Job에서 공용 결과 디렉터리 역할을 한다.
RESULT_DIR = "/var/knowledges/docs/result"


# ---------------------------------------------------------
# 유틸리티 함수
# ---------------------------------------------------------

def ensure_dirs():
    """
    결과 디렉터리(RESULT_DIR)가 없으면 생성한다.
    - Jenkins 컨테이너 내에서 실행될 때도 동일한 절대경로를 쓴다.
    - 경로 유무를 먼저 보장해 변환·업로드 단계에서 '경로 없음' 오류를 예방한다.
    - exist_ok=True로 이미 있을 때도 예외 없이 넘어간다.
    - readme.md의 “지식 관리 자동화” 파이프라인(DSCORE-Knowledge-Sync 시리즈)에서
      convert 단계가 실패 없이 시작되도록 사전 준비를 해둔다.
    """
    os.makedirs(RESULT_DIR, exist_ok=True)


def clean_result_dir():
    """
    이전 실행의 결과물을 제거한다. (파일/폴더 모두 삭제)
    - PoC에서는 증분 업로드를 처리하지 않으므로 매번 비우는 방식이 가장 단순하다.
    - 오래된 파일이 남아 잘못 업로드되는 상황을 막아준다.
    - RESULT_DIR가 없으면 조용히 반환해 불필요한 오류를 피한다.
    - readme.md의 Jenkins Job 예시에서 convert 단계 시작 시마다 호출되어,
      이전 파이프라인 산출물이 섞이지 않도록 하는 안전장치다.
    """
    if not os.path.exists(RESULT_DIR):
        return
    for name in os.listdir(RESULT_DIR):
        path = os.path.join(RESULT_DIR, name)
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path, ignore_errors=True)


def df_to_markdown_simple(df: pd.DataFrame) -> str:
    """
    pandas의 to_markdown(tabulate 의존)을 사용하지 않고 최소한의 마크다운 테이블을 생성한다.
    - 멀티 인덱스, 서식 지정 등은 고려하지 않는다.
    - NaN은 빈 문자열로 치환해 Dify 업로드 시 불필요한 'nan' 문자열이 나타나지 않도록 한다.
    - tabulate 설치가 안 된 환경에서도 바로 동작하도록 직접 테이블 문자열을 만든다.
    - 열 순서는 DataFrame의 열 순서를 그대로 따르며, 모든 값을 문자열로 변환한다.
    - readme.md에서 “지식 업로드” 단계는 Jenkins 슬림 이미지 기준이므로,
      추가 패키지 설치 없이도 테이블을 만들 수 있게 최소 구현으로 유지한다.
    """
    if df is None or df.empty:
        return ""

    cols = [str(c) for c in df.columns.tolist()]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"

    rows = []
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row.get(c, "")
            vals.append("" if pd.isna(v) else str(v))
        rows.append("| " + " | ".join(vals) + " |")

    return "\n".join([header, sep] + rows)


# ---------------------------------------------------------
# 변환 함수
# ---------------------------------------------------------

def convert_to_md(filepath: str, filename: str):
    """
    TXT, DOCX, XLSX/XLS, PDF를 Markdown 텍스트로 변환하여 result에 저장한다.

    출력 파일명 규칙
    - 원본이 example.pdf이면 example.pdf.md 형태로 저장한다.
    - 원본명 보존이 목적이다.
    처리 방식
    - TXT: 원문을 그대로 사용한다.
    - DOCX: 비어 있지 않은 단락을 이어 붙인다.
    - XLSX/XLS: 시트별로 제목을 붙이고 간단한 테이블 문자열로 변환한다.
    - PDF: 각 페이지의 텍스트를 순서대로 합친다.
    - PPTX는 convert_pptx_to_pdf에서 PDF를 만든 뒤 여기로 넘어와 MD를 추가로 생성한다.
    사소하지만 중요한 포인트
    - 파일 인코딩 문제를 피하려고 errors="ignore"로 열어 깨진 문자도 무시하고 진행한다.
    - 변환 결과가 비어 있으면 저장을 생략해 불필요한 빈 파일을 만들지 않는다.
    - readme.md의 Jenkins Job #1/#2/#3(문서/Q&A/Vision 지식 동기화)에서
      원본 포맷을 모두 Markdown 텍스트로 통일해 Dify에 업로드하기 위한 전처리 단계다.
    - 시트 이름이나 PDF 페이지 순서를 유지해, 이후 검색·질의 응답 시 원본 구조를 추적하기 쉽다.
    """
    ext = filename.lower().split(".")[-1]
    md_content = ""
    target_path = os.path.join(RESULT_DIR, f"{filename}.md")

    print(f"[Convert:MD] {filename}")

    try:
        if ext == "txt":
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                md_content = f.read()

        elif ext == "docx":
            doc = Document(filepath)
            parts = []
            for para in doc.paragraphs:
                t = para.text.strip()
                if t:
                    parts.append(t)
            md_content = "\n\n".join(parts)

        elif ext in ["xlsx", "xls"]:
            xls = pd.ExcelFile(filepath)
            parts = []
            for sheet_name in xls.sheet_names:
                parts.append(f"## Sheet: {sheet_name}")
                df = pd.read_excel(xls, sheet_name=sheet_name)
                table = df_to_markdown_simple(df)
                if table:
                    parts.append(table)
                parts.append("")
            md_content = "\n\n".join(parts)

        elif ext == "pdf":
            doc = fitz.open(filepath)
            parts = []
            for page in doc:
                parts.append(page.get_text())
            md_content = "\n\n".join(parts)

        if md_content.strip():
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(f"# {filename}\n\n{md_content}\n")
            print(f"[Saved] {target_path}")

    except Exception as e:
        print(f"[Error] convert_to_md failed: {filename} / {e}")


def convert_pptx_to_pdf(filepath: str, filename: str) -> str:
    """
    PPTX를 LibreOffice(soffice) headless 모드로 PDF로 변환한다.

    반환값
    - 변환된 PDF 파일의 전체 경로를 반환한다.
    - 실패하면 빈 문자열을 반환한다.
    사용 이유
    - Dify 업로드 전에 PPTX를 PDF로 변환하고, 필요시 PDF에서 텍스트를 추출한 MD도 생성하기 위함이다.
    - headless 모드만 사용하므로 Jenkins 에이전트에서도 GUI 의존성이 없다.
    - soffice가 설치되어 있어야 동작하며, 실패 시 예외를 잡아 빈 문자열을 반환한다.
    - 표준 출력/에러는 버려 로그를 간결하게 유지한다.
    - readme.md의 “지식 업로드” 파이프라인에서 PPTX는 바로 텍스트 추출이 어렵기 때문에,
      PDF 중간 변환을 거쳐 텍스트/파일 업로드 모두를 지원하는 구조다.
    """
    print(f"[Convert:PDF] {filename}")

    try:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", RESULT_DIR, filepath],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        pdf_name = filename.rsplit(".", 1)[0] + ".pdf"
        pdf_path = os.path.join(RESULT_DIR, pdf_name)

        if os.path.exists(pdf_path):
            print(f"[Saved] {pdf_path}")
            return pdf_path

        print(f"[Error] PPTX conversion output not found: {pdf_path}")
        return ""

    except Exception as e:
        print(f"[Error] convert_pptx_to_pdf failed: {filename} / {e}")
        return ""


# ---------------------------------------------------------
# Dify API 연동 함수
# ---------------------------------------------------------

def get_dataset_doc_form(dataset_id: str, api_key: str) -> str:
    """
    Dataset의 doc_form을 조회한다. (qa_model / document / 기타)
    업로드 요청 시 지정한 doc_form과 Dataset 설정이 다르면 Dify가 오류를 반환하므로,
    사전 조회로 불일치 여부를 검증한다.
    - 요청 실패나 예상치 못한 응답은 빈 문자열로 처리해 이후 로직이 기본값 흐름을 따른다.
    - 네트워크 예외는 조용히 무시하고 빈 문자열을 반환한다.
    - readme.md의 Job #2(QA 모델)처럼 doc_form이 특정되어 있을 때,
      사용자가 CLI에서 doc_form을 잘못 넘겨도 여기서 감지해 업로드를 중단한다.
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        return data.get("doc_form", "")
    except Exception:
        return ""


def upload_md(dataset_id: str, api_key: str, file_path: str, doc_form: str, doc_language: str) -> requests.Response:
    """
    MD 파일을 Dify에 텍스트 문서로 업로드한다. (create-by-text 엔드포인트)
    - doc_form이 주어지면 payload에 명시해 Dataset 설정과 일치하도록 한다.
    - doc_language가 있으면 검색/질의 품질을 높이기 위해 함께 전달한다.
    - indexing_technique는 high_quality로 고정해 검색 품질을 우선한다.
    - process_rule은 자동 처리로 설정한다.
    - 파일은 UTF-8로 읽되 errors="ignore"로 깨진 문자를 무시한다.
    - readme.md의 “Knowledge Base 업로드” 예시에서 사용하는 엔드포인트와 동일하다.
    - doc_language는 비워도 되지만, 명시하면 LLM 검색 성능이 조금 더 향상될 수 있다.
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-text"
    headers = {"Authorization": f"Bearer {api_key}"}

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    payload = {
        "name": os.path.basename(file_path),
        "text": content,
        "indexing_technique": "high_quality",
        "process_rule": {"mode": "automatic"},
    }

    if doc_form:
        payload["doc_form"] = doc_form

    if doc_language:
        payload["doc_language"] = doc_language

    return requests.post(url, headers=headers, json=payload, timeout=60)


def upload_pdf(dataset_id: str, api_key: str, file_path: str) -> requests.Response:
    """
    PDF 파일을 Dify에 파일로 업로드한다.
    Dify API 문서의 create-by-file 엔드포인트를 사용한다. :contentReference[oaicite:4]{index=4}

    주의
    - create-by-file 문서 스키마에는 doc_form이 명시되어 있지 않다.
    - PoC에서는 파일 업로드는 문서형 Dataset에서만 수행한다.
    - Q&A Dataset(qa_model)에서는 PDF를 파일로 업로드하지 않고, PDF 텍스트를 MD로 올린다.
    - 업로드 시 process_rule은 자동 처리로 고정한다.
    - PDF는 바이너리로 전송하며, requests의 files 인자를 사용한다.
    - readme.md Job #1(문서형)에서는 PDF 업로드가 활성화되며,
      Job #2(QA형)에서는 convert_to_md를 통해 텍스트만 올린다.
    """
    url = f"{DIFY_API_BASE}/datasets/{dataset_id}/document/create-by-file"
    headers = {"Authorization": f"Bearer {api_key}"}

    data = {
        "indexing_technique": "high_quality",
        "process_rule": json.dumps({"mode": "automatic"}),
    }

    files = {
        "file": (os.path.basename(file_path), open(file_path, "rb"), "application/pdf")
    }

    return requests.post(url, headers=headers, data=data, files=files, timeout=120)


def upload_files(dataset_id: str, api_key: str, requested_doc_form: str = "", doc_language: str = "") -> int:
    """
    result 폴더의 파일을 스캔하여 Dify로 업로드한다.

    동작 규칙
    - doc_form이 qa_model이면:
      - MD만 업로드한다.
      - PDF 파일 업로드는 수행하지 않는다.
      - PDF 원본이 있으면, 그 PDF에서 추출한 *.pdf.md를 업로드 대상으로 삼는다.
    - doc_form이 비어 있거나 qa_model이 아니면:
      - MD는 create-by-text로 업로드한다.
      - PDF는 create-by-file로 업로드한다.

    반환값
    - 실패 개수를 반환한다.
    추가 검증
    - 호출 시 전달된 doc_form과 Dataset의 doc_form이 다르면 즉시 중단해 잘못된 업로드를 막는다.
    - doc_language는 optional로 전달하며, Dify 측이 이를 사용하지 않아도 무해하다.
    업로드 시나리오 예시
    - 문서형 Dataset(document): PDF 원본은 파일로, 기타/추출물은 MD로 업로드한다.
    - Q&A Dataset(qa_model): PDF 파일 업로드를 건너뛰고, 추출한 *.pdf.md만 올린다.
    - doc_form 파라미터를 CLI에서 지정하면 Dataset 설정과 일치하는지 확인해 불일치 시 에러를 낸다.
    실패 처리
    - 각 파일 업로드 실패 시 fail_count를 올리고 오류 메시지를 출력한다.
    - 전체 완료 후 fail_count가 0보다 크면 호출부에서 비정상 종료 코드로 반환한다.
    - readme.md Stage 1/2에서 Jenkins가 이 함수를 호출하며, 실패 count가 0보다 크면
      파이프라인이 실패로 표시되어 문제를 즉시 알 수 있다.
    - QA(doc_form=qa_model)일 때 PDF 원본을 건너뛰는 분기 로직이 핵심이다.
    """
    if not os.path.exists(RESULT_DIR):
        print("[Upload] result directory not found")
        return 1

    dataset_doc_form = get_dataset_doc_form(dataset_id, api_key)
    effective_doc_form = requested_doc_form or dataset_doc_form

    if requested_doc_form and dataset_doc_form and requested_doc_form != dataset_doc_form:
        print(f"[Upload:Error] requested doc_form={requested_doc_form} but dataset doc_form={dataset_doc_form}")
        print("[Upload:Error] dataset의 doc_form과 업로드 doc_form이 일치해야 한다.")
        return 1

    print(f"[Upload] scan: {RESULT_DIR}")
    print(f"[Upload] doc_form: {effective_doc_form if effective_doc_form else '(not set)'}")
    print(f"[Upload] doc_language: {doc_language if doc_language else '(not set)'}")

    fail_count = 0

    for root, _, files in os.walk(RESULT_DIR):
        for name in files:
            path = os.path.join(root, name)
            ext = name.lower().split(".")[-1]

            try:
                if ext == "md":
                    resp = upload_md(dataset_id, api_key, path, effective_doc_form, doc_language)
                    if resp.status_code == 200:
                        print(f"[Upload:OK] {name}")
                    else:
                        fail_count += 1
                        print(f"[Upload:FAIL] {name} / {resp.status_code} / {resp.text}")

                elif ext == "pdf":
                    if effective_doc_form == "qa_model":
                        print(f"[Upload:SKIP] {name} (qa_model에서는 PDF 파일 업로드를 생략한다)")
                        continue

                    resp = upload_pdf(dataset_id, api_key, path)
                    if resp.status_code == 200:
                        print(f"[Upload:OK] {name}")
                    else:
                        fail_count += 1
                        print(f"[Upload:FAIL] {name} / {resp.status_code} / {resp.text}")

            except Exception as e:
                fail_count += 1
                print(f"[Upload:Error] {name} / {e}")

    return fail_count


# ---------------------------------------------------------
# 메인
# ---------------------------------------------------------

def print_usage():
    """
    스크립트 사용법을 출력한다. (명령 인자 부족 시 호출)
    - convert: SOURCE_DIR의 문서를 RESULT_DIR로 변환만 수행한다.
    - upload: RESULT_DIR의 산출물을 Dify Dataset으로 업로드한다.
    - doc_form/doc_language는 선택 인자로, Dataset 설정과 불일치 시 업로드를 막는다.
    - readme.md의 Jenkins 샘플 파이프라인 명령과 동일한 인자 구성을 유지해 혼란을 줄인다.
    """
    print("Usage:")
    print("  python doc_processor.py convert")
    print("  python doc_processor.py upload <dataset_id> <api_key> [doc_form] [doc_language]")
    print("")
    print("Examples:")
    print("  python doc_processor.py upload <id> <key>")
    print("  python doc_processor.py upload <id> <key> qa_model Korean")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    mode = sys.argv[1].strip()

    if mode == "convert":
        # 1) 변환 전 환경 준비: 결과 디렉터리를 만들고 기존 결과물을 비운다.
        ensure_dirs()
        clean_result_dir()

        print("[Convert] start")

        for root, _, files in os.walk(SOURCE_DIR):
            for name in files:
                # Office 임시 파일(~$로 시작)은 건너뛴다.
                if name.startswith("~$"):
                    continue

                full_path = os.path.join(root, name)
                ext = name.lower().split(".")[-1]

                if ext == "pptx":
                    # PPTX는 먼저 PDF로 변환한다.
                    # Q&A Dataset에서도 PDF 텍스트를 활용할 수 있도록, PDF에서 MD도 추가 생성한다.
                    # readme.md의 지침대로, PPTX 업로드는 PDF 변환을 필수로 거쳐야 한다.
                    pdf_path = convert_pptx_to_pdf(full_path, name)
                    # PPTX는 PDF로 변환하는 것이 핵심이다.
                    # Q&A Dataset 업로드를 위해, 변환된 PDF에서 텍스트를 추출한 MD도 함께 만든다.
                    if pdf_path:
                        convert_to_md(pdf_path, os.path.basename(pdf_path))

                elif ext in ["txt", "docx", "xlsx", "xls", "pdf"]:
                    # 나머지 포맷은 바로 MD로 변환해 결과 디렉터리에 저장한다.
                    # readme.md의 DSCORE-Knowledge-Sync 흐름에서 지원 포맷은 이 목록에 포함된다.
                    convert_to_md(full_path, name)

        print("[Convert] done")
        sys.exit(0)

    if mode == "upload":
        if len(sys.argv) < 4:
            print_usage()
            sys.exit(1)

        dataset_id = sys.argv[2].strip()
        api_key = sys.argv[3].strip()
        doc_form = sys.argv[4].strip() if len(sys.argv) >= 5 else ""
        doc_language = sys.argv[5].strip() if len(sys.argv) >= 6 else ""

        print("[Upload] start")
        # 변환 결과를 스캔해 Dataset에 업로드한다.
        fail = upload_files(dataset_id, api_key, doc_form, doc_language)
        print("[Upload] done")

        # 업로드 실패가 1건이라도 있으면 Jenkins에서 실패로 표시되도록 exit code를 비정상으로 종료한다.
        if fail > 0:
            sys.exit(2)
        sys.exit(0)

    print_usage()
    sys.exit(1)
