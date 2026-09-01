"""大模型客户端：任意 OpenAI 兼容接口（智谱 GLM / DeepSeek / OpenAI / Ollama…）。

- 带指数退避重试（429 / 5xx / 超时）
- 稳健的 JSON 解析（容忍 ```json 围栏、前后缀文字）
- token 用量累计，批改结束汇报成本
"""

from __future__ import annotations

import json
import re
import time

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


def parse_json_loose(text: str) -> dict:
    """从 LLM 回复中尽力提取 JSON 对象。"""
    text = text.strip()
    # 去掉 markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：截取最外层大括号
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"无法从模型回复中解析 JSON：{text[:200]}")


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.api_key or "localhost" in self.cfg.base_url or "127.0.0.1" in self.cfg.base_url)

    def chat(self, system: str, user: str) -> str:
        if not self.cfg.api_key:
            raise LLMError(
                "未配置 API Key。请设置环境变量 PAPER_GRADER_API_KEY，"
                "或写入 config.yaml 的 llm.api_key；也可先用 --mock 模式试跑。"
            )
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"

        last_err: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                with httpx.Client(timeout=self.cfg.timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    self.usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    self.usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    self.usage["requests"] += 1
                    return data["choices"][0]["message"]["content"]
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
            wait = 5 * attempt
            time.sleep(wait)
        raise LLMError(f"调用模型失败（已重试 {self.cfg.max_retries} 次）：{last_err}")

    def chat_json(self, system: str, user: str) -> dict:
        return parse_json_loose(self.chat(system, user))
