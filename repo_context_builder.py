#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".scannerwork", "node_modules", "build", "dist", "out", "target",
    ".idea", ".vscode", ".gradle", ".next", ".nuxt", ".cache", ".venv", "venv",
}

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
