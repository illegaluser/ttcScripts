#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
from datetime import datetime

# 목적: readme.md의 DSCORE-Code-Knowledge-Sync(Job #4)에서 리포지토리 트리와 핵심 설정 파일을 Markdown으로 요약해 RAG/Dify 업로드용 컨텍스트를 만든다.
# 원칙:
# - 생성된 Markdown은 RAG 컨텍스트로 활용되어, 정적 분석 결과를 LLM이 해석하고
#   개선 방안을 제시할 때 사전 정보(트리/README/패키지 매니페스트 등)로 쓰인다.
# - readme.md에서 설명한 Job #4(DSCORE-Code-Knowledge-Sync) 흐름을 지원해,
#   코드 컨텍스트를 Dify 지식베이스로 올리기 전에 요약본을 만든다.
# - Jenkins/CI 자동화에서 호출하는 것을 전제로 하며, 대규모 리포에서도 안전하게
#   동작하도록 출력 라인/바이트 제한을 둔다.
# - LLM이 코드 품질 이슈를 설명하거나 제안할 때, “프로젝트 전반 구조/주요 설정”을
#   빠르게 참고하도록 돕는 사전 자료 역할을 한다.
# - 출력은 Markdown 하나로 끝나므로 Jenkins 아티팩트나 로그에 남겨 두었다가
#   다른 스크립트/워크플로(Dify 업로드 등)에서 바로 재사용할 수 있다.
# 기대결과: 트리 + 주요 파일 내용을 담은 Markdown이 생성되어, 코드 컨텍스트 지식베이스로 바로 업로드하거나 Jenkins 아티팩트로 활용된다.

# 트리 생성 시 스킵할 디렉터리 목록.
# (빌드 산출물, IDE 설정, 가상환경, 캐시 등은 컨텍스트 가치가 낮으므로 제외)
# - readme.md에서 다루지 않는 노이즈 디렉터리를 걸러 RAG 입력 크기와 노이즈를 줄인다.
# - node_modules/.venv/.gradle처럼 용량이 큰 폴더는 탐색하지 않아 속도도 확보한다.
EXCLUDE_DIRS = {
    ".git", ".scannerwork", "node_modules", "build", "dist", "out", "target",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv",
}

# 트리 외에 본문에 포함할 핵심 파일 목록.
# 존재하는 항목만 읽어와 Markdown 섹션으로 추가한다.
# - readme.md에서 강조한 패키지 매니페스트, 빌드 스크립트, 환경 예시를 그대로 반영했다.
# - LLM이 “언어/프레임워크/빌드 시스템”을 빠르게 추정하도록 돕는 의도다.
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
    주어진 파일을 최대 max_bytes만 읽어 UTF-8 문자열로 반환한다.
    - 바이너리 파일이나 권한 오류가 있으면 빈 문자열을 반환해 전체 흐름이 중단되지 않도록 한다.
    - decode errors는 무시하고 진행해 부분 정보라도 얻는다.
    - readme.md에 명시된 핵심 설정/매니페스트 파일들을 다루기 때문에,
      파일이 크더라도 상한을 두어 파이프라인이 멈추지 않게 한다.
    - lock 파일처럼 수십 MB가 될 수 있는 파일도 상한을 적용해 안전하게 잘라낸다.
    - 실패해도 호출부는 빈 문자열을 그대로 넣어 섹션을 비워 두는 식으로 진행한다.
    """
    try:
        data = path.read_bytes()
        data = data[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def build_tree(repo_root: Path, max_lines: int = 3000) -> str:
    """
    repo_root 이하의 디렉터리/파일 트리를 문자열로 만든다.
    - EXCLUDE_DIRS에 정의된 폴더는 탐색에서 제외한다.
    - max_lines를 넘기면 [TRUNCATED] 표시 후 중단해 너무 긴 출력으로 인한 성능 저하를 막는다.
    - readme.md 기준으로 코드/문서/인프라 파일을 한눈에 볼 수 있는 요약을 만드는 단계다.
    - 숨김 파일(.DS_Store 등)이나 노이즈 디렉터리를 제외해 RAG 품질을 높인다.
    """
    lines = []
    count = 0

    for root, dirs, files in os.walk(repo_root):
        rel_root = Path(root).relative_to(repo_root)
        # 노이즈 디렉터리는 여기서 바로 제외해 하위 탐색도 건너뛴다.
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        depth = len(rel_root.parts)
        indent = "  " * depth

        if str(rel_root) == ".":
            lines.append(f"{repo_root.name}/")
        else:
            lines.append(f"{indent}{rel_root.name}/")
        count += 1
        if count >= max_lines:
            # 너무 길어지면 잘랐다는 표시를 남기고 종료한다.
            lines.append("[TRUNCATED] tree lines limit reached")
            break

        # 파일은 알파벳 순으로 정렬해 출력 안정성을 높인다.
        for f in sorted(files):
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
    CLI 엔트리포인트.
    - --repo_root: 트리를 만들 리포지토리 루트 경로
    - --out: 저장 경로 (파일명은 내부에서 context-yymmdd-.md로 자동 변경됨)
    - --max_key_file_bytes: KEY_FILES 개별 파일을 읽을 때의 최대 바이트 수 (기본 30KB)
    동작:
    1) 현재 날짜(yymmdd)를 기반으로 파일명 생성
    2) KEY_FILES 중 존재하는 파일을 순회하며 본문 섹션을 추가
    3) 결과를 out 경로에 저장
    추가 설명:
    - 출력 포맷은 Markdown 코드블록을 포함한 간단한 보고서 형태로,
      이후 Dify 지식베이스 업로드나 LLM 프롬프트에 바로 붙여 넣기 쉽게 만든다.
    - KEY_FILES 목록은 readme.md에서 강조한 패키지 매니페스트(패키지 매니저별),
      빌드 스크립트, 환경 예시(.env.example) 등을 포함해 구성했다.
    - max_key_file_bytes를 줄이면 대형 lock 파일도 안전하게 스킵/절단할 수 있다.
    - Jenkins에서 아티팩트로 남기거나, Dify 지식베이스에 업로드할 때 그대로 활용할 수 있다.
    - readme.md의 Code-Knowledge-Sync 파이프라인에서 이 파일을 upstream 입력으로 사용한다.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo_root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max_key_file_bytes", type=int, default=30000)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    
    # [수정] 저장소 이름을 포함한 파일명 생성 (context_저장소이름.md)
    target_filename = f"context_{repo_root.name}.md"
    
    out_arg = Path(args.out).resolve()
    if out_arg.is_dir():
        out_path = out_arg / target_filename
    else:
        out_path = out_arg.parent / target_filename

    parts = []
    # 헤더: 보고서 제목
    parts.append("# Repository Context")
    parts.append("")
    # (1) 트리 섹션: 프로젝트 전체 구조를 텍스트 트리로 보여준다.
    parts.append("## Tree")
    parts.append("")
    parts.append("```text")
    parts.append(build_tree(repo_root))
    parts.append("```")
    parts.append("")

    # (2) 핵심 파일 섹션: 설정/매니페스트 내용을 그대로 첨부한다.
    parts.append("## Key Files")
    parts.append("")
    for k in KEY_FILES:
        p = repo_root / k
        if not p.exists():
            continue
        parts.append(f"### {k}")
        parts.append("")
        parts.append("```")
        parts.append(safe_read_text(p, args.max_key_file_bytes))
        parts.append("```")
        parts.append("")

    # (3) 추가 문서 섹션: 리포지토리 내의 모든 .md 파일을 찾아 내용을 첨부한다.
    # - KEY_FILES에 이미 포함된 파일은 중복 방지를 위해 제외한다.
    # - EXCLUDE_DIRS에 포함된 경로는 탐색하지 않는다.
    additional_md_parts = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.lower().endswith(".md"):
                full_p = Path(root) / f
                rel_p = full_p.relative_to(repo_root)
                
                # 이미 KEY_FILES에서 처리했거나, 현재 생성 중인 출력 파일인 경우 제외
                if str(rel_p) in KEY_FILES or full_p == out_path:
                    continue
                
                additional_md_parts.append(f"### {rel_p}\n\n```markdown\n{safe_read_text(full_p, args.max_key_file_bytes)}\n```\n")

    if additional_md_parts:
        parts.append("## Additional Documentation (.md files)")
        parts.append("")
        parts.extend(additional_md_parts)

    # 최종 Markdown 저장
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[Saved] {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
