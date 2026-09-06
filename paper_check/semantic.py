"""语义通道：句向量召回的统一接口 + 原型兜底排序器。

接口 SemanticModel.embed(list[str]) -> ndarray：任何句向量模型
（BGE、SimCSE、text-embedding 服务）实现该接口即可插入 engine，
用于捕捉“同义改写、语序调整”类抄袭。部署了 sentence-transformers
时 engine 会自动加载本地中文模型。

未配置向量模型时，engine 使用内置的 bigram 包含系数排序器兜底：
- 与语料构成无关（不像 TF-IDF 余弦幅度会随拟合集漂移）
- 只负责“召回”（把真匹配排进 Top-K），是否判抄袭由编辑相似度闸门
  把关（见 engine._semantic_pass）。
- 局限：对深度改写（字面重叠 <0.6）召回有限，属于原型已知边界。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .fingerprint import bigrams

logger = logging.getLogger(__name__)

# 余弦阈值（仅在接入了真实句向量模型时使用）
COS_RECALL = 0.55  # 召回门槛：候选进入验证
COS_STRONG = 0.72  # 强阈值：余弦单独够高可判“改写疑似”

# 兜底排序器（bigram 包含系数）阈值
CONTAIN_RECALL = 0.35  # 召回门槛
CONTAIN_STRONG = 0.75  # 词块重叠很高但语序不同 → 改写疑似
TOP_K = 3  # 每个查询句取前 K 个候选进入精排验证


class SemanticModel:
    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    @property
    def name(self) -> str:
        return "base"


class SentenceEmbedder(Protocol):
    """句向量模型的最小结构接口：任何提供 encode 的模型（如 SentenceTransformer）均可注入。"""

    def encode(self, texts: list[str], *, normalize_embeddings: bool = False) -> np.ndarray: ...


@dataclass
class STSemantic(SemanticModel):
    model: SentenceEmbedder
    _name: str = field(default="st-semantic", init=False)

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True))

    @property
    def name(self) -> str:
        return self._name


def load_semantic_model():
    """加载本地中文句向量模型；不可用则返回 None（engine 走兜底排序器）。"""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        model = STSemantic(SentenceTransformer("BAAI/bge-small-zh-v1.5"))
        logger.info("语义通道：使用 bge-small-zh 句向量模型")
        return model
    except Exception:
        logger.info("语义通道：未检测到本地向量模型，使用 bigram 包含系数兜底")
        return None


def containment(a: str, b: str) -> float:
    """bigram 集合包含系数：|A∩B| / min(|A|,|B|)，语料无关、短句友好。"""
    ba, bb = set(bigrams(a)), set(bigrams(b))
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / min(len(ba), len(bb))


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """行归一化向量的两两余弦相似度矩阵。"""
    return a @ b.T
