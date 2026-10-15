"""大模型客户端：任意 OpenAI 兼容接口（智谱 GLM / DeepSeek / OpenAI / Ollama…）。

- 带指数退避重试（429 / 5xx / 超时）
- 稳健的 JSON 解析（容忍 ```json 围栏、前后缀文字）
- token 用量累计，批改结束汇报成本
"""

from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import urlparse

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_endpoint(base_url: str) -> bool:
    """按 URL 的 hostname 精确判断是否本机端点（Ollama 等本地模型无需 API Key）。

    不能用子串匹配：如 https://localhost.evil.com 或 ?x=127.0.0.1 会被误判为本地。
    """
    try:
        host = urlparse(base_url.strip()).hostname
    except ValueError:
        return False
    return (host or "").lower() in LOCAL_HOSTS


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
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        """复用连接池的 HTTP 客户端（批量批改时减少握手开销）。"""
        if self._client is None:
            self._client = httpx.Client(timeout=self.cfg.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def is_local(self) -> bool:
        return is_local_endpoint(self.cfg.base_url)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.api_key) or self.is_local

    def chat(self, system: str, user: str) -> str:
        if not self.cfg.api_key and not self.is_local:
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
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:  # 本地端点（Ollama 等）无 Key 时不发送 Authorization
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"

        last_err: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                client = self._http()
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
                    # 尊重服务端 Retry-After；否则指数退避 + 随机抖动
                    retry_after = resp.headers.get("Retry-After")
                else:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = e
                retry_after = None
            if retry_after and str(retry_after).strip().isdigit():
                wait = min(int(retry_after), 120)
            else:
                wait = min(60, 2**attempt) + random.uniform(0, 1)
            time.sleep(wait)
        raise LLMError(f"调用模型失败（已重试 {self.cfg.max_retries} 次）：{last_err}")

    def chat_json(self, system: str, user: str) -> dict:
        return parse_json_loose(self.chat(system, user))
