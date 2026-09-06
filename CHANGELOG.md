# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] - 2026-09-06

### 安全
- 补充 MIT LICENSE（含 samples/corpus 第三方内容许可说明）
- 外部 LLM 数据流同意机制：`--local-only` / `--confirm-remote` / 交互确认
- PII 脱敏选项（`--redact-pii`）：发送前移除邮箱/手机号/身份证号/学号
- 批改报告记录实际 provider（base_url + 模型）与脱敏状态
- paper_check 服务加固：上传体上限（10MB）、解析后文本上限、任务并发与
  数量上限（20）、任务 TTL（2h）、可选令牌鉴权（PAPER_CHECK_TOKEN）、
  报告响应 no-store、README 顶部原型声明
- LLM 客户端：复用 HTTP 连接池；重试尊重 Retry-After，指数退避 + 抖动

### 工程
- requirements 锁定精确版本；测试依赖分离至 requirements-dev.txt
- CI：Python 3.11/3.12/3.13 矩阵、覆盖率门槛 45%、上传覆盖率产物、
  新增 pip-audit 作业
- 新增 SECURITY.md / CONTRIBUTING.md / Bug 报告模板
