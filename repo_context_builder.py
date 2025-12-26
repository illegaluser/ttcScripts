#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

# 리포지토리 구조와 핵심 설정/문서 내용을 하나의 Markdown으로 덤프한다.
# - 생성된 Markdown은 RAG 컨텍스트로 활용되어, 정적 분석 결과를 LLM이 해석하고
#   개선 방안을 제시할 때 사전 정보(트리/README/패키지 매니페스트 등)로 쓰인다.
# - Jenkins 파이프라인/자동화 스크립트에서 호출하는 것을 전제로 하며,
#   대규모 리포지토리에서도 안전하게 실행되도록 출력/바이트 제한을 둔다.
# 리포지토리 요약을 생성해 Dify 지식베이스나 Jenkins 로그에 남길 때 사용한다.
# - 전체 트리와 핵심 설정 파일(README, 패키지 매니페스트 등) 내용을 묶어 하나의 Markdown으로 출력한다.
# - 용량이 큰 리포지토리에서도 안전하게 동작하도록 디렉터리/바이트 제한을 적용한다.

# 트리 생성 시 스킵할 디렉터리 목록.
# (빌드 산출물, IDE 설정, 가상환경, 캐시 등은 컨텍스트 가치가 낮으므로 제외)
EXCLUDE_DIRS = {
    ".git", ".scannerwork", "node_modules", "build", "dist", "out", "target",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv",
}

# 트리 외에 본문에 포함할 핵심 파일 목록.
# 존재하는 항목만 읽어와 Markdown 섹션으로 추가한다.
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
    """
    lines = []
    count = 0

    for root, dirs, files in os.walk(repo_root):
        rel_root = Path(root).relative_to(repo_root)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        depth = len(rel_root.parts)
        indent = "  " * depth

        if str(rel_root) == ".":
            lines.append(f"{repo_root.name}/")
        else:
            lines.append(f"{indent}{rel_root.name}/")
        count += 1
        if count >= max_lines:
            lines.append("[TRUNCATED] tree lines limit reached")
            break

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
    - --out: 생성된 Markdown을 저장할 경로
    - --max_key_file_bytes: KEY_FILES 개별 파일을 읽을 때의 최대 바이트 수 (기본 30KB)
    동작:
    1) 트리 섹션을 추가
    2) KEY_FILES 중 존재하는 파일을 순회하며 본문 섹션을 추가
    3) 결과를 out 경로에 저장
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo_root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max_key_file_bytes", type=int, default=30000)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_path = Path(args.out).resolve()

    parts = []
    parts.append("# Repository Context")
    parts.append("")
    parts.append("## Tree")
    parts.append("")
    parts.append("```text")
    parts.append(build_tree(repo_root))
    parts.append("```")
    parts.append("")

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

    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[Saved] {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
