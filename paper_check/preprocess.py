"""查重预处理：文本归一化、分句、章节识别、参考文献剔除。

设计要点（对应方案第二章“文本比对流程”）：
- 中文按字符处理，不依赖分词组件；归一化去除对复制检测无意义的差异
  （空白、全半角、大小写），保留标点用于分句。
- 章节识别复用论文批改项目的标题规则，用于章节级相似度统计。
- 参考文献部分默认剔除：与引文库匹配属正常引用，纳入比对只会制造噪声。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(
    r"^(#{1,3}\s|第[一二三四五六七八九十百]+[章节部分]|[(（]?(?:[一二三四五六七八九十]|\d{1,2})[)）]、?\s*\S)"
)
REF_MARK_RE = re.compile(r"^(参考文献|参\s*考\s*文\s*献|References|REFERENCES)\s*$")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|\n+")


def normalize(text: str) -> str:
    """检测用归一化：去空白、全角转半角、转小写。"""
    table = str.maketrans(
        "，。！？；：（）“”‘’【】《》—％",
        ",.!?;:()\"\"''[]<>-%",
    )
    text = text.translate(table)
    return re.sub(r"\s+", "", text).lower()


@dataclass
class Sentence:
    idx: int          # 在全文句子序列中的序号
    display: str      # 原始文本（报告展示用）
    norm: str         # 归一化文本（比对用）
    start: int        # 在全文（含标题）中的起始偏移，用于染色定位
    end: int


@dataclass
class Chapter:
    title: str
    start: int
    end: int


@dataclass
class PreparedDoc:
    """预处理产物：正文句子序列 + 章节结构 + 元信息。"""

    name: str
    full_text: str
    sentences: list[Sentence] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    excluded_ref_chars: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def body_chars(self) -> int:
        """参与比对的正文字符数（剔除参考文献后）。"""
        return sum(len(s.norm) for s in self.sentences)

    def chapter_of(self, pos: int) -> str:
        for ch in self.chapters:
            if ch.start <= pos < ch.end:
                return ch.title
        return "（前置部分）"


def _split_body_and_refs(text: str) -> tuple[str, int]:
    """按“参考文献”标题切成正文 + 参考文献区，返回正文与被剔除字符数。"""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if REF_MARK_RE.match(line.strip()):
            body = "".join(lines[:i])
            refs = "".join(lines[i:])
            return body, len(refs)
    return text, 0


def prepare(text: str, name: str = "") -> PreparedDoc:
    body, excluded = _split_body_and_refs(text)
    doc = PreparedDoc(name=name, full_text=text, excluded_ref_chars=excluded)
    if excluded:
        doc.notes.append(f"已剔除参考文献区域（{excluded} 字符）")

    # 先标章节（含标题行本身，用于热力图定位）
    cursor = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and HEADING_RE.match(stripped):
            doc.chapters.append(Chapter(title=stripped.lstrip("# ").strip()[:30],
                                        start=cursor, end=cursor + len(line)))
        cursor += len(line)

    # 分句：保留标题行（作为独立句子，展示时标记），正文按句末标点切
    pos = 0
    idx = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            pos += len(raw_line) + 1
            continue
        if HEADING_RE.match(line):
            doc.sentences.append(Sentence(
                idx=idx, display=line, norm=normalize(line), start=pos, end=pos + len(line)))
            idx += 1
            pos += len(raw_line) + 1
            continue
        # 按句末标点切分，记录每句在原文中的偏移
        local = 0
        parts = [p for p in _SENT_SPLIT_RE.split(line) if p and p.strip()]
        for part in parts:
            start_in_line = line.find(part, local)
            local = start_in_line + len(part)
            norm = normalize(part)
            if len(norm) >= 6:  # 过短片段（如单个标点）不参与比对
                doc.sentences.append(Sentence(
                    idx=idx, display=part.strip(), norm=norm,
                    start=pos + start_in_line, end=pos + local))
                idx += 1
        pos += len(raw_line) + 1

    if not doc.sentences:
        doc.notes.append("未提取到有效正文句子")
    return doc
