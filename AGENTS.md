# AGENTS.md — AI 协作规范（paper-grader）

本项目已接入完整工程化规范（pre-commit 钩子 / ruff / mypy / gitleaks / CI）。AI agent 在本仓库工作时遵守以下约定。

## 项目概要
学术论文自动批改工具（Python）：`paper_grader/`（LLM 逐维度批改 + Excel 成绩汇总）、`paper_check/`（本地查重引擎）、`scripts/`、`tests/`。

## 常用命令
```bash
uvx ruff check .                 # lint（应保持 0 问题）
uvx ruff format --check .        # 格式校验
uvx mypy .                       # 类型检查（宽松档，已入 CI，保持 0 错误）
uv run --with-requirements requirements.txt --with pytest --with pytest-cov \
  pytest tests/ -q               # 测试（11 个用例，必须全绿）
```

## 提交规范
- 提交信息：Conventional Commits —— `<type>(<scope>)?: <subject>`，type 取 feat/fix/docs/test/refactor/chore/ci 等，小写祈使句。
- 本地 pre-commit 钩子会自动跑 ruff 与 gitleaks：若提示 files were modified，`git add -u` 后重新提交；**禁止 --no-verify**。
- lint 级修复与功能改动分开提交。

## 行为红线
- API Key 只走环境变量（`PAPER_GRADER_API_KEY`）或本地 config，绝不写进代码/配置文件——gitleaks 会拦截。
- `E501` 已全局豁免：中文注释与长提示词的超长行**不要手工折行**，避免无意义 diff。
- 语料类 `.txt` 不做行尾空白清理（钩子已排除），不要改动语料内容。
- 测试写在 `tests/`，风格与现有断言式写法保持一致；新功能须带测试。
