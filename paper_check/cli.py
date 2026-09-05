"""查重命令行入口。

用法：
  python3 -m paper_check index <文献文件夹>     # 建设比对库（显式操作）
  python3 -m paper_check check <论文文件>       # 查重（默认不入库，隐私安全）
  python3 -m paper_check serve [--port 8000]    # 启动 Web 服务（异步任务链）

设计约束：check 永远不会把送检文件写入比对库——学生自查的论文不会
污染文献库，也不会泄露给后续送检者（对应方案“不入库模式”）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract_shared import extract_any
from .preprocess import prepare
from .report import save_report
from .store import LibraryStore, sha256_of

DEFAULT_DB = Path("output/check/library.db")
DEFAULT_REPORTS = Path("output/check/reports")


def build_params_key(use_semantic: bool, store: LibraryStore) -> str:
    import hashlib
    import json

    sig = f"sem={use_semantic}|{store.stats()}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def run_check(db: Path, reports_dir: Path, file_path: Path, use_semantic: bool = True,
              verbose: bool = False) -> dict:
    store = LibraryStore(db)
    try:
        from .engine import DedupEngine

        raw = file_path.read_bytes()
        qsha = sha256_of(raw)
        params_key = build_params_key(use_semantic, store)

        cached = store.find_cached(qsha, params_key)
        if cached:
            summary = store.get_summary(cached["report_no"]) or {}
            summary["cached"] = True
            summary["html_path"] = cached["html_path"]
            print(f"[秒传] 该文件在此比对库版本下已检测过，直接复用报告："
                  f"{cached['report_no']}")
            return summary

        engine = DedupEngine(store)
        text, _warn = extract_any(file_path)
        prepared = prepare(text, name=file_path.name)
        if verbose:
            print(f"预处理：{len(prepared.sentences)} 句 / 正文 {prepared.body_chars} 字符"
                  f"{'；'.join(prepared.notes)}")

        def progress(stage, pct, msg):
            if verbose:
                print(f"  [{stage} {pct:>3}%] {msg}")

        result = engine.check(prepared, progress=progress, use_semantic=use_semantic)
        params = {"use_semantic": use_semantic, "exclude_refs": True,
                  "index_stats": engine.library_stats()}
        html_path, report_no, summary = save_report(result, reports_dir, params)
        store.save_report(report_no, qsha, params_key, result.total_ratio,
                          str(html_path), summary)
        summary["cached"] = False
        summary["html_path"] = str(html_path)
        return summary
    finally:
        store.close()


def cmd_index(args) -> int:
    store = LibraryStore(args.db)
    try:
        from .engine import DedupEngine

        files = [f for f in sorted(Path(args.path).rglob("*"))
                 if f.is_file() and f.suffix.lower() in {".txt", ".md", ".pdf", ".docx"}
                 and not f.name.startswith(("~", "."))] if Path(args.path).is_dir() else [Path(args.path)]
        if not files:
            print(f"未找到可入库的文献文件（.txt/.md/.pdf/.docx）：{args.path}")
            return 1
        n_new = 0
        for f in files:
            raw = f.read_bytes()
            sha = sha256_of(raw)
            if store.conn.execute("SELECT 1 FROM docs WHERE sha256=?", (sha,)).fetchone():
                print(f"  [跳过] {f.name}（已入库）")
                continue
            text, _ = extract_any(f)
            prepared = prepare(text, name=f.name)
            engine = DedupEngine(store)
            engine.add_document(sha, f.name, prepared)
            n_new += 1
            print(f"  [入库] {f.name}：{len(prepared.sentences)} 句")
        print(f"入库完成：新增 {n_new} 篇，当前库 {store.stats()}")
        return 0
    finally:
        store.close()


def cmd_check(args) -> int:
    summary = run_check(Path(args.db), Path(args.reports), Path(args.file),
                        use_semantic=not args.no_semantic, verbose=True)
    print(f"\n总相似比：{summary['total_ratio'] * 100:.1f}%"
          f"（单篇最大来源 {summary['single_max_ratio'] * 100:.1f}%）")
    for s in summary.get("sources", [])[:5]:
        print(f"  - {s['name']}：{s['ratio'] * 100:.1f}%（{s['n_matches']} 句）")
    print(f"报告：{summary['html_path']}")
    print(f"报告编号：{summary['report_no']}" + ("（秒传复用）" if summary.get("cached") else ""))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("paper_check.service:app", host=args.host, port=args.port)
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(DEFAULT_DB), help="比对库 SQLite 路径")
    common.add_argument("--reports", default=str(DEFAULT_REPORTS), help="报告输出目录")

    ap = argparse.ArgumentParser(prog="paper_check", description="论文查重（本地三级漏斗引擎）",
                                 parents=[common])
    sub = ap.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("index", help="建设比对库", parents=[common])
    p1.add_argument("path")
    p1.set_defaults(func=cmd_index)

    p2 = sub.add_parser("check", help="查重一篇（不入库）", parents=[common])
    p2.add_argument("file")
    p2.add_argument("--no-semantic", action="store_true", help="关闭语义通道")
    p2.set_defaults(func=cmd_check)

    p3 = sub.add_parser("serve", help="启动 Web 异步任务服务", parents=[common])
    p3.add_argument("--host", default="127.0.0.1")
    p3.add_argument("--port", type=int, default=8018)
    p3.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
