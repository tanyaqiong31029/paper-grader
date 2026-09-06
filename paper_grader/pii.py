"""PII 脱敏：发送给外部模型服务前可选移除常见个人标识信息。

覆盖：邮箱、手机号（含 +86）、座机、连续学号/工号、身份证号。
正则为保守匹配（宁可漏、不可误伤正文），用于降低隐私泄露面，
不能替代人工检查。
"""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # 邮箱
    re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}"),  # 手机号
    re.compile(r"\b\d{3}-\d{4}-\d{4}\b"),  # 座机分段
    re.compile(r"\b\d{17}[\dXx]\b"),  # 身份证号
    re.compile(r"(?:学号|工号|学籍号)[:：\s]*[A-Za-z0-9]{6,20}"),  # 学号/工号
]

_REPLACEMENT = "[已脱敏]"


def redact_pii(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub(_REPLACEMENT, text)
    return text
