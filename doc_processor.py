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
# Dify docker-compose.override.yaml에서 api 서비스가 devops-net에 붙어 있어야 한다.
DIFY_API_BASE = "http://api:5001/v1"

# 지식 원본 폴더
# - 사용자가 여기에 원본 문서파일을 넣는다.
SOURCE_DIR = "/var/knowledges/docs/org"

# 지식 결과 폴더
# - 변환 결과(MD, PDF)가 여기에 생성된다.
RESULT_DIR = "/var/knowledges/docs/result"


# ---------------------------------------------------------
# 유틸리티 함수
# ---------------------------------------------------------

def ensure_dirs():
    """
    결과 디렉터리가 없으면 생성한다.
    """
    os.makedirs(RESULT_DIR, exist_ok=True)


def clean_result_dir():
    """
    이전 실행의 결과물을 제거한다.
    PoC에서는 중복 업로드를 피하기 위해 result를 매번 비우는 방식이 가장 단순하다.
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
    pandas의 to_markdown(tabulate 의존)을 사용하지 않고,
    최소한의 마크다운 테이블을 직접 생성한다.
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
    Dataset의 doc_form을 조회한다.
    doc_form mismatch 오류를 피하기 위해, 업로드 전에 dataset의 doc_form을 확인한다.
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
    MD 파일을 Dify에 텍스트 문서로 업로드한다.
    Dify API 문서의 create-by-text 엔드포인트를 사용한다.  :contentReference[oaicite:3]{index=3}
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
    스크립트 사용법을 출력한다.
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
        ensure_dirs()
        clean_result_dir()

        print("[Convert] start")

        for root, _, files in os.walk(SOURCE_DIR):
            for name in files:
                if name.startswith("~$"):
                    continue

                full_path = os.path.join(root, name)
                ext = name.lower().split(".")[-1]

                if ext == "pptx":
                    pdf_path = convert_pptx_to_pdf(full_path, name)
                    # PPTX는 PDF로 변환하는 것이 핵심이다.
                    # Q&A Dataset 업로드를 위해, 변환된 PDF에서 텍스트를 추출한 MD도 함께 만든다.
                    if pdf_path:
                        convert_to_md(pdf_path, os.path.basename(pdf_path))

                elif ext in ["txt", "docx", "xlsx", "xls", "pdf"]:
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
        fail = upload_files(dataset_id, api_key, doc_form, doc_language)
        print("[Upload] done")

        # 업로드 실패가 1건이라도 있으면 Jenkins에서 실패로 표시되도록 exit code를 비정상으로 종료한다.
        if fail > 0:
            sys.exit(2)
        sys.exit(0)

    print_usage()
    sys.exit(1)
