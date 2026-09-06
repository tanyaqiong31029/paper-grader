# 参与贡献

感谢关注 paper-grader！这是一个以"本地部署、数据流向透明"为核心的自部署论文自查工具。
欢迎以下类型的贡献：

- 🐛 Bug 修复（请先附[缺陷报告](.github/ISSUE_TEMPLATE/bug_report.yml)中的复现信息）
- 🔒 安全加固（漏洞请走 [SECURITY.md](SECURITY.md) 的私人漏洞报告通道，不要开公开 Issue）
- 🧪 测试补充：查重口径、分句边界、恶意文档解析、AIGC 评测集扩充
- 🌍 AIGC 检测方法：欢迎按 `evals/` 的接口接入新引擎或更好的评测集
- 📝 文档与翻译（README_EN 与中文版需保持同步）

## 开发流程

```bash
bash setup.sh                                  # 安装 + 构建（npm ci 锁定依赖）
cd backend && python3 -m pytest tests/ -q      # 后端测试必须全绿
cd backend && python3 scripts/eval_aigc.py \
    --min-auroc 0.80 --max-fpr45 0.40          # AIGC 回归门槛
cd frontend && npm run build                   # 前端构建 + TS 严格检查
```

## 提交约定

- **小步提交**：一个提交只做一件事，标题用 `feat:/fix:/docs:/test:/chore:` 前缀；
  大改动建议先开 Issue 讨论方案，再走 PR
- 触碰检测口径（阈值、片段合并、重复率公式）的改动必须同步更新对应测试，
  并在 PR 描述中给出前后对比样本
- 触碰数据流向（新增对外请求）的改动必须同步更新 README「隐私与数据流向」
  表格与提交页提示，缺一不可
- 不接受把用户论文文本写入仓库或测试固件的 PR（评测集一律自建）

## 本地注意事项

- 数据目录 `data/` 已被 gitignore，请勿 `git add -f`
- 请勿提交任何真实论文、API Key 或令牌
