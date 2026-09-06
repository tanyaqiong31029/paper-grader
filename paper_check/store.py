"""文献库与检测结果存储（SQLite）。

对应方案的数据库设计（原型规模）：
- docs：已入库文献（指纹索引起点），sha256 去重
- sentences：句级指纹（引擎启动时载入内存构建 LSH 桶；规模化时此表
  迁移到 ES/向量库，见 README）
- reports：历史报告（支撑“秒传”——同文件同参数直接复用结果）

隐私设计：
- no_index 模式：检测结果照常产出，但文件内容与句指纹绝不写入 docs/
  sentences（学生自查默认），仅保留报告本体。
- 原始文件默认不落盘：入库只存指纹与句子文本；检测完成后临时文件删除。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT UNIQUE,
    name TEXT,
    n_chars INTEGER,
    n_sentences INTEGER,
    added_at REAL
);
CREATE TABLE IF NOT EXISTS sentences (
    doc_id INTEGER,
    sent_idx INTEGER,
    fp INTEGER,
    norm TEXT,
    display TEXT,
    PRIMARY KEY (doc_id, sent_idx)
);
CREATE INDEX IF NOT EXISTS idx_sent_fp ON sentences(fp);
CREATE TABLE IF NOT EXISTS reports (
    report_no TEXT PRIMARY KEY,
    query_sha256 TEXT,
    params_key TEXT,
    created_at REAL,
    total_ratio REAL,
    html_path TEXT,
    summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_q ON reports(query_sha256, params_key);
"""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LibraryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 文献入库 ----------
    def add_document(self, sha: str, name: str, prepared) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO docs(sha256,name,n_chars,n_sentences,added_at) VALUES(?,?,?,?,?)",
            (sha, name, prepared.body_chars, len(prepared.sentences), time.time()),
        )
        if cur.rowcount == 0:  # 已存在（按内容哈希去重）
            return self.conn.execute("SELECT id FROM docs WHERE sha256=?", (sha,)).fetchone()[0]
        doc_id = cur.lastrowid
        # 走到此处 INSERT 必已成功（rowcount != 0），lastrowid 必为新生成主键
        assert doc_id is not None
        self.conn.executemany(
            "INSERT OR REPLACE INTO sentences(doc_id,sent_idx,fp,norm,display) VALUES(?,?,?,?,?)",
            [(doc_id, s.idx, simhash_fp(s.norm), s.norm, s.display) for s in prepared.sentences],
        )
        self.conn.commit()
        return doc_id

    def load_sentences(self) -> list[tuple[int, int, int, str, str]]:
        from .fingerprint import to_uint64

        rows = self.conn.execute(
            "SELECT doc_id, sent_idx, fp, norm, display FROM sentences"
        ).fetchall()
        return [(d, s, to_uint64(fp), n, t) for d, s, fp, n, t in rows]

    def doc_names(self) -> dict[int, str]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT id, name FROM docs")}

    def stats(self) -> dict:
        docs = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(n_chars),0) FROM docs").fetchone()
        sents = self.conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        return {"docs": docs[0], "chars": docs[1], "sentences": sents}

    def doc_source_text(self, doc_id: int) -> str:
        """重建来源文档文本（报告对照用；库里本就存了句子原文）。"""
        rows = self.conn.execute(
            "SELECT display FROM sentences WHERE doc_id=? ORDER BY sent_idx", (doc_id,)
        ).fetchall()
        return "".join(r[0] for r in rows)

    # ---------- 报告（秒传支撑） ----------
    def save_report(
        self,
        report_no: str,
        query_sha: str,
        params_key: str,
        total_ratio: float,
        html_path: str,
        summary: dict,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?,?,?)",
            (
                report_no,
                query_sha,
                params_key,
                time.time(),
                total_ratio,
                html_path,
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def find_cached(self, query_sha: str, params_key: str):
        row = self.conn.execute(
            "SELECT report_no, html_path FROM reports WHERE query_sha256=? AND params_key=? "
            "ORDER BY created_at DESC LIMIT 1",
            (query_sha, params_key),
        ).fetchone()
        if row and Path(row[1]).exists():
            return {"report_no": row[0], "html_path": row[1]}
        return None

    def get_summary(self, report_no: str) -> dict | None:
        row = self.conn.execute(
            "SELECT summary_json FROM reports WHERE report_no=?", (report_no,)
        ).fetchone()
        return json.loads(row[0]) if row else None


def simhash_fp(norm: str) -> int:
    from .fingerprint import simhash, to_signed64

    return to_signed64(simhash(norm))
