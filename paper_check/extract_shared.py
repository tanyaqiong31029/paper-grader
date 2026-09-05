"""复用论文批改项目的文档提取能力（同仓库内共享，避免重复实现）。"""

from __future__ import annotations

from pathlib import Path

from paper_grader.extract import extract_paper


def extract_any(path: str | Path) -> tuple[str, list[str]]:
    paper = extract_paper(path)
    return paper.text, paper.warnings
