"""查重引擎：三级漏斗编排（指纹粗筛 → 编辑精排 → 语义兜底）。

流程（对应方案第二章）：
  1. 指纹   ：查询句计算 SimHash
  2. 粗筛   ：LSH 分块倒排召回候选（doc, sent）对，把比对范围
              从“全库”缩到“候选集”
  3. 精排   ：候选对计算编辑相似度，按阈值分级（复制/高度疑似/疑似）
  4. 语义   ：未命中的句子走句向量余弦，标“改写疑似”
  5. 归并   ：连续命中合并为片段，按来源聚合，章节热力统计
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .align import Match, Span, TIER_SUSPECT, edit_similarity, tier_of
from .fingerprint import LSHIndex, bigrams, simhash
from .preprocess import PreparedDoc
from .semantic import (COS_RECALL, COS_STRONG, CONTAIN_RECALL, CONTAIN_STRONG,
                       SemanticModel, TOP_K, cosine_matrix, containment)

# 语义通道的编辑相似度精度闸门：召回的候选，编辑相似度达到此值
# 才判为命中（防止仅共享常用词的无关句子误报）
SEMANTIC_ES_GATE = 0.60


@dataclass
class SourceStat:
    doc_id: int
    name: str
    matched_chars: int
    n_matches: int
    ratio: float = 0.0


@dataclass
class CheckResult:
    name: str
    body_chars: int
    matched_chars: int
    total_ratio: float
    single_max_ratio: float
    sources: list[SourceStat]
    matches: list[Match]
    spans: list[Span]
    chapters: list[dict]
    semantic_model: str = ""
    timings: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    prepared: PreparedDoc | None = None  # 报告渲染用，不序列化


class DedupEngine:
    def __init__(self, store, semantic: SemanticModel | None = None):
        self.store = store
        self.semantic = semantic
        self.index = LSHIndex()
        self._doc_names: dict[int, str] = {}
        self._sent_texts: dict[tuple[int, int], tuple[str, str]] = {}
        self._lib_matrix: np.ndarray | None = None   # 库句向量缓存
        self._lib_keys: list[tuple[int, int]] = []
        self._load_index()

    def _load_index(self):
        t0 = time.time()
        for doc_id, sent_idx, fp, norm, display in self.store.load_sentences():
            self.index.add(doc_id, sent_idx, fp)
            self._sent_texts[(doc_id, sent_idx)] = (norm, display)
        self._doc_names = self.store.doc_names()
        self._lib_matrix = None  # 索引变更后向量缓存失效
        self._load_ms = int((time.time() - t0) * 1000)

    # ---------- 文献入库 ----------
    def add_document(self, sha: str, name: str, prepared: PreparedDoc) -> int:
        doc_id = self.store.add_document(sha, name, prepared)
        for s in prepared.sentences:
            fp = simhash(s.norm)
            self.index.add(doc_id, s.idx, fp)
            self._sent_texts[(doc_id, s.idx)] = (s.norm, s.display)
        self._doc_names[doc_id] = name
        self._lib_matrix = None
        return doc_id

    def library_stats(self) -> dict:
        st = self.store.stats()
        st["index_sentences"] = self.index.n_sentences
        st["load_ms"] = self._load_ms
        return st

    # ---------- 主流程 ----------
    def check(self, prepared: PreparedDoc, progress=None, use_semantic: bool = True) -> CheckResult:
        def report(stage, pct, msg=""):
            if progress:
                progress(stage, pct, msg)

        t0 = time.time()
        notes = list(prepared.notes)

        # 1) 指纹 + 2) LSH 粗筛
        report("粗筛", 10, "计算句指纹并召回候选")
        candidates: dict[int, dict[tuple[int, int], int]] = {}
        for s in prepared.sentences:
            fp = simhash(s.norm)
            cands = self.index.candidates(fp)
            if cands:
                candidates[s.idx] = cands
        report("粗筛", 35, f"召回 {sum(len(v) for v in candidates.values())} 个候选句对")

        # 3) 编辑相似度精排
        report("精排", 45, "候选句对精确比对")
        matches: list[Match] = []
        matched_q: set[int] = set()
        for s in prepared.sentences:
            for (doc_id, s_idx), _d in candidates.get(s.idx, {}).items():
                s_norm, s_display = self._sent_texts[(doc_id, s_idx)]
                sim = edit_similarity(s.norm, s_norm)
                tier = tier_of(sim)
                if tier:
                    matches.append(Match(
                        q_idx=s.idx, doc_id=doc_id, doc_name=self._doc_names.get(doc_id, str(doc_id)),
                        s_idx=s_idx, s_display=s_display, sim=round(sim, 3), tier=tier))
                    matched_q.add(s.idx)

        # 4) 语义兜底：字面未达标的句子做向量召回
        sem_name = ""
        if use_semantic and self.index.n_sentences:
            report("语义", 60, "语义通道比对（改写检测）")
            sem_name, sem_hits = self._semantic_pass(prepared, matched_q)
            matches.extend(sem_hits)

        # 5) 归并聚合
        report("聚合", 80, "合并片段并统计来源分布")
        matches.sort(key=lambda m: (m.q_idx, -(m.sim)))
        best_per_q: dict[int, Match] = {}
        for m in matches:
            if m.q_idx not in best_per_q or (m.sim, m.semantic_cos) > (
                    best_per_q[m.q_idx].sim, best_per_q[m.q_idx].semantic_cos):
                best_per_q[m.q_idx] = m

        body_chars = prepared.body_chars
        matched_chars = sum(len(prepared.sentences[i].norm) for i in best_per_q)
        total_ratio = round(matched_chars / body_chars, 4) if body_chars else 0.0

        src_map: dict[int, SourceStat] = {}
        for m in best_per_q.values():
            st = src_map.setdefault(m.doc_id, SourceStat(m.doc_id, m.doc_name, 0, 0))
            st.matched_chars += len(prepared.sentences[m.q_idx].norm)
            st.n_matches += 1
        sources = sorted(src_map.values(), key=lambda x: -x.matched_chars)
        for st in sources:
            st.ratio = round(st.matched_chars / body_chars, 4) if body_chars else 0.0

        spans = self._merge_spans(prepared, matches)
        chapters = self._chapter_stats(prepared, best_per_q)

        report("聚合", 95, "生成报告数据")
        return CheckResult(
            name=prepared.name,
            body_chars=body_chars,
            matched_chars=matched_chars,
            total_ratio=total_ratio,
            single_max_ratio=sources[0].ratio if sources else 0.0,
            sources=sources,
            matches=matches,
            spans=spans,
            chapters=chapters,
            semantic_model=sem_name,
            timings={
                "index_load_ms": self._load_ms,
                "total_ms": int((time.time() - t0) * 1000),
                "n_query_sentences": len(prepared.sentences),
            },
            notes=notes,
            prepared=prepared,
        )

    # ---------- 语义通道 ----------
    def _semantic_pass(self, prepared: PreparedDoc, already_matched: set[int]) -> tuple[str, list[Match]]:
        """对字面未命中的查询句做语义召回。

        两条路径：
        - 接入了句向量模型 → 余弦 Top-K 召回
        - 未接入（原型默认）→ bigram 包含系数 Top-K 召回（语料无关）
        两条路径的精度都由编辑相似度闸门（SEMANTIC_ES_GATE）把关：
        字面重叠够高按字面分级；语序重排但词块高度重合判“改写疑似”。
        """
        pending = [s for s in prepared.sentences
                   if s.idx not in already_matched and len(s.norm) >= 10]
        if not pending:
            return (self.semantic.name if self.semantic else "bigram-containment"), []

        if self.semantic is not None:
            keys, lib_vecs = self._lib_vectors()
            q_vecs = self.semantic.embed([s.norm for s in pending])
            sim = cosine_matrix(q_vecs, lib_vecs)
            scored = [
                (s, [(float(sim[i, j]), int(j)) for j in np.argsort(-sim[i])[:TOP_K]])
                for i, s in enumerate(pending)
            ]
        else:
            keys = sorted(self._sent_texts.keys())
            lib_sets = {k: set(bigrams(self._sent_texts[k][0])) for k in keys}
            scored = []
            for s in pending:
                bset = set(bigrams(s.norm))
                scores = sorted(
                    ((len(bset & lib_sets[k]) / min(len(bset), len(lib_sets[k]))
                      if bset and lib_sets[k] else 0.0, ki)
                     for ki, k in enumerate(keys)),
                    key=lambda x: -x[0],
                )[:TOP_K]
                scored.append((s, scores))

        hits: list[Match] = []
        for s, cands in scored:
            for score, ki in cands:
                if score < (COS_RECALL if self.semantic is not None else CONTAIN_RECALL):
                    break  # 已按分数降序，后面更低
                doc_id, s_idx = keys[ki]
                s_norm, s_display = self._sent_texts[(doc_id, s_idx)]
                es = edit_similarity(s.norm, s_norm)
                if es >= SEMANTIC_ES_GATE:
                    tier = tier_of(es)          # 字面重叠确实高 → 按字面分级
                elif es >= TIER_SUSPECT:
                    tier = "suspect"
                elif score >= (COS_STRONG if self.semantic is not None else CONTAIN_STRONG):
                    tier = "semantic"           # 词块高度重合但语序重排
                else:
                    continue
                hits.append(Match(
                    q_idx=s.idx, doc_id=doc_id, doc_name=self._doc_names.get(doc_id, str(doc_id)),
                    s_idx=s_idx, s_display=s_display, sim=round(es, 3),
                    tier=tier, semantic_cos=round(score, 3)))
        return (self.semantic.name if self.semantic else "bigram-containment"), hits

    def _lib_vectors(self):
        """库句向量（有真实模型时缓存）。"""
        if self._lib_matrix is None:
            self._lib_keys = sorted(self._sent_texts.keys())
            self._lib_matrix = self.semantic.embed(
                [self._sent_texts[k][0] for k in self._lib_keys])
        return self._lib_keys, self._lib_matrix

    # ---------- 片段合并与章节统计 ----------
    def _merge_spans(self, prepared: PreparedDoc, matches: list[Match]) -> list[Span]:
        if not matches:
            return []
        by_q: dict[int, list[Match]] = {}
        for m in matches:
            by_q.setdefault(m.q_idx, []).append(m)
        idxs = sorted(by_q.keys())
        spans, cur = [], [idxs[0]]
        for prev, nxt in zip(idxs, idxs[1:]):
            if nxt - prev <= 2:  # 允许隔一个短句仍视为同一片段
                cur.append(nxt)
            else:
                spans.append(cur)
                cur = [nxt]
        spans.append(cur)
        return [Span(q_start=g[0], q_end=g[-1],
                     matches=[m for i in g for m in by_q[i]])
                for g in spans if len(g) >= 1]

    def _chapter_stats(self, prepared: PreparedDoc, best_per_q: dict[int, Match]) -> list[dict]:
        if not prepared.chapters:
            return []
        chs = sorted(prepared.chapters, key=lambda c: c.start)
        out = []
        for ch in chs:
            chars, hit = 0, 0
            for s in prepared.sentences:
                if ch.start <= s.start < ch.end:
                    chars += len(s.norm)
                    if s.idx in best_per_q:
                        hit += len(s.norm)
            out.append({"title": ch.title,
                        "ratio": round(hit / chars, 4) if chars else 0.0,
                        "body_chars": chars})
        return out
