"""精排：候选句对的真实相似度计算、分级与连续片段合并。

粗筛只负责“快”（LSH 候选），这里的编辑相似度负责“准”：
- 相似度 = 1 - 归一化编辑距离（Levenshtein，DP 实现，句长百字级开销可忽略）
- 分级：≥0.80 复制 / 0.65~0.80 高度疑似 / 0.55~0.65 疑似
- 连续命中的查询句合并为片段（span），报告按片段展示来源对照。
"""

from __future__ import annotations

from dataclasses import dataclass

# 相似度分级阈值（可按学科校准）
TIER_COPY = 0.80
TIER_NEAR = 0.65
TIER_SUSPECT = 0.55

TIER_LABELS = {"copy": "复制", "near": "高度疑似", "suspect": "疑似", "semantic": "改写疑似"}


def edit_distance(a: str, b: str, cap: float = 0.55) -> int:
    """归一化编辑距离，支持提前终止（超过 cap*maxlen 即可判定不合格）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) / max(la, lb, 1) >= 1 - cap:
        return max(la, lb)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            row_min = min(row_min, cur[j])
        if row_min / max(la, lb) >= 1 - cap:  # 提前终止
            return max(la, lb)
        prev = cur
    return prev[lb]


def edit_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 - edit_distance(a, b) / max(len(a), len(b))


def tier_of(sim: float, semantic_only: bool = False) -> str:
    if semantic_only:
        return "semantic"
    if sim >= TIER_COPY:
        return "copy"
    if sim >= TIER_NEAR:
        return "near"
    if sim >= TIER_SUSPECT:
        return "suspect"
    return ""


@dataclass
class Match:
    """一次命中：查询句 × 来源句。"""

    q_idx: int
    doc_id: int
    doc_name: str
    s_idx: int
    s_display: str
    sim: float  # 编辑相似度
    tier: str  # copy / near / suspect / semantic
    semantic_cos: float = 0.0  # 语义通道相似度（如有）


@dataclass
class Span:
    """连续命中合并成的片段。"""

    q_start: int
    q_end: int  # 含端
    matches: list[Match]

    @property
    def primary(self) -> Match:
        return max(self.matches, key=lambda m: (m.sim, m.semantic_cos))
