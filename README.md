# 论文自动批改工具（Paper Grader）

面向教师的**中文学术论文 AI 辅助批量批改**命令行工具：把一个文件夹里的论文（期刊投稿、研究生课程论文、学位/毕业论文）批量交给大模型，按可插拔的评分量规逐维度打分，产出**单篇批改报告（Markdown）+ 全批成绩汇总（Excel）**。

> ⚠️ 定位声明：AI 评分为**辅助**而非替代。每个分数都附带原文证据引用与置信度，低置信度论文自动标记"需人工复评"，最终成绩由教师核定。

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
python3 tests/test_pipeline.py   # 21 项断言：JSON 解析、加权计算、分块路径、失败处理、缓存签名
python3 -m paper_grader grade samples --mock   # 端到端试跑
```

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
