"""端到端流水线测试（使用假 LLM 客户端，不消耗 token、无需 API Key）。

运行：python3 tests/test_pipeline.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper_grader.config import AppConfig, LLMConfig
from paper_grader.extract import extract_paper
from paper_grader.grader import Grader, split_chunks
from paper_grader.llm import LLMError, parse_json_loose
from paper_grader.rubric import load_rubric

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ✓ {name}")


class FakeClient:
    """按调用序返回预设回复，记录请求内容。"""

    def __init__(self, replies, fail_on=None):
        self.replies = list(replies)
        self.prompts = []
        self.usage = {"prompt_tokens": 10, "completion_tokens": 5, "requests": 0}
        self.fail_on = fail_on  # 包含该子串的请求将抛错

    def chat_json(self, system, user):
        self.prompts.append(user)
        self.usage["requests"] += 1
        if self.fail_on and self.fail_on in user:
            raise LLMError("模拟网络故障")
        return self.replies.pop(0)


# ---------- 1. JSON 解析稳健性 ----------
def test_json_parsing():
    print("[1] JSON 解析稳健性")
    check("纯 JSON", parse_json_loose('{"score": 88}') == {"score": 88})
    check("```json 围栏", parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1})
    check("前后有说明文字",
          parse_json_loose('好的，结果如下：{"score": 77, "confidence": 0.9} 请查收')["score"] == 77)
    try:
        parse_json_loose("完全不是 JSON")
        check("非 JSON 报错", False)
    except LLMError:
        check("非 JSON 报错", True)


# ---------- 2. 短文直评路径 ----------
def test_short_paper():
    print("[2] 短文直评路径（course 量规）")
    cfg = AppConfig.load()
    paper = extract_paper(Path("samples/研究生课程论文_大语言模型在教育教学中的应用研究.docx"))
    dim_replies = [
        {"score": 90, "confidence": 0.9, "evidence": ["证据一"], "comment": "切题", "suspicions": ""},
        {"score": 80, "confidence": 0.8, "evidence": [], "comment": "概念准确", "suspicions": ""},
        {"score": 70, "confidence": 0.7, "evidence": [], "comment": "论证一般", "suspicions": ""},
        {"score": 60, "confidence": 0.6, "evidence": [], "comment": "结构尚可", "suspicions": ""},
        {"score": 50, "confidence": 0.5, "evidence": [], "comment": "引用不规范", "suspicions": "文献过少"},
    ]
    synth_reply = {
        "overall_comment": "总评文字",
        "strengths": ["选题实用"], "improvements": ["补充实验"], "flags": [],
    }
    client = FakeClient(dim_replies + [synth_reply])
    rubric = load_rubric(cfg, "course")
    res = Grader(cfg, client, rubric).grade(paper)

    expect = round((90 * 20 + 80 * 25 + 70 * 25 + 60 * 15 + 50 * 15) / 100, 1)
    check("加权总分正确", res.total == expect, f"got {res.total}, want {expect}")
    check("等级映射", res.band == "中等")
    check("置信度取均值", res.confidence == 0.7)
    check("综合评语写入", res.overall_comment == "总评文字")
    check("维度评语进入提示词", any("引用不规范" in p for p in client.prompts))
    check("未走分块路径", all("全文分块要点" not in p for p in client.prompts))


# ---------- 3. 长文分块 map-reduce 路径 ----------
def test_long_paper():
    print("[3] 长文分块 map 路径（thesis 量规，模拟超长论文）")
    cfg = AppConfig.load()
    cfg.grading.max_fulltext_chars = 1000  # 人为调低阈值触发分块
    cfg.grading.chunk_chars = 800

    long_text = "摘要：测试论文。\n\n" + "\n\n".join(
        f"第{i}段。" + "实验数据表明该方法有效，各项指标均优于基线模型，具体数值见正文表格。" * 4
        for i in range(60)
    )
    from paper_grader.extract import PaperText
    paper = PaperText(path=Path("fake_thesis.docx"), title="测试学位论文", text=long_text)

    n_chunks = len(split_chunks(long_text, 800))
    replies = [{"summary": f"块{i}要点", "strengths": [], "weaknesses": ["数据不足"], "quotes": []}
               for i in range(1, n_chunks + 1)]
    replies += [{"score": 75, "confidence": 0.75, "evidence": ["q"], "comment": "c", "suspicions": ""}
                for _ in range(6)]
    replies.append({"overall_comment": "ok", "strengths": [], "improvements": [], "flags": ["工作量偏少"]})

    client = FakeClient(replies)
    rubric = load_rubric(cfg, "thesis")
    res = Grader(cfg, client, rubric).grade(paper)

    check(f"分块数={n_chunks}，map 请求已发出", len(client.prompts) == n_chunks + 6 + 1)
    check("维度评分使用分块要点", any("全文分块要点" in p for p in client.prompts))
    check("分块要点包含块摘要", any("块1要点" in p for p in client.prompts))
    check("风险标记透传", "工作量偏少" in res.flags)
    check("总分 = Σ 权重×维度分", res.total == 75.0)


# ---------- 4. 失败处理与置信度标记 ----------
def test_failure_paths():
    print("[4] 失败处理与需人工复评标记")
    cfg = AppConfig.load()
    from paper_grader.extract import PaperText
    paper = PaperText(path=Path("f.docx"), title="t", text="正文。" * 100)

    replies = [{"score": s, "confidence": 0.3, "evidence": [], "comment": "c", "suspicions": ""}
               for s in (80, 80, 80, 80, 80)]
    replies.append({"overall_comment": "x", "strengths": [], "improvements": [], "flags": []})
    client = FakeClient(replies)
    res = Grader(cfg, client, load_rubric(cfg, "course")).grade(paper)
    check("低置信度 → 标记人工复评", any("人工复评" in f for f in res.flags))

    client2 = FakeClient([], fail_on="选题")
    try:
        Grader(cfg, client2, load_rubric(cfg, "course")).grade(paper)
        check("维度调用失败应抛出 LLMError", False)
    except LLMError:
        check("维度调用失败向上抛出（CLI 层兜底记入失败清单）", True)


def test_cli_cache_sig():
    print("[5] 缓存签名校验")
    from paper_grader.cli import _cache_valid
    from paper_grader.grader import GradeResult
    p = Path("config.yaml")
    res = GradeResult(file="config.yaml", title="t", ptype="course", rubric_name="r",
                      total=80, band="良好", confidence=0.8, dimensions=[])
    cached = {"model": "glm-4.6", "mock": False,
              "source_sig": {"size": p.stat().st_size, "mtime": int(p.stat().st_mtime)}}
    check("签名一致 → 命中缓存", _cache_valid(cached, p, "glm-4.6", False))
    check("模型不同 → 失效", not _cache_valid(cached, p, "deepseek-chat", False))
    check("mock 开关不同 → 失效", not _cache_valid(cached, p, "glm-4.6", True))
    cached["source_sig"]["size"] = -1
    check("文件变更 → 失效", not _cache_valid(cached, p, "glm-4.6", False))


if __name__ == "__main__":
    test_json_parsing()
    test_short_paper()
    test_long_paper()
    test_failure_paths()
    test_cli_cache_sig()
    print(f"\n全部 {PASS} 项断言通过 ✅")
