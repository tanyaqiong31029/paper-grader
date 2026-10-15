"""回归测试：报告 XSS、本地模型免 Key、PII 脱敏、mock 短文、论文收集/缓存键等修复。

运行：pytest tests/test_fixes.py -q
"""

import importlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper_check.align import Match
from paper_check.engine import CheckResult
from paper_check.preprocess import prepare
from paper_check.report import render_html, render_summary
from paper_grader.config import AppConfig, LLMConfig
from paper_grader.extract import PaperText, collect_papers, paper_key
from paper_grader.grader import Grader
from paper_grader.llm import LLMClient, LLMError, is_local_endpoint
from paper_grader.pii import redact_pii
from paper_grader.rubric import load_rubric

ROOT = Path(__file__).resolve().parent.parent


# ---------- PII 脱敏 ----------
def test_pii_id_card_fully_redacted():
    assert redact_pii("身份证号110101199003071234") == "身份证号[已脱敏]"
    assert redact_pii("身份证：11010119900307123X。") == "身份证：[已脱敏]。"


def test_pii_phone_email_landline():
    out = redact_pii("手机13812345678，+86 13912345678，邮箱a.b@x.edu.cn，电话010-1234-5678")
    assert "138" not in out and "139" not in out
    assert "@" not in out and "010-1234" not in out
    # 长数字串中的 11 位片段不应被当成手机号截走一部分
    assert redact_pii("编号913812345678901") == "编号913812345678901"


# ---------- mock 模式短文 ----------
def test_mock_short_paper_no_unbound_error():
    cfg = AppConfig.load(ROOT / "config.yaml")
    rubric = load_rubric(cfg, "course")
    paper = PaperText(path=Path("short.txt"), title="短文", text="很短的正文。")
    res = Grader(cfg, LLMClient(cfg.llm), rubric, mock=True).grade(paper)
    assert res.error is None
    assert len(res.dimensions) == len(rubric.dimensions)
    assert all(d.evidence == [] for d in res.dimensions)


# ---------- 本地模型免 API Key ----------
def test_is_local_endpoint_uses_hostname():
    assert is_local_endpoint("http://localhost:11434/v1")
    assert is_local_endpoint("http://127.0.0.1:8000/v1")
    assert is_local_endpoint("http://[::1]:11434/v1")
    assert not is_local_endpoint("https://localhost.evil.com/v1")
    assert not is_local_endpoint("https://api.example.com/v1?h=127.0.0.1")
    assert not is_local_endpoint("https://open.bigmodel.cn/api/paas/v4")


def test_local_llm_without_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(LLMConfig(base_url="http://localhost:11434/v1", api_key=""))
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert client.enabled
    assert client.chat("sys", "user") == "ok"
    assert seen["auth"] is None
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"


def test_remote_llm_without_key_still_rejected():
    client = LLMClient(LLMConfig(base_url="https://localhost.evil.com/v1", api_key=""))
    assert not client.enabled
    try:
        client.chat("sys", "user")
        raise AssertionError("远程端点无 Key 应报错")
    except LLMError:
        pass


def test_env_api_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("PAPER_GRADER_API_KEY", "env-key")
    assert LLMConfig.from_dict({"api_key": "file-key"}).api_key == "env-key"
    monkeypatch.delenv("PAPER_GRADER_API_KEY")
    assert LLMConfig.from_dict({"api_key": "file-key"}).api_key == "file-key"


# ---------- 维度分数宽松解析 ----------
def test_normalize_dimension_tolerates_units():
    cfg = AppConfig.load(ROOT / "config.yaml")
    rubric = load_rubric(cfg, "course")
    g = Grader(cfg, LLMClient(cfg.llm), rubric)
    dim = rubric.dimensions[0]
    assert g._normalize_dimension(dim, {"score": "78分", "confidence": "0.8"}).score == 78.0
    assert g._normalize_dimension(dim, {"score": "约85.5/100"}).score == 85.5
    assert g._normalize_dimension(dim, {"score": "无"}).score == 0.0
    assert g._normalize_dimension(dim, {"score": 150}).score == 100.0


# ---------- 查重报告转义 ----------
def test_check_report_escapes_script_payloads():
    evil = "</script><script>alert(1)</script><!--"
    text = f"第一章 绪论\n{evil}这是一个用于测试的足够长的句子内容。\n"
    prepared = prepare(text, name="evil.txt")
    s0 = next(x for x in prepared.sentences if "alert(1)" in x.display)
    match = Match(
        q_idx=s0.idx,
        doc_id=1,
        doc_name="<img src=x onerror=alert(2)>",
        s_idx=0,
        s_display=evil,
        sim=0.9,
        tier="copy",
    )
    res = CheckResult(
        name="evil.txt",
        body_chars=len(text),
        matched_chars=10,
        total_ratio=0.1,
        single_max_ratio=0.1,
        sources=[],
        matches=[match],
        spans=[],
        chapters=[],
        prepared=prepared,
    )
    summary = render_summary(res, "PC-TEST", {})
    html = render_html(res, "PC-TEST", summary)
    assert html.count("</script>") == 1  # 只有报告自身的闭合标签
    assert "<script>alert" not in html
    assert "<!--" not in html
    assert "<img src=x" not in html
    # 嵌入的 JSON 仍能还原出原文
    start = html.index("const S = ") + len("const S = ")
    data = json.loads(html[start : html.index(";\n", start)])
    assert "</script><script>alert(1)</script>" in data[0]["text"]
    assert data[0]["matches"][0]["doc"] == "<img src=x onerror=alert(2)>"
    # 证据面板用转义函数渲染，而不是直接拼接原文
    assert "escHtml(s.text)" in html and "escHtml(m.doc)" in html


# ---------- 论文收集与缓存键 ----------
def test_collect_papers_exclusions(tmp_path):
    base = tmp_path / "output" / "papers"  # 输入目录本身位于名为 output 的目录下
    (base / "sub").mkdir(parents=True)
    (base / "output").mkdir()
    (base / "results").mkdir()
    (base / "a.txt").write_text("a", encoding="utf-8")
    (base / "sub" / "b.md").write_text("b", encoding="utf-8")
    (base / "output" / "old.txt").write_text("x", encoding="utf-8")
    (base / "results" / "批改报告__a.md").write_text("x", encoding="utf-8")
    (base / ".hidden.txt").write_text("x", encoding="utf-8")

    found = collect_papers(base, exclude=base / "results")
    names = sorted(f.relative_to(base).as_posix() for f in found)
    assert names == ["a.txt", "sub/b.md"]
    # 不传 exclude 时，自定义 --out 目录下的文件会被收进来（证明 exclude 生效）
    assert any(f.parent.name == "results" for f in collect_papers(base))


def test_paper_key_distinguishes_same_name(tmp_path):
    (tmp_path / "1班").mkdir()
    (tmp_path / "2班").mkdir()
    a, b = tmp_path / "1班" / "张三.docx", tmp_path / "2班" / "张三.docx"
    for f in (a, b):
        f.write_text("x", encoding="utf-8")
    ka, kb = paper_key(a, tmp_path), paper_key(b, tmp_path)
    assert ka != kb
    assert ka.startswith("张三__") and "/" not in ka


def test_cache_rejects_errors_and_signature_changes():
    from paper_grader.cli import _cache_valid

    p = ROOT / "config.yaml"
    cached = {
        "result": {"error": None},
        "model": "m",
        "mock": False,
        "ptype": "course",
        "rubric_sig": "abc",
        "redact_pii": True,
        "source_sig": {"size": p.stat().st_size, "mtime": int(p.stat().st_mtime)},
    }
    kw = {"ptype": "course", "rubric_sig": "abc", "redact_pii": True}
    assert _cache_valid(cached, p, "m", False, **kw)
    assert not _cache_valid(cached, p, "m", False, **{**kw, "ptype": "thesis"})
    assert not _cache_valid(cached, p, "m", False, **{**kw, "rubric_sig": "def"})
    assert not _cache_valid(cached, p, "m", False, **{**kw, "redact_pii": False})
    cached["result"]["error"] = "LLMError: boom"
    assert not _cache_valid(cached, p, "m", False, **kw)


def test_grade_cli_mock_same_name_no_collision(tmp_path):
    from paper_grader.cli import main

    src = tmp_path / "in"
    for cls in ("1班", "2班"):
        (src / cls).mkdir(parents=True)
        (src / cls / "课程论文.txt").write_text(f"{cls}的课程论文正文。" * 20, encoding="utf-8")
    out = tmp_path / "out"
    assert main(["grade", str(src), "--mock", "--out", str(out)]) == 0
    assert len(list((out / "cache").glob("*.json"))) == 2
    assert len(list((out / "报告").glob("*.md"))) == 2


# ---------- paper_check 命令行与服务 ----------
def test_check_cli_top_level_db_not_overridden():
    from paper_check.cli import DEFAULT_DB, build_argparser

    ap = build_argparser()
    a = ap.parse_args(["--db", "x.db", "--reports", "r", "check", "f.txt"])
    assert (a.db, a.reports) == ("x.db", "r")
    a = ap.parse_args(["check", "--db", "y.db", "f.txt"])
    assert a.db == "y.db"
    a = ap.parse_args(["check", "f.txt"])
    assert a.db == str(DEFAULT_DB)


def test_serve_passes_paths_to_service(monkeypatch, tmp_path):
    import uvicorn

    from paper_check import cli

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    # 先 setenv 登记原值，测试结束时 monkeypatch 会还原 cmd_serve 写入的环境变量
    monkeypatch.setenv("PAPER_CHECK_DB", "")
    monkeypatch.setenv("PAPER_CHECK_REPORTS", "")
    db, rep = tmp_path / "lib.db", tmp_path / "reports"
    assert cli.main(["serve", "--db", str(db), "--reports", str(rep)]) == 0
    import paper_check.service as service

    service = importlib.reload(service)
    assert db == service.DB_PATH
    assert rep == service.REPORTS_DIR
    monkeypatch.undo()
    importlib.reload(service)


def test_service_token_required(monkeypatch):
    from fastapi.testclient import TestClient

    import paper_check.service as service

    monkeypatch.setenv("PAPER_CHECK_TOKEN", "s3cret")
    service = importlib.reload(service)
    try:
        c = TestClient(service.app)
        assert c.get("/api/task/nope").status_code == 401
        assert c.get("/api/task/nope", headers={"X-Admin-Token": "bad"}).status_code == 401
        assert c.get("/api/task/nope", headers={"X-Admin-Token": "s3cret"}).status_code == 404
        page = c.get("/").text
        assert "X-Admin-Token" in page and "sessionStorage" in page
    finally:
        monkeypatch.delenv("PAPER_CHECK_TOKEN")
        importlib.reload(service)
