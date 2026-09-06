"""命令行入口。

用法：
  python -m paper_grader grade <论文文件夹或单文件> [选项]

选项：
  --type {course,journal,thesis,auto}  论文类型，auto=按文件名自动识别（默认）
  --out DIR       结果输出目录（默认 ./output）
  --mock          试运行：不调用大模型，用启发式假分数走通全流程
  --force         忽略缓存，全部重新批改
  --config PATH   指定配置文件（默认项目根 config.yaml）

其他子命令：
  python -m paper_grader report --out DIR   # 仅从缓存重新生成汇总 Excel
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import __version__
from .config import AppConfig
from .extract import collect_papers, extract_paper
from .grader import DimensionResult, Grader, GradeResult
from .llm import LLMClient
from .report import save_failures_csv, save_report, save_summary_xlsx
from .rubric import load_rubric


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="paper_grader", description="学术论文自动批改工具")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("grade", help="批量批改论文")
    g.add_argument("path", help="论文文件夹或单个论文文件")
    g.add_argument("--type", default="auto", choices=["auto", "course", "journal", "thesis"])
    g.add_argument("--out", default="output", help="结果输出目录")
    g.add_argument("--mock", action="store_true", help="试运行，不调用大模型")
    g.add_argument("--force", action="store_true", help="忽略缓存重新批改")
    g.add_argument("--config", default=None, help="配置文件路径")

    r = sub.add_parser("report", help="从缓存重新生成汇总 Excel")
    r.add_argument("--out", default="output", help="缓存所在目录")
    r.add_argument("--config", default=None)
    return ap


def _cache_path(out_dir: Path, stem: str) -> Path:
    return out_dir / "cache" / f"{stem}.json"


def _result_from_dict(d: dict) -> GradeResult:
    d = dict(d)
    d["dimensions"] = [DimensionResult(**x) for x in d.get("dimensions", [])]
    return GradeResult(**d)


def _cache_valid(cached: dict, path: Path, model: str, mock: bool) -> bool:
    sig = cached.get("source_sig", {})
    return (
        cached.get("model") == model
        and cached.get("mock") == mock
        and sig.get("size") == path.stat().st_size
        and sig.get("mtime") == int(path.stat().st_mtime)
    )


def grade_files(args) -> int:
    cfg = AppConfig.load(args.config)
    out_dir = Path(args.out)
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    files = collect_papers(args.path)
    if not files:
        print(f"在 {args.path} 中未找到论文文件（支持 .pdf .docx .txt .md）")
        return 1

    print(
        f"共发现 {len(files)} 篇论文，类型策略：{args.type}"
        + ("，mock 试运行" if args.mock else "")
    )
    client = LLMClient(cfg.llm)
    if not args.mock and not client.enabled:
        print(
            "\n[提示] 未配置 API Key：请 export PAPER_GRADER_API_KEY=... 或使用 --mock 模式试跑。\n"
        )
        return 1

    results: list[GradeResult] = []
    pending: list[tuple[Path, str]] = []
    t0 = time.time()

    for f in files:
        ptype = cfg.detect_paper_type(f.name) if args.type == "auto" else args.type
        cp = _cache_path(out_dir, f.stem)
        if not args.force and cp.exists():
            try:
                cached = json.loads(cp.read_text(encoding="utf-8"))
                if _cache_valid(cached, f, cfg.llm.model, args.mock):
                    res = _result_from_dict(cached["result"])
                    results.append(res)
                    print(f"  [缓存] {f.name} → {res.total}（{res.band}）")
                    continue
            except Exception:
                pass  # 缓存损坏则重新批改
        pending.append((f, ptype))

    def work(item: tuple[Path, str]) -> GradeResult:
        path, ptype = item
        rubric = load_rubric(cfg, ptype)
        grader = Grader(cfg, client, rubric, mock=args.mock)
        try:
            paper = extract_paper(path)
            res = grader.grade(paper)
        except Exception as e:  # 单篇失败不拖垮整批（80 份里坏一份很常见）
            res = GradeResult(
                file=path.name,
                title=path.stem,
                ptype=ptype,
                rubric_name=rubric.name,
                total=0,
                band="—",
                confidence=0,
                dimensions=[],
                error=f"{type(e).__name__}: {e}"[:300],
            )
        return res

    if pending:
        workers = max(1, cfg.grading.concurrent if not args.mock else 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(work, it): it for it in pending}
            for done, fut in enumerate(as_completed(futs), start=1):
                res = fut.result()
                results.append(res)
                if res.error:
                    print(f"  [{done}/{len(pending)}] ✗ {res.file}：{res.error[:120]}")
                else:
                    print(
                        f"  [{done}/{len(pending)}] {res.file} → {res.total} 分"
                        f"（{res.band}，置信度 {res.confidence:.2f}）"
                    )
                # 写缓存（失败的也记下来，便于排查；--force 可重试）
                cp = _cache_path(out_dir, Path(res.file).stem)
                src = Path(futs[fut][0])
                cp.write_text(
                    json.dumps(
                        {
                            "result": res.to_dict(),
                            "model": cfg.llm.model,
                            "mock": args.mock,
                            "source_sig": {
                                "size": src.stat().st_size,
                                "mtime": int(src.stat().st_mtime),
                            },
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    encoding="utf-8",
                )

    # ---------- 汇总输出 ----------
    print("\n===== 批改完成 =====")
    ok = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]
    for r in sorted(ok, key=lambda x: -x.total):
        report_path = save_report(
            r,
            out_dir / "报告",
            model_note=f"AI 辅助（{cfg.llm.model}）" if not r.mock else "mock 试运行",
        )
        print(f"  {r.total:>5} {r.band}  {r.file}  →  {report_path.name}")
    if failed:
        print(f"\n失败 {len(failed)} 篇：")
        for r in failed:
            assert r.error is not None  # failed 仅由 error 非空的结果构成（见上方筛选）
            print(f"  ✗ {r.file}：{r.error[:150]}")
        print(f"失败清单：{save_failures_csv(failed, out_dir)}")

    xlsx = save_summary_xlsx(results, out_dir)
    print(f"\n成绩汇总：{xlsx}")
    if not args.mock and client.usage["requests"]:
        u = client.usage
        print(
            f"模型调用：{u['requests']} 次，"
            f"输入 {u['prompt_tokens']} tokens / 输出 {u['completion_tokens']} tokens"
        )
    print(f"耗时 {time.time() - t0:.0f} 秒。单篇报告在 {out_dir / '报告'} 目录。")
    return 0 if not failed else 2


def report_from_cache(args) -> int:
    _cfg = AppConfig.load(args.config)  # 保留配置加载/校验副作用
    out_dir = Path(args.out)
    cache_files = sorted((out_dir / "cache").glob("*.json"))
    if not cache_files:
        print("缓存目录为空，无历史结果可汇总。")
        return 1
    results = []
    for cp in cache_files:
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            results.append(_result_from_dict(data["result"]))
        except Exception as e:
            print(f"跳过损坏缓存 {cp.name}: {e}")
    xlsx = save_summary_xlsx(results, out_dir)
    print(f"已重新生成：{xlsx}（共 {len(results)} 条记录）")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    if args.command == "grade":
        return grade_files(args)
    return report_from_cache(args)


if __name__ == "__main__":
    sys.exit(main())
