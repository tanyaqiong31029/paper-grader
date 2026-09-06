# 论文批改 + 查重工具集（Paper Grader & Checker）

> ⚠️ **原型声明**：paper_check 查重 Web 服务为单机原型（无认证、任务存内存），**不可直接暴露公网**；paper_grader 默认配置会把论文发送给外部模型服务，首次运行需显式确认。详见下方「隐私与数据流向」。

本仓库包含两个模块：

1. **paper_grader** —— 面向教师的**中文学术论文 AI 辅助批量批改**命令行工具：把一个文件夹里的论文（期刊投稿、研究生课程论文、学位/毕业论文）批量交给大模型，按可插拔的评分量规逐维度打分，产出**单篇批改报告（Markdown）+ 全批成绩汇总（Excel）**。
2. **paper_check** —— **本地查重引擎**（三级漏斗架构原型）：指纹粗筛 → 句级精排 → 语义兜底，产出逐句染色、来源可对照的 HTML 查重报告，支持异步任务 Web 服务。

> ⚠️ 定位声明：AI 评分为**辅助**而非替代。每个分数都附带原文证据引用与置信度，低置信度论文自动标记"需人工复评"，最终成绩由教师核定。查重结果同样定位为初筛参考，报告保留完整证据链供人工复核。

## 与 GitHub 同类项目的对比

| 参考项目 | 它的做法 | 本项目的借鉴与创新 |
|---|---|---|
| [Xiaochr/LLM-AES](https://github.com/Xiaochr/LLM-AES)（LAK'25） | 双过程框架：先逐特征分析再整体打分；置信度+解释辅助教师复评 | **借鉴**双过程架构（逐维度评分→综合定级）与置信度→"需人工复评"标记机制 |
| [jsu360/EssayJudge](https://github.com/jsu360/EssayJudge)（ACL'25） | 基于量规（rubric）的多粒度 trait 评分基准 | **借鉴** rubric-based 逐维度评分；创新点：量规完全可插拔（YAML 配置即可改权重、加维度） |
| [NirDiamant LangGraph 评分 agent](https://github.com/NirDiamant/GenAI_Agents/blob/main/all_agents_tutorials/essay_grading_system_langgraph.ipynb) | 每个评分维度一个独立节点，最后汇总 | **借鉴**逐维度独立调用（比一次性打总分更稳）；状态可恢复落地为 JSON 缓存 |
| [Eric-Terminal/Pro_llm_correct](https://github.com/Eric-Terminal/Pro_llm_correct) | 可插拔评分模板、run-id 任务追溯、并发+失败记录、token 统计 | **借鉴**失败清单、并发调度、token 用量统计、MD 报告 |
| [Dmoayad/essay-grader-llm](https://github.com/Dmoayad/essay-grader-llm) | FastAPI+Gradio 界面、RAG 对比同水平作文 | 暂未引入 UI/RAG（见路线图） |
| [binary-husky/gpt_academic](https://github.com/binary-husky/gpt_academic) | 插件化科研助手 | 定位不同：本项目专做"批量评分"，不做润色对话 |

**本项目的差异化（现有项目均未覆盖）：**

1. **面向中文学术论文**（期刊/研究生课程/学位论文三类量规），而非中小学作文或英文 essay；
2. **长文档处理**：几万字的学位论文自动走"分块要点提炼（map）→ 逐维度评分 → 综合定级（reduce）"两阶段，短文则一次性直评；
3. **教师工作台输出**：文件夹级批量处理 → Excel 成绩总表（含等级分布统计、按类型分表）+ 单篇评语报告，可直接用于登分；
4. **证据可复核**：每个维度分数必须附原文引用，教师可抽查有据可依；
5. **断点续批**：JSON 缓存按文件签名（大小+修改时间）+模型名校验，80 份批到一半中断后重跑自动跳过已批的；
6. **零成本试跑**：`--mock` 模式无需 API Key 即可走通全流程，验证文件名识别、量规配置与输出格式。

## 快速开始

```bash
# 1. 安装依赖（建议虚拟环境）
pip install -r requirements.txt

# 2. 免 API Key 试跑（用三篇样例论文走通全流程）
python3 -m paper_grader grade samples --mock

# 3. 正式批改：配置密钥后运行
export PAPER_GRADER_API_KEY=你的密钥        # 智谱/DeepSeek/OpenAI 均可
python3 -m paper_grader grade 收到的作业文件夹/ --type auto
```

`--type` 可选 `course`（课程论文）/ `journal`（期刊论文）/ `thesis`（学位论文）/ `auto`（按文件名关键词识别，默认）。识别规则与关键词在 `config.yaml` 的 `type_keywords` 中可改。

### 模型配置

`config.yaml` 中 `llm` 段默认使用智谱 GLM（`glm-4.6`）。任何 OpenAI 兼容接口都可切换，例如：

```yaml
llm:
  base_url: https://api.deepseek.com/v1   # 或 https://api.openai.com/v1
  model: deepseek-chat                    # 或 gpt-4o
```

本地模型（如 Ollama）把 `base_url` 改为 `http://localhost:11434/v1` 即可，论文内容不出本机。

## 输出说明

```
output/
├── 批改成绩汇总_20260902_1530.xlsx   # 成绩总表（按总分排序+等级分布统计）+ 每类论文的维度明细分表
├── 报告/批改报告__张三_课程论文.md    # 单篇报告：总分/等级/分维度评分+原文证据/总评/优缺点/修改建议
├── cache/张三_课程论文.json           # 断点续批缓存（文件更新或换模型后自动失效重批）
└── 失败清单_20260902_1530.csv        # 批改失败的文件与原因（单篇失败不影响整批）
```

常用参数：

```bash
python3 -m paper_grader grade 作业/ --out 成绩输出/   # 指定输出目录
python3 -m paper_grader grade 作业/ --force           # 忽略缓存全部重批
python3 -m paper_grader report --out 成绩输出/        # 只从缓存重新生成汇总 Excel
```

## 自定义评分量规

量规在 `config.yaml` 的 `rubrics` 段，纯 YAML 即可增删维度、调整权重（每套权重和须为 100）。例如把课程论文的"分析与论证"权重从 25 调到 30、新增"小组协作"维度，改完直接生效。`tests/` 中有量规校验（权重和、类型完整性）。

## 长论文处理

全文超过 `grading.max_fulltext_chars`（默认 24000 字）的论文（如学位论文）自动切换两阶段：

1. **map**：按 `chunk_chars`（默认 9000 字）分块，逐块提炼要点、亮点、问题、代表性原文；
2. **score**：每个维度独立评分，提示词包含结构提纲、摘要、全部分块要点与原文抽样；
3. **synthesize**：总分 = Σ(权重 × 维度分) 确定性加权（不交给模型拍脑袋），模型只撰写总评与优缺点建议。

## 测试

```bash
python3 tests/test_pipeline.py   # 批改：21 项断言（JSON 解析、加权、分块、失败处理、缓存）
python3 tests/test_check.py      # 查重：31 项断言（指纹/LSH/对齐/端到端/秒传/不入库）
python3 -m paper_grader grade samples --mock    # 批改端到端试跑
```

---

# paper_check：本地查重引擎

## 架构（三级漏斗）

```
送检论文 → 预处理(分句/章节识别/参考文献剔除)
         → ① 指纹粗筛：字符bigram SimHash + 4×16bit 分块倒排(LSH)
              全库 N 句 → 候选 K 句（鸽笼原理：汉明距离≤3必命中一个分块）
         → ② 编辑精排：候选句对归一化编辑距离 → 分级
              ≥0.80 复制 / 0.65-0.80 高度疑似 / 0.55-0.65 疑似
         → ③ 语义兜底：LSH 未召回的句子走语义通道
              - 未配置向量模型：bigram 包含系数 Top-3 召回（语料无关）
              - 配置后自动用 BGE 句向量：捕捉同义改写
              精度由编辑相似度闸门（≥0.60）把关，防误报
         → 归并片段 → 来源归属 → 章节热力 → HTML 对照报告
```

## 快速使用

```bash
# 1. 建设比对库（显式操作；支持 .pdf/.docx/.txt/.md）
python3 -m paper_check index 文献库文件夹/

# 2. 查重一篇（永远不入库——学生论文不会污染文献库或泄露给后续送检者）
python3 -m paper_check check 论文.docx
# → 总相似比、单篇最大来源、来源分布 + output/check/reports/ 下的对照报告

# 3. Web 服务（异步任务链：上传即返回任务ID → 阶段进度 → 报告）
python3 -m paper_check serve --port 8018
# → http://127.0.0.1:8018  拖拽上传，实时进度条，完成后在线查看报告
```

## 报告内容

总相似比与单篇最大来源、来源分布条形图、章节相似度热力、**逐句染色正文**（红=复制/橙=高度疑似/黄=疑似/蓝=改写疑似），点击染色句在右侧查看来源句对照与相似度；报告带唯一编号可验真。Web 服务另有"秒传"：同一文件在同一版比对库下重复提交直接复用历史报告。

## 隐私设计

- **check 永不入库**：送检文件仅在检测期间存在于临时目录，检测完即删；句指纹不写入文献库（有测试断言保障）
- 原始文献入库只存句子文本与指纹，支持按内容哈希去重
- 上传页明示"检测后不入库"；规模化部署时在此分层上叠加传输加密与对象存储加密

## 已知边界与规模化路径（对应优化方案）

| 原型现状 | 规模化路径 |
|---|---|
| SimHash 句级 LSH，内存倒排 | 桶结构迁移 Redis/ES；文档级指纹加 MinHash LSH |
| bigram 包含系数兜底，深度改写召回有限 | 接入 BGE/SimCSE 句向量（实现 `SemanticModel.embed` 接口即自动启用） |
| SQLite 存储句指纹 | 迁移 PostgreSQL + ES/向量库（Milvus） |
| 线程池任务执行（单机） | 换 Kafka/RabbitMQ + Worker 池，接口不变 |
| 无用户体系 | 接入网关鉴权 + RBAC + 报告签名令牌 |

阈值（相似度分级、语义闸门）按语料校准：均集中在 `align.py` / `semantic.py` / `engine.py` 顶部常量，改完跑 `python3 tests/test_check.py` 回归。

## 已知边界与路线图

- 暂不支持旧版 `.doc`（请学生交 `.docx` 或 PDF）；扫描版 PDF（图片页）无法提取文字，会在报告中给出警告。
- AI 分数适合做**排序与初筛**（快速定位需要重点关照的论文），不建议直接作为最终成绩登分——先用 `--mock` 校准量规，再小批量人工对照。
- 路线图：Web 界面（Gradio，参考 essay-grader-llm）、Word 修订模式批注（直接在学生稿上标注）、基于历史人工评分的校准（few-shot 参照，参考 EssayJudge RAG 思路）、查重粗筛。

## 项目结构

```
paper_grader/
├── cli.py        # 命令行入口：批量调度、缓存、失败清单
├── config.py     # 配置加载（config.yaml + 环境变量）
├── extract.py    # PDF/DOCX/TXT/MD 文本提取
├── rubric.py     # 量规加载与校验
├── llm.py        # OpenAI 兼容客户端（重试/JSON 解析/token 统计）
├── grader.py     # 批改编排：分块 map → 逐维度评分 → 综合定级
└── report.py     # Markdown 报告 + Excel 汇总 + 失败 CSV
config.yaml       # 量规、模型、批改参数（改这里即可定制）
samples/          # 三篇样例论文（course docx / thesis docx / journal pdf）
tests/            # 无需 API Key 的流水线测试
```

## 隐私与数据流向

**定位**：默认本地处理（查重在本地比对库上完成）；只有当你在 config.yaml 配置了
外部模型服务并显式确认后，论文内容才会离开本机。

| 场景 | 数据去向 | 触发条件 |
|---|---|---|
| paper_check 查重 | 不出本机（临时文件检测完即删） | 直接使用 |
| paper_grader + 本地 Ollama | 不出本机 | base_url 指向 localhost |
| paper_grader + 外部模型（默认智谱） | 正文分块发送给服务商（≤24,000 字符/篇） | `--confirm-remote` 或交互输入 yes |

- `--local-only`：强制仅本地端点，配置了外部服务时直接拒绝启动；
- `--redact-pii`：发送前脱敏邮箱/手机号/身份证号/学号；
- 批改报告会记录实际使用的 provider（base_url + 模型）与是否脱敏；
- 服务商的数据保留政策请自行查阅（如智谱：bigmodel.cn 的隐私条款）；
- paper_check 可用环境变量 `PAPER_CHECK_TOKEN` 启用令牌鉴权，
  `PAPER_CHECK_TASK_TTL` 控制任务保留时长（默认 2 小时）。


## 许可证

本项目采用 [MIT License](LICENSE)。

- `samples/` 与 `corpus/` 下的样例论文与语料均为本仓库脚本（`scripts/`）生成的**合成内容**，随项目同许可分发；
- README 中引用的第三方开源项目（LLM-AES、EssayJudge 等）版权归原作者所有，此处仅作方案致谢与出处标注；
- 外部模型服务（智谱/DeepSeek/OpenAI）各自的服务条款与数据政策独立于本许可证。
