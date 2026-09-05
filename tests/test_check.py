"""查重引擎测试（纯本地，无需网络与模型下载）。

运行：python3 tests/test_check.py
覆盖：SimHash/LSH、编辑相似度、预处理（参考文献剔除/章节识别）、
端到端查重、秒传、不入库隐私模式、报告产物。
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper_check.align import edit_similarity, tier_of
from paper_check.engine import DedupEngine
from paper_check.fingerprint import LSHIndex, hamming, lsh_blocks, simhash
from paper_check.preprocess import prepare
from paper_check.report import save_report
from paper_check.store import LibraryStore, sha256_of

PASS = 0
TMP = Path("/tmp/paper_check_test")


def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ✓ {name}")


# ---------- 1. 指纹与 LSH ----------
def test_fingerprint():
    print("[1] SimHash 与 LSH 分块")
    a = "知识图谱以结构化三元组组织领域知识，为智能问答提供支撑"
    b = "知识图谱以结构化三元组组织领域知识，为智能问答提供支持"  # 一字之改
    c = "今天天气很好，适合出门散步和运动"
    fa, fb, fc = simhash(a), simhash(b), simhash(c)
    check("相同文本指纹相同", fa == simhash(a))
    check("一字之改汉明距离小", hamming(fa, fb) <= 8, f"d={hamming(fa, fb)}")
    check("无关文本汉明距离大", hamming(fa, fc) >= 20, f"d={hamming(fa, fc)}")
    # 鸽笼原理：汉明距离 ≤3 的指纹至少共享一个 16-bit 分块
    check(
        "LSH 鸽笼保证（d≤3 必共享分块）",
        all(_shares_block(fa, fa ^ (1 << k)) for k in range(3)),
        "逐位翻转自检",
    )
    # 已知边界：d=4 且 4 位分散在各块（每块恰好错 1 位）时可能漏召回
    spread = (1 << 0) | (1 << 16) | (1 << 32) | (1 << 48)
    check(
        "d=4 分散翻转是已知盲区（原型以 max_hamming 校验兜底）",
        not _shares_block(0, spread) and hamming(0, spread) == 4,
    )


def _shares_block(a: int, b: int) -> bool:
    return any(x == y for x, y in zip(lsh_blocks(a), lsh_blocks(b), strict=False))


def test_lsh_recall():
    print("[2] LSH 倒排召回")
    idx = LSHIndex()
    sents = [
        "大语言模型在教育场景中应用广泛",
        "今天食堂的红烧肉非常好吃",
        "基于检索增强生成的系统降低了幻觉率",
    ]
    for i, s in enumerate(sents):
        idx.add(1, i, simhash(s))
    q = simhash("基于检索增强生成的系统降低了幻觉率")  # 完全一致
    cands = idx.candidates(q)
    check("精确句可召回", (1, 2) in cands)
    q2 = simhash("基于检索增强生成的系统降低了幻觉率。")  # 多一个标点（归一化后一致）
    check("微扰句可召回", (1, 2) in idx.candidates(q2))


# ---------- 2. 精排与预处理 ----------
def test_align():
    print("[3] 编辑相似度与分级")
    check("完全一致 = 1.0", edit_similarity("人工智能改变世界", "人工智能改变世界") == 1.0)
    s1 = "模型按照评分量规对学生作文进行多维度打分并给出修改建议，能够减轻教师的重复劳动"
    s2 = "模型按照评分量规对学生作文进行多维度打分并给出修改建议，减轻了教师的重复劳动"
    sim = edit_similarity(s1, s2)
    check("轻度改写 ≥0.8", 0.8 <= sim < 1.0, f"sim={sim:.3f}")
    check(
        "无关句子 <0.4",
        edit_similarity("知识图谱以三元组组织知识", "今天天气晴朗适合郊游踏青") < 0.4,
    )
    check(
        "分级映射", tier_of(0.9) == "copy" and tier_of(0.7) == "near" and tier_of(0.6) == "suspect"
    )


def test_preprocess():
    print("[4] 预处理：参考文献剔除与章节识别")
    text = Path("corpus/文献库_知识图谱综述.txt").read_text(encoding="utf-8")
    doc = prepare(text, name="t")
    check("识别到章节", len(doc.chapters) >= 3, f"chapters={len(doc.chapters)}")
    check("剔除参考文献", doc.excluded_ref_chars > 0)
    check("参考文献内容不进入比对句", all("TKDE" not in s.norm for s in doc.sentences))
    check("正文句数合理", 10 <= len(doc.sentences) <= 30, f"n={len(doc.sentences)}")


# ---------- 3. 端到端 ----------
def test_e2e():
    print("[5] 端到端：建库 → 查重 → 报告")
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    store = LibraryStore(TMP / "lib.db")
    engine = DedupEngine(store)
    for f in sorted(Path("corpus").glob("*.txt")):
        text = f.read_text(encoding="utf-8")
        prepared = prepare(text, name=f.name)
        engine.add_document(sha256_of(text.encode()), f.name, prepared)
    check("库规模 3 篇", store.stats()["docs"] == 3)

    from paper_check.extract_shared import extract_any

    text, _ = extract_any(Path("samples/查重测试_学生论文_部分抄袭.docx"))
    prepared = prepare(text, name="查重测试.docx")
    res = engine.check(prepared)

    check(
        "总相似比在预期区间（30%~50%）", 0.30 <= res.total_ratio <= 0.50, f"ratio={res.total_ratio}"
    )
    src_names = {s.name for s in res.sources}
    check(
        "命中全部 3 个真实来源",
        {"文献库_知识图谱综述.txt", "文献库_大模型教育应用.txt", "文献库_自动评分研究.txt"}
        == src_names,
        f"got={src_names}",
    )
    check(
        "最大来源为知识图谱综述（逐字段落最长）",
        res.sources[0].name == "文献库_知识图谱综述.txt",
        f"got={res.sources[0].name}",
    )
    tiers = {m.tier for m in res.matches}
    check("存在“复制”级命中", "copy" in tiers)
    check("改写段被捕捉（高度疑似/疑似）", tiers & {"near", "suspect"}, f"tiers={tiers}")
    check(
        "原创段无误报来源",
        not any("混合方法" in m.s_display or "技术接受模型" in m.s_display for m in res.matches),
    )
    check("章节热力非空", len(res.chapters) >= 3)

    params = {"use_semantic": True, "index_stats": engine.library_stats()}
    html_path, report_no, summary = save_report(res, TMP / "reports", params)
    html = Path(html_path).read_text(encoding="utf-8")
    check("报告编号格式", report_no.startswith("PC-"))
    check("报告含染色句", 'class="hit"' in html)
    check("报告含来源分布", "相似来源分布" in html)
    json.loads(json.dumps(summary, ensure_ascii=False))  # 可序列化

    # 秒传
    key = "k1"
    store.save_report(report_no, "qsha123", key, res.total_ratio, str(html_path), summary)
    hit = store.find_cached("qsha123", key)
    check("秒传命中", hit is not None and Path(hit["html_path"]).exists())
    store.close()


def test_no_index_privacy():
    print("[6] 不入库隐私模式")
    db = TMP / "privacy.db"
    store = LibraryStore(db)
    engine = DedupEngine(store)
    lib_text = Path("corpus/文献库_知识图谱综述.txt").read_text(encoding="utf-8")
    engine.add_document(
        sha256_of(lib_text.encode()), "库文献.txt", prepare(lib_text, name="库文献.txt")
    )
    check("初始库 1 篇", store.stats()["docs"] == 1)

    from paper_check.extract_shared import extract_any

    text, _ = extract_any(Path("samples/查重测试_学生论文_部分抄袭.docx"))
    prepared = prepare(text, name="student.docx")
    res = engine.check(prepared)  # check 不调用 add_document
    check("查重结果正常产出", res.total_ratio > 0.15, f"ratio={res.total_ratio}")
    check("查重后库规模不变（学生论文未入库）", store.stats()["docs"] == 1)
    check("学生论文句指纹未进入索引", engine.index.n_sentences == len(prepare(lib_text).sentences))
    store.close()


if __name__ == "__main__":
    test_fingerprint()
    test_lsh_recall()
    test_align()
    test_preprocess()
    test_e2e()
    test_no_index_privacy()
    print(f"\n全部 {PASS} 项断言通过 ✅")
