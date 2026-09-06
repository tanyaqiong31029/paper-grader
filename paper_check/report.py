"""查重报告：可复核的对照式 HTML 报告 + 摘要 JSON。

对应方案第五章“报告可读性”：
- 总览：总相似比、单篇最大来源、来源分布条形图、章节热力
- 正文：逐句按相似度分级染色，点击染色句在右侧证据面板查看
  来源句对照与相似度（教师可逐条复核，避免“只有一个百分比”）
- 自包含单文件 HTML（无外部依赖），报告编号 + 参数记录可追溯
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
from datetime import datetime
from pathlib import Path

from .engine import CheckResult, Match
from .preprocess import PreparedDoc

TIER_COLORS = {
    "copy": ("#c0392b", "复制"),
    "near": ("#e67e22", "高度疑似"),
    "suspect": ("#f1c40f", "疑似"),
    "semantic": ("#2e86de", "改写疑似"),
}


def make_report_no(query_sha: str) -> str:
    return f"PC-{query_sha[:8].upper()}-{datetime.now():%Y%m%d%H%M%S}"


def _heat_color(ratio: float) -> str:
    if ratio >= 0.4:
        return "#c0392b"
    if ratio >= 0.2:
        return "#e67e22"
    if ratio > 0:
        return "#f1c40f"
    return "#2ecc71"


def render_summary(res: CheckResult, report_no: str, params: dict) -> dict:
    return {
        "report_no": report_no,
        "file": res.name,
        "total_ratio": res.total_ratio,
        "single_max_ratio": res.single_max_ratio,
        "matched_chars": res.matched_chars,
        "body_chars": res.body_chars,
        "sources": [
            {
                "name": s.name,
                "ratio": s.ratio,
                "matched_chars": s.matched_chars,
                "n_matches": s.n_matches,
            }
            for s in res.sources
        ],
        "chapters": res.chapters,
        "semantic_model": res.semantic_model,
        "timings": res.timings,
        "notes": res.notes,
        "params": params,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_html(res: CheckResult, report_no: str, summary: dict) -> str:
    esc = html_mod.escape
    assert res.prepared is not None  # render 只处理 check() 的产出，该路径下 prepared 必已生成
    prepared: PreparedDoc = res.prepared
    tier_by_q: dict[int, Match] = {}
    for m in res.matches:
        cur = tier_by_q.get(m.q_idx)
        if cur is None or (m.sim, m.semantic_cos) > (cur.sim, cur.semantic_cos):
            tier_by_q[m.q_idx] = m

    # ---------- 正文逐句渲染 ----------
    body_parts, sent_json = [], []
    for s in prepared.sentences:
        hit = tier_by_q.get(s.idx)
        if hit:
            color, _ = TIER_COLORS[hit.tier]
            body_parts.append(
                f'<span class="hit" id="q{s.idx}" style="background:{color}33;'
                f'border-bottom:2px solid {color}" '
                f'onclick="showMatch({s.idx})">{esc(s.display)}</span>'
            )
            sent_json.append(
                {
                    "idx": s.idx,
                    "text": s.display,
                    "tier": hit.tier,
                    "sim": hit.sim,
                    "cos": hit.semantic_cos,
                    "chapter": prepared.chapter_of(s.start),
                    "matches": [
                        {
                            "doc": x.doc_name,
                            "text": x.s_display,
                            "sim": x.sim,
                            "tier": x.tier,
                            "cos": x.semantic_cos,
                        }
                        for x in sorted(
                            [y for y in res.matches if y.q_idx == s.idx], key=lambda y: -y.sim
                        )[:3]
                    ],
                }
            )
        else:
            body_parts.append(f"<span>{esc(s.display)}</span>")
    body_html = " ".join(body_parts)
    sentences_json = json.dumps(sent_json, ensure_ascii=False)

    # ---------- 来源分布 ----------
    max_ratio = max((s.ratio for s in res.sources), default=0.0)
    src_rows = (
        "".join(
            f"<tr><td class='mono'>{esc(s.name)}</td>"
            f"<td class='num'>{s.matched_chars}</td>"
            f"<td class='num'>{s.ratio * 100:.1f}%</td>"
            f"<td><div class='bar'><div class='fill' style='width:{(s.ratio / max_ratio * 100) if max_ratio else 0:.0f}%'></div></div></td>"
            f"<td class='num'>{s.n_matches}</td></tr>"
            for s in res.sources
        )
        or "<tr><td colspan=5>未发现相似来源</td></tr>"
    )

    # ---------- 章节热力 ----------
    heat = (
        "".join(
            f"<div class='ch' style='background:{_heat_color(c['ratio'])}22;"
            f"border-left:4px solid {_heat_color(c['ratio'])}'>"
            f"<b>{esc(c['title'])}</b><br>"
            f"<span class='num'>{c['ratio'] * 100:.1f}%</span>（{c['body_chars']} 字）</div>"
            for c in res.chapters
        )
        or "<div class='ch'>未识别到章节结构</div>"
    )

    legend = "　".join(
        f"<span class='lg'><i style='background:{c}33;border-bottom:2px solid {c}'></i>{label}"
        f"{'（≥0.80）' if k == 'copy' else '（0.65-0.80）' if k == 'near' else '（0.55-0.65）' if k == 'suspect' else '（语义相似）'}</span>"
        for k, (c, label) in TIER_COLORS.items()
    )

    st = summary["params"].get("index_stats", {})
    pct = res.total_ratio * 100
    level = "高" if pct >= 30 else "中" if pct >= 15 else "低"

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>查重报告 {report_no}</title><style>
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f5f6f8;color:#2c3e50}}
.wrap{{max-width:1200px;margin:0 auto;padding:24px}}
.hd{{background:#2c3e50;color:#fff;border-radius:10px;padding:20px 26px;display:flex;justify-content:space-between;align-items:center}}
.hd .no{{font-size:12px;opacity:.75}}
.cards{{display:flex;gap:14px;margin:16px 0}}
.card{{flex:1;background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card .v{{font-size:30px;font-weight:700}} .card .l{{font-size:12px;color:#7f8c8d}}
.card.warn .v{{color:#c0392b}}
table{{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden}}
th,td{{padding:8px 12px;border-bottom:1px solid #ecf0f1;font-size:13px;text-align:left}}
th{{background:#34495e;color:#fff}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.mono{{font-family:ui-monospace,monospace;font-size:12px}}
.bar{{background:#ecf0f1;height:12px;border-radius:6px;width:100%}} .fill{{background:#2980b9;height:12px;border-radius:6px}}
.sec{{background:#fff;border-radius:10px;padding:18px 22px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.sec h3{{margin:0 0 12px;font-size:15px}}
.chs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}}
.ch{{border-radius:8px;padding:10px 12px;font-size:12px}}
.split{{display:grid;grid-template-columns:1.6fr 1fr;gap:14px}}
.pane{{background:#fff;border-radius:10px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
#text{{line-height:2;font-size:15px}}
.hit{{cursor:pointer;border-radius:3px;padding:1px 0}}
.hit:hover{{outline:2px solid #2c3e50}}
#evi{{position:sticky;top:12px;max-height:78vh;overflow:auto}}
.em{{border-left:3px solid #2980b9;background:#f8f9fa;border-radius:6px;padding:10px 12px;margin:10px 0;font-size:13px}}
.em .src{{color:#7f8c8d;font-size:12px;margin-bottom:4px}}
.lg i{{display:inline-block;width:22px;height:11px;border-radius:2px;margin-right:3px;vertical-align:middle}}
.lg{{font-size:12px;color:#555;margin-right:10px}}
.ft{{font-size:12px;color:#95a5a6;margin-top:18px;line-height:1.8}}
@media(max-width:900px){{.split{{grid-template-columns:1fr}}.cards{{flex-wrap:wrap}}}}
</style></head><body><div class="wrap">
<div class="hd"><div><h2 style="margin:0">论文查重报告</h2>
<div class="no">报告编号 {report_no}　文件 {esc(res.name)}　生成于 {summary["generated_at"]}</div></div>
<div style="text-align:right;font-size:12px;opacity:.85">比对库：{st.get("docs", 0)} 篇 / {st.get("sentences", 0)} 句<br>语义通道：{esc(res.semantic_model) or "未启用"}<br>耗时 {res.timings.get("total_ms", 0)} ms</div></div>

<div class="cards">
<div class="card {"warn" if pct >= 30 else ""}"><div class="v">{pct:.1f}%</div><div class="l">总相似比（{level}）— {res.matched_chars}/{res.body_chars} 字</div></div>
<div class="card"><div class="v">{res.single_max_ratio * 100:.1f}%</div><div class="l">单篇最大来源占比</div></div>
<div class="card"><div class="v">{len(res.sources)}</div><div class="l">相似来源文献数</div></div>
<div class="card"><div class="v">{res.timings.get("n_query_sentences", 0)}</div><div class="l">送检句数</div></div>
</div>

<div class="sec"><h3>相似来源分布</h3><table><tr><th>来源文献</th><th>重合字符</th><th>占全文</th><th>分布</th><th>命中句数</th></tr>{src_rows}</table></div>

<div class="sec"><h3>章节相似度热力</h3><div class="chs">{heat}</div></div>

<div class="sec" style="padding-bottom:10px"><h3>正文比对（点击染色句查看来源对照）</h3><div>{legend}</div></div>
<div class="split"><div class="pane"><div id="text">{body_html}</div></div>
<div class="pane" id="evi"><h3 style="margin:0 0 8px;font-size:14px">来源对照</h3><div id="evi-body" style="color:#7f8c8d;font-size:13px">点击左侧任意染色句，此处显示对应的来源原文与相似度。</div></div>
</div>

<div class="ft">参数：{esc(json.dumps(summary["params"], ensure_ascii=False))}<br>
{("备注：" + esc("；".join(summary["notes"])) + "<br>") if summary["notes"] else ""}
说明：本报告由本地查重引擎生成，相似度分级为辅助初筛参考，最终认定须由人工复核；
报告编号可用于防伪核验（与出具方记录比对）。</div></div>

<script>
const S = {sentences_json};
function showMatch(i) {{
  const s = S.find(x => x.idx === i); if (!s) return;
  document.querySelectorAll('.hit').forEach(e => e.style.outline = '');
  const el = document.getElementById('q' + i); if (el) el.style.outline = '2px solid #2c3e50';
  const c = {{copy:'#c0392b',near:'#e67e22',suspect:'#f1c40f',semantic:'#2e86de'}}[s.tier] || '#888';
  let h = `<div class="em"><div class="src">送检句（${{s.chapter}}）· 分级 <b style="color:${{c}}">${{{{copy:'复制',near:'高度疑似',suspect:'疑似',semantic:'改写疑似'}}[s.tier]}}}}</b> · 相似度 ${{s.sim.toFixed(2)}}${{s.cos ? ' · 语义余弦 ' + s.cos.toFixed(2) : ''}}</div><div>${{s.text}}</div></div>`;
  for (const m of s.matches) {{
    h += `<div class="em"><div class="src">来源：${{m.doc}} · ${{{{
      copy:'复制',near:'高度疑似',suspect:'疑似',semantic:'改写疑似'}}[m.tier]}} · 相似度 ${{m.sim.toFixed(2)}}</div><div>${{m.text}}</div></div>`;
  }}
  document.getElementById('evi-body').innerHTML = h;
}}
</script></body></html>"""


def save_report(res: CheckResult, out_dir: Path, params: dict) -> tuple[Path, str, dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(res.name.encode()).hexdigest()
    report_no = make_report_no(sha)
    summary = render_summary(res, report_no, params)
    html_text = render_html(res, report_no, summary)
    path = out_dir / f"查重报告__{Path(res.name).stem}.html"
    path.write_text(html_text, encoding="utf-8")
    (out_dir / f"查重摘要__{Path(res.name).stem}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return path, report_no, summary
