"""评分量规（Rubric）加载与校验。

量规定义在 config.yaml 的 rubrics 段，可插拔：
每套量规 = name + dimensions[{name, weight, criteria}]，权重和须为 100。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig, PAPER_TYPES, PAPER_TYPE_LABELS


@dataclass
class Dimension:
    name: str
    weight: int
    criteria: str


@dataclass
class Rubric:
    ptype: str          # journal / course / thesis
    name: str
    dimensions: list[Dimension]

    @property
    def total_weight(self) -> int:
        return sum(d.weight for d in self.dimensions)


def load_rubric(cfg: AppConfig, ptype: str) -> Rubric:
    if ptype not in PAPER_TYPES:
        raise ValueError(f"未知论文类型: {ptype}，可选 {PAPER_TYPES}")
    data = cfg.rubrics.get(ptype)
    if not data or "dimensions" not in data:
        raise ValueError(f"config.yaml 中缺少论文类型 “{ptype}” 的评分量规（rubrics.{ptype}）")

    dims = [
        Dimension(
            name=d["name"],
            weight=int(d["weight"]),
            criteria=" ".join(str(d["criteria"]).split()),
        )
        for d in data["dimensions"]
    ]
    rubric = Rubric(
        ptype=ptype,
        name=data.get("name", PAPER_TYPE_LABELS.get(ptype, ptype)),
        dimensions=dims,
    )
    if rubric.total_weight != 100:
        raise ValueError(
            f"量规 “{rubric.name}” 各维度权重之和为 {rubric.total_weight}，应为 100"
        )
    return rubric


def rubric_overview(rubric: Rubric) -> str:
    """渲染给 LLM 看的量规说明。"""
    lines = [f"论文类型：{rubric.name}，满分 100 分，评分维度及权重："]
    for d in rubric.dimensions:
        lines.append(f"- {d.name}（{d.weight} 分）：{d.criteria}")
    return "\n".join(lines)
