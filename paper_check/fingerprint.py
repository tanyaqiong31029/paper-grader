"""指纹粗筛：字符 bigram SimHash + 分块倒排索引（LSH）。

对应方案中的“粗筛召回”层——把比对范围从全库 N 篇缩到候选 K 句：
- 特征：归一化句子的字符 bigram（中文无需分词）
- 指纹：FNV-1a 64 位（跨进程稳定，区别于内建 hash 的随机化）
- 索引：64 位指纹切 4×16 bit 分块，鸽笼原理保证汉明距离 ≤3 的句子
  至少命中一个分块，查询时只扫描命中的桶，而非全库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MASK64 = (1 << 64) - 1


def fnv1a64(data: str) -> int:
    h = 0xCBF29CE484222325
    for byte in data.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & MASK64
    return h


def bigrams(norm_text: str) -> list[str]:
    if len(norm_text) < 2:
        return [norm_text] if norm_text else []
    return [norm_text[i : i + 2] for i in range(len(norm_text) - 1)]


def simhash(norm_text: str) -> int:
    feats = bigrams(norm_text)
    if not feats:
        return 0
    v = [0] * 64
    for f in feats:  # 权重=词频
        h = fnv1a64(f)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    fp = 0
    for i in range(64):
        if v[i] > 0:
            fp |= 1 << i
    return fp


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def to_signed64(fp: int) -> int:
    """无符号 64 位 → SQLite 有符号整型。"""
    return fp - (1 << 64) if fp >= (1 << 63) else fp


def to_uint64(v: int) -> int:
    return v & MASK64


def lsh_blocks(fp: int, n_blocks: int = 4, bits: int = 16) -> list[int]:
    """把 64 位指纹切成 n_blocks 块，返回各块键。"""
    return [(fp >> (i * bits)) & ((1 << bits) - 1) for i in range(n_blocks)]


@dataclass
class SentenceRef:
    doc_id: int
    sent_idx: int
    fp: int


@dataclass
class LSHIndex:
    """句级指纹倒排。原型规模（十万句内）常驻内存即可；
    百万句级应将桶结构迁移至 Redis/ES（见 README“规模化路径”）。"""

    n_blocks: int = 4
    max_hamming: int = 4
    buckets: dict[tuple[int, int], list[SentenceRef]] = field(default_factory=dict)
    n_sentences: int = 0

    def add(self, doc_id: int, sent_idx: int, fp: int) -> None:
        if not fp:
            return
        ref = SentenceRef(doc_id, sent_idx, fp)
        for bi, key in enumerate(lsh_blocks(fp, self.n_blocks)):
            self.buckets.setdefault((bi, key), []).append(ref)
        self.n_sentences += 1

    def candidates(self, fp: int) -> dict[tuple[int, int], int]:
        """返回 {(doc_id, sent_idx): 汉明距离}，距离 ≤ max_hamming。"""
        seen: set[tuple[int, int]] = set()
        out: dict[tuple[int, int], int] = {}
        if not fp:
            return out
        for bi, key in enumerate(lsh_blocks(fp, self.n_blocks)):
            for ref in self.buckets.get((bi, key), ()):
                k = (ref.doc_id, ref.sent_idx)
                if k in seen:
                    continue
                seen.add(k)
                d = hamming(fp, ref.fp)
                if d <= self.max_hamming:
                    out[k] = d
        return out
