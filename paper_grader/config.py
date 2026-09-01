"""配置加载：config.yaml + 环境变量。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# 论文类型 → config.yaml 中 rubrics 的键
PAPER_TYPES = ("journal", "course", "thesis")

PAPER_TYPE_LABELS = {
    "journal": "期刊论文",
    "course": "研究生课程论文",
    "thesis": "学位论文（毕业论文）",
}


@dataclass
class LLMConfig:
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    model: str = "glm-4.6"
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: int = 300
    max_retries: int = 3

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMConfig":
        env_key = os.environ.get("PAPER_GRADER_API_KEY", "")
        return cls(
            base_url=d.get("base_url", cls.base_url),
            model=d.get("model", cls.model),
            api_key=d.get("api_key") or env_key,
            temperature=float(d.get("temperature", cls.temperature)),
            max_tokens=int(d.get("max_tokens", cls.max_tokens)),
            timeout=int(d.get("timeout", cls.timeout)),
            max_retries=int(d.get("max_retries", cls.max_retries)),
        )


@dataclass
class GradeConfig:
    chunk_chars: int = 9000        # 长文分块大小（字符）
    max_fulltext_chars: int = 24000  # 超过则走“分块 map + 汇总 reduce”两阶段
    concurrent: int = 2            # 批量批改时的并发篇数
    grade_bands: list = field(default_factory=lambda: [
        {"min": 90, "label": "优秀"},
        {"min": 80, "label": "良好"},
        {"min": 70, "label": "中等"},
        {"min": 60, "label": "及格"},
        {"min": 0, "label": "不及格"},
    ])

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GradeConfig":
        c = cls()
        if not d:
            return c
        c.chunk_chars = int(d.get("chunk_chars", c.chunk_chars))
        c.max_fulltext_chars = int(d.get("max_fulltext_chars", c.max_fulltext_chars))
        c.concurrent = int(d.get("concurrent", c.concurrent))
        if "grade_bands" in d:
            c.grade_bands = d["grade_bands"]
        return c

    def band_of(self, score: float) -> str:
        for band in self.grade_bands:
            if score >= band["min"]:
                return band["label"]
        return "不及格"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    grading: GradeConfig = field(default_factory=GradeConfig)
    rubrics: dict = field(default_factory=dict)
    type_keywords: dict = field(default_factory=lambda: {
        "thesis": ["毕业论文", "学位论文", "毕业设计", "开题"],
        "course": ["课程论文", "课程作业", "期末论文", "课程考核", "结课论文"],
    })

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        cfg = cls()
        cfg.llm = LLMConfig.from_dict(data.get("llm", {}))
        cfg.grading = GradeConfig.from_dict(data.get("grading", {}))
        cfg.rubrics = data.get("rubrics", {})
        if "type_keywords" in data:
            cfg.type_keywords = data["type_keywords"]
        return cfg

    def detect_paper_type(self, filename: str) -> str:
        """按文件名关键词识别论文类型，识别不出默认按课程论文处理。"""
        for ptype, kws in self.type_keywords.items():
            if ptype in PAPER_TYPES:
                for kw in kws:
                    if kw in filename:
                        return ptype
        return "course"
