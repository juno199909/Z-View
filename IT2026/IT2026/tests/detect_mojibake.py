# -*- coding: utf-8 -*-
"""检测源码中 'UTF-8 被 GBK 误读' 型双重编码乱码。"""

import re
import sys
from pathlib import Path

CJK_RUN = re.compile(r"[^\x00-\x7f]{2,}")


def is_mojibake_run(run: str) -> bool:
    """合法中文经 GBK 编码后无法再被当作 UTF-8 解出不同文本；
    乱码文本 GBK 编码后恰好等于原 UTF-8 字节，能解出正常中文。"""
    if len(run) < 2 or not re.search(r"[\u4e00-\u9fff]", run):
        return False
    try:
        rebuilt = run.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False
    return rebuilt != run and bool(re.search(r"[\u4e00-\u9fff]", rebuilt))


def scan_file(path: Path):
    hits = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        bad_runs = [run for run in CJK_RUN.findall(line) if is_mojibake_run(run)]
        if bad_runs:
            preview = line.strip()[:70]
            fixed = line
            for run in bad_runs:
                fixed = fixed.replace(run, run.encode("gbk").decode("utf-8"))
            hits.append((lineno, preview, fixed.strip()[:70]))
    return hits


def main():
    root = Path(sys.argv[1])
    patterns = ["*.py", "*.vue", "*.js", "*.md", "*.json"]
    total_files = 0
    for pattern in patterns:
        for path in root.rglob(pattern):
            if any(part in {"node_modules", ".git", "__pycache__", "dist"} for part in path.parts):
                continue
            try:
                hits = scan_file(path)
            except Exception:
                continue
            if hits:
                total_files += 1
                print(f"== {path.relative_to(root)} ==")
                for lineno, before, after in hits[:40]:
                    print(f"  L{lineno}: {before}")
                    print(f"     -> {after}")
    print(f"\n共 {total_files} 个文件存在疑似双重编码乱码")


if __name__ == "__main__":
    main()
