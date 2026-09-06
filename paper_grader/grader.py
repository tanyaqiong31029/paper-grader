"""批改编排 —— 借鉴 LLM-AES（LAK'25）双过程框架：

  System 2（细致分析）：
    - 长论文先分块提炼（map）：每块抽取要点/亮点/问题/原文片段
    - 再逐维度独立评分：0-100 分 + 置信度 + 原文证据引用（EssayJudge 式
      rubric-based、NirDiamant 式逐维度节点）
  System 1（综合判断）：
    - 总分 = Σ 权重 × 维度分（确定性计算，可复核）
    - LLM 只负责综合评语、优缺点、风险标记（如“需人工复评”）

置信度偏低的论文会被打上“需人工复评”标记，交给教师抽查——这是
人机协同（human-AI co-grading）的关键接口。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from .config import AppConfig
from .extract import PaperText
from .llm import LLMClient, LLMError
from .rubric import Rubric, rubric_overview

JUDGE_SYSTEM = (
    "你是一位严谨的研究生导师兼期刊审稿人，负责批改中文学术论文。"
    "你必须：1) 只依据给出的论文内容评分，不臆测未提供的信息；"
    "2) 指出问题时引用原文片段作为证据；3) 严格按要求的 JSON 格式输出，不要输出其他内容。"
)


@dataclass
class DimensionResult:
    name: str
    weight: int
    score: float
    confidence: float
    evidence: list[str]
    comment: str
    suspicions: str = ""


@dataclass
class GradeResult:
    file: str
    title: str
    ptype: str
    rubric_name: str
    total: float
    band: str
    confidence: float
    dimensions: list[DimensionResult]
    overall_comment: str = ""
    strengths: list = field(default_factory=list)
    improvements: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    n_chars: int = 0
    mock: bool = False
    error: str | None = None
    provider: str = ""
    version: str = "0.1.0"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------- 论文结构信息抽取（正则，供提示词与 mock 模式使用） ----------


def paper_meta(paper: PaperText) -> dict:
    text = paper.text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    abstract = ""
    m = re.search(
        r"(摘\s*要[:：]?)\s*(.{50,800}?)(关键词|Abstract|【|一、|1\s|引言|\n\n)", text, re.S
    )
    if m:
        abstract = m.group(2).strip()

    keywords = ""
    m = re.search(r"关键\s*词?[:：]\s*(.{4,120})", text)
    if m:
        keywords = m.group(1).strip()

    headings = [
        ln.lstrip("#").strip()
        for ln in lines
        if re.match(
            r"^(#{1,3}\s|第[一二三四五六七八九十]+[章节部分]|[(（]?(?:[一二三四五六七八九十]|\d{1,2})[)）]、?\s*\S)",
            ln,
        )
        and 2 < len(ln.lstrip("#").strip()) <= 40
    ]
    return {
        "title": paper.title,
        "abstract": abstract[:600],
        "keywords": keywords[:120],
        "headings": headings[:30],
        "n_chars": len(text),
    }


def split_chunks(text: str, chunk_chars: int) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        paras = [text]
    chunks, buf = [], ""
    for p in paras:
        if buf and len(buf) + len(p) > chunk_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def _sample_excerpt(text: str, limit: int = 3600) -> str:
    """头/中/尾三段抽样，控制提示词长度。"""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    mid = text[len(text) // 2 - 300 : len(text) // 2 + 700]
    tail = text[-(limit - limit // 2 - 1000) :]
    return f"{head}\n\n……（中略）……\n\n{mid}\n\n……（后略）……\n\n{tail}"


# ---------- 评分流程 ----------


class Grader:
    def __init__(
        self,
        cfg: AppConfig,
        client: LLMClient,
        rubric: Rubric,
        mock: bool = False,
        redact_pii: bool = False,
    ):
        self.cfg = cfg
        self.client = client
        self.rubric = rubric
        self.mock = mock
        self.redact_pii = redact_pii

    def _maybe_redact(self, text: str) -> str:
        from .pii import redact_pii

        return redact_pii(text) if self.redact_pii else text

    # ---- 单篇论文入口 ----
    def grade(self, paper: PaperText) -> GradeResult:
        meta = paper_meta(paper)
        long_paper = meta["n_chars"] > self.cfg.grading.max_fulltext_chars
        chunk_notes = ""
        if long_paper and not self.mock:
            chunk_notes = self._map_chunks(paper)

        dims: list[DimensionResult] = []
        for dim in self.rubric.dimensions:
            if self.mock:
                dr = self._mock_dimension(dim, paper, meta)
            else:
                dr = self._score_dimension(dim, paper, meta, chunk_notes)
            dims.append(dr)

        total = round(sum(d.score * d.weight for d in dims) / 100, 1)
        confidence = round(min(1.0, sum(d.confidence for d in dims) / len(dims)), 2)

        provider = (
            "mock（不调用模型）"
            if self.mock
            else f"{self.cfg.llm.base_url} · {self.cfg.llm.model}"
            + ("，已启用 PII 脱敏" if self.redact_pii else "")
        )
        res = GradeResult(
            file=str(paper.path.name),
            title=meta["title"] or paper.path.stem,
            ptype=self.rubric.ptype,
            rubric_name=self.rubric.name,
            total=total,
            band=self.cfg.grading.band_of(total),
            confidence=confidence,
            dimensions=dims,
            n_chars=meta["n_chars"],
            mock=self.mock,
            provider=provider,
        )
        if not self.mock:
            try:
                self._synthesize(res, meta, chunk_notes)
            except LLMError as e:
                res.overall_comment = "（综合评语生成失败，各维度评语仍有效）：" + str(e)
        # 置信度偏低的论文交给教师抽查（人机协同接口）
        if confidence < 0.6 and not any("人工复评" in f for f in res.flags):
            res.flags.append("需人工复评（模型置信度偏低）")
        return res

    # ---- 长文分块提炼（map 阶段）----
    def _map_chunks(self, paper: PaperText) -> str:
        chunks = split_chunks(paper.text, self.cfg.grading.chunk_chars)
        notes = []
        for i, chunk in enumerate(chunks, 1):
            user = (
                f"以下是论文《{paper.title}》的第 {i}/{len(chunks)} 部分。"
                "请提炼该部分内容，输出 JSON：\n"
                '{"summary": "内容要点，150字以内", "strengths": ["亮点1"], '
                '"weaknesses": ["问题1"], "quotes": ["代表性原文片段1"]}\n\n'
                f"【第 {i} 部分原文】\n{chunk}"
            )
            data = self.client.chat_json(JUDGE_SYSTEM, self._maybe_redact(user))
            notes.append(
                f"[第{i}部分] {data.get('summary', '')}"
                f" 亮点：{'；'.join(map(str, data.get('strengths', [])[:3]))}"
                f" 问题：{'；'.join(map(str, data.get('weaknesses', [])[:3]))}"
                f" 原文片段：{'；'.join(map(str, data.get('quotes', [])[:2]))}"
            )
        return "\n".join(notes)

    # ---- 单维度评分（System 2）----
    def _score_dimension(
        self, dim, paper: PaperText, meta: dict, chunk_notes: str
    ) -> DimensionResult:
        content = (
            f"【结构提纲】{'、'.join(meta['headings']) or '未识别到章节标题'}"
            f"\n【摘要】{meta['abstract'] or '未识别到摘要'}"
        )
        if chunk_notes:
            content += f"\n【全文分块要点与问题记录】\n{chunk_notes}"
            content += f"\n【原文抽样】\n{_sample_excerpt(paper.text)}"
        else:
            content += f"\n【论文全文】\n{paper.text[: self.cfg.grading.max_fulltext_chars + 6000]}"

        user = (
            f"请批改论文《{paper.title}》的单一维度。\n\n"
            f"{rubric_overview(self.rubric)}\n\n"
            f"【本次只需评的维度】{dim.name}（权重 {dim.weight} 分）\n"
            f"【评分要点】{dim.criteria}\n\n"
            f"{content}\n\n"
            "请输出 JSON（score 为 0-100 整数；confidence 为 0~1 小数，"
            "表示你对该维度评分的确信度；evidence 从原文摘录，每条不超过60字）：\n"
            '{"score": 78, "confidence": 0.8, "evidence": ["……", "……"], '
            '"comment": "该维度评语，具体指出做得好与不足，150字左右", '
            '"suspicions": "学术规范问题，无则留空"}'
        )
        data = self.client.chat_json(JUDGE_SYSTEM, self._maybe_redact(user))
        return self._normalize_dimension(dim, data)

    def _normalize_dimension(self, dim, data: dict) -> DimensionResult:
        score = float(data.get("score", 0))
        score = max(0.0, min(100.0, score))
        conf = float(data.get("confidence", 0.7))
        conf = max(0.0, min(1.0, conf))
        evidence = [str(e)[:80] for e in (data.get("evidence") or []) if str(e).strip()][:5]
        return DimensionResult(
            name=dim.name,
            weight=dim.weight,
            score=score,
            confidence=conf,
            evidence=evidence,
            comment=str(data.get("comment", "")).strip(),
            suspicions=str(data.get("suspicions", "")).strip(),
        )

    # ---- 综合评价（System 1）----
    def _synthesize(self, res: GradeResult, meta: dict, chunk_notes: str):
        dims_for_llm = [
            {"维度": d.name, "得分": d.score, "权重": d.weight, "评语": d.comment[:200]}
            for d in res.dimensions
        ]
        user = (
            f"论文《{res.title}》（{res.rubric_name}）已完成逐维度评分：\n"
            f"{json.dumps(dims_for_llm, ensure_ascii=False, indent=1)}\n"
            f"加权总分 {res.total}（等级 {res.band}）。"
            + (f"\n全文分块问题记录：\n{chunk_notes[:3000]}" if chunk_notes else "")
            + "\n请作为导师撰写最终综合意见，输出 JSON：\n"
            '{"overall_comment": "总评，250字左右：先概括论文做了什么、整体水平，再点出关键问题", '
            '"strengths": ["突出优点1", "优点2", "优点3"], '
            '"improvements": ["修改建议1（要具体可操作）", "建议2", "建议3"], '
            '"flags": ["风险标记，如：疑似内容堆砌/文献陈旧/篇幅不足，没有则空数组"]}'
        )
        data = self.client.chat_json(JUDGE_SYSTEM, self._maybe_redact(user))
        res.overall_comment = str(data.get("overall_comment", "")).strip()
        res.strengths = [str(s) for s in (data.get("strengths") or []) if str(s).strip()][:5]
        res.improvements = [str(s) for s in (data.get("improvements") or []) if str(s).strip()][:5]
        res.flags.extend(str(f) for f in (data.get("flags") or []) if str(f).strip())

    # ---- mock 模式：无 API Key 的确定性试跑 ----
    def _mock_dimension(self, dim, paper: PaperText, meta: dict) -> DimensionResult:
        text = paper.text
        h = int(hashlib.md5(f"{paper.path}:{dim.name}".encode()).hexdigest(), 16)
        base = 62 + h % 30  # 62~91 的确定性基础分

        # 用简单的结构启发式修正分数，让 mock 结果有区分度
        bonus = 0
        if re.search(r"摘\s*要", text):
            bonus += 2
        if re.search(r"参考\s*文献|References", text, re.I):
            bonus += 2
        if meta["headings"]:
            bonus += min(3, len(meta["headings"]) // 4)
        if meta["n_chars"] > 12000:
            bonus += 2
        elif meta["n_chars"] < 3000:
            bonus -= 8
        if not re.search(r"\[[0-9]+\]|\(\w+ et al\.?,? \d{4}\)", text):
            bonus -= 3  # 无规范引用标记
        score = max(35, min(96, base + bonus))

        sents = [s.strip() for s in re.split(r"[。！？\n]", text) if 30 < len(s.strip()) < 90]
        if sents:
            n = len(sents)
            idx = {h % n, (h // n) % n}
            while len(idx) < min(2, n):
                idx.add((max(idx) + 1) % n)
            evidence = [sents[i] for i in sorted(idx)][:2]

        conf = round(0.6 + (h % 30) / 100, 2)
        comment = (
            f"【mock 试运行，非真实评分】{dim.name}维度："
            + ("结构要素基本齐全，" if bonus >= 0 else "结构要素有明显缺失，")
            + f"按启发式规则给出参考分 {score}。配置 API Key 后将调用大模型给出真实批改。"
        )
        return DimensionResult(
            name=dim.name,
            weight=dim.weight,
            score=float(score),
            confidence=conf,
            evidence=evidence,
            comment=comment,
            suspicions="" if re.search(r"参考\s*文献", text) else "未识别到参考文献列表",
        )
