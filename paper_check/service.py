"""查重 Web 服务：异步任务链（上传 → 任务ID → 进度轮询 → 报告）。

对应方案第四章“高并发与异步化”的原型实现：
- 上传即返回 task_id，检测在后台线程池执行，前端轮询进度
- 阶段进度：解析 → 粗筛 → 精排 → 语义 → 聚合 → 完成
- 文件只在检测期间落盘临时目录，检测完即删（隐私最小化）
- 秒传：同文件 + 同比对库版本直接复用历史报告
规模化时把线程池换成 MQ + Worker 池即可，接口不变。
"""

from __future__ import annotations

import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .cli import DEFAULT_DB, DEFAULT_REPORTS, build_params_key, run_check
from .store import LibraryStore, sha256_of

app = FastAPI(title="论文查重服务", version="0.1.0")

_pool = ThreadPoolExecutor(max_workers=2)
_tasks: dict = {}


def _get_engine_ready(db: Path):
    store = LibraryStore(db)
    stats = store.stats()
    store.close()
    return stats


@app.get("/", response_class=HTMLResponse)
def index_page():
    return UPLOAD_PAGE


@app.get("/api/stats")
def stats():
    return _get_engine_ready(DEFAULT_DB)


@app.post("/api/check")
async def submit(file: UploadFile = File(...), use_semantic: bool = Form(True)):
    if not file.filename or not file.filename.lower().endswith((".pdf", ".docx", ".txt", ".md")):
        raise HTTPException(400, "仅支持 PDF / DOCX / TXT / MD")
    task_id = uuid.uuid4().hex[:12]
    raw = await file.read()
    _tasks[task_id] = {"status": "queued", "stage": "排队中", "pct": 0,
                       "message": "", "filename": file.filename}

    def work():
        tmp = None
        try:
            _tasks[task_id].update(status="running", stage="解析", pct=5, message="解析文档")
            with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix,
                                             delete=False) as tf:
                tf.write(raw)
                tmp = Path(tf.name)
            summary = run_check(DEFAULT_DB, DEFAULT_REPORTS, tmp,
                                use_semantic=use_semantic, verbose=False)
            _tasks[task_id].update(
                status="done", stage="完成", pct=100, message="检测完成",
                report_no=summary["report_no"], report_path=summary["html_path"],
                total_ratio=summary["total_ratio"],
                single_max=summary["single_max_ratio"],
                sources=summary.get("sources", [])[:5], cached=summary.get("cached", False))
        except Exception as e:  # 单任务失败不影响服务
            _tasks[task_id].update(status="error", stage="失败", pct=100,
                                   message=f"{type(e).__name__}: {e}")
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)  # 送检文件即用即删

    _pool.submit(work)
    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
def task_status(task_id: str):
    t = _tasks.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return {k: v for k, v in t.items() if k != "report_path"}


@app.get("/api/report/{task_id}", response_class=HTMLResponse)
def report(task_id: str):
    t = _tasks.get(task_id)
    if not t or t.get("status") != "done":
        raise HTTPException(404, "报告不存在或未完成")
    return FileResponse(t["report_path"], media_type="text/html")


UPLOAD_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>论文查重</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;background:#f5f6f8;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:34px;width:min(560px,92vw)}
h1{font-size:20px;margin:0 0 6px}.sub{color:#7f8c8d;font-size:13px;margin-bottom:18px}
#drop{border:2px dashed #b2bec3;border-radius:10px;padding:38px;text-align:center;color:#636e72;cursor:pointer;transition:.2s}
#drop.over{border-color:#0984e3;background:#e7f2fd}
label{font-size:13px;color:#555;display:block;margin:14px 0 6px}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:8px;background:#0984e3;color:#fff;font-size:15px;cursor:pointer}
button:disabled{background:#b2bec3}
#bar{height:10px;background:#ecf0f1;border-radius:5px;overflow:hidden;margin-top:16px;display:none}
#fill{height:10px;background:#0984e3;width:0;transition:width .4s}
#msg{font-size:13px;color:#636e72;margin-top:8px;min-height:18px}
#a{display:none;margin-top:14px;text-align:center}
a{color:#0984e3}
.stale{color:#e17055;font-size:12px;margin-top:10px}
</style></head><body><div class="box">
<h1>论文查重</h1><div class="sub">本地引擎 · 检测后不入库 · 支持条形图进度与对照报告</div>
<div id="drop">拖拽 PDF / DOCX / TXT 到这里，或点击选择文件</div>
<input id="f" type="file" accept=".pdf,.docx,.txt,.md" hidden>
<label><input type="checkbox" id="sem" checked> 启用语义通道（改写检测）</label>
<button id="go" disabled>开始检测</button>
<div id="bar"><div id="fill"></div></div><div id="msg"></div>
<div id="a"><a target="_blank">打开查重报告 →</a></div>
<div class="stale">说明：文件仅用于本次检测，检测完成后即从临时目录删除，不会进入比对库。</div>
</div>
<script>
const drop=document.getElementById('drop'),f=document.getElementById('f'),
go=document.getElementById('go'),bar=document.getElementById('bar'),
fill=document.getElementById('fill'),msg=document.getElementById('msg'),a=document.getElementById('a');
let file=null;
drop.onclick=()=>f.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};
drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files[0])pick(e.dataTransfer.files[0])};
f.onchange=()=>f.files[0]&&pick(f.files[0]);
function pick(x){file=x;drop.textContent='已选择：'+x.name;go.disabled=false;bar.style.display='none';a.style.display='none';msg.textContent=''}
go.onclick=async()=>{
  go.disabled=true;
  const fd=new FormData();fd.append('file',file);fd.append('use_semantic',document.getElementById('sem').checked);
  msg.textContent='上传中…';
  const r=await fetch('/api/check',{method:'POST',body:fd});const j=await r.json();
  if(!r.ok){msg.textContent=j.detail||'提交失败';go.disabled=false;return}
  bar.style.display='block';msg.textContent='排队中…';
  const t=setInterval(async()=>{
    const s=await (await fetch('/api/task/'+j.task_id)).json();
    fill.style.width=s.pct+'%';msg.textContent=s.stage+'：'+s.message;
    if(s.status==='done'){clearInterval(t);msg.textContent='完成！总相似比 '+(s.total_ratio*100).toFixed(1)+'%，单篇最大来源 '+(s.single_max*100).toFixed(1)+'%'+(s.cached?'（秒传）':'');
      a.style.display='block';a.querySelector('a').href='/api/report/'+j.task_id;go.disabled=false}
    if(s.status==='error'){clearInterval(t);msg.textContent='失败：'+s.message;go.disabled=false}
  },800);
};
</script></body></html>"""
