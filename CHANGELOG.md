# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased] - 2026-09-06

### 修复
- 查重报告：内嵌 JSON 转义 `<`/`>`/`&`，证据面板对原文/来源名/章节做 HTML 转义（存储型 XSS）
- 本地模型（Ollama 等）无需 API Key、不发送 Authorization；本地端点按 URL hostname 精确判定
- `--mock` 短文 UnboundLocalError；维度分数容忍 "78分" 等带单位写法
- PII：身份证号先于手机号匹配，数字边界改用前后断言（"身份证号110…"可完整脱敏）
- 论文收集排除实际 `--out` 目录，"output" 仅按相对输入目录判断
- 缓存/报告文件名按相对路径唯一键，防止不同子目录同名论文互相覆盖；失败结果不再命中缓存，
  缓存签名纳入论文类型、量规内容哈希与脱敏开关
- paper_check：顶层 `--db/--reports` 不再被子命令默认值覆盖，`serve` 参数真正传入服务；
  上传页携带 X-Admin-Token；令牌比较改为 `hmac.compare_digest`
- API Key 优先级与文档一致：环境变量优先于 config.yaml

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
