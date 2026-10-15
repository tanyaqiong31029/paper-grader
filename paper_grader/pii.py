"""PII 脱敏：发送给外部模型服务前可选移除常见个人标识信息。

覆盖：邮箱、手机号（含 +86）、座机、连续学号/工号、身份证号。
正则为保守匹配（宁可漏、不可误伤正文），用于降低隐私泄露面，
不能替代人工检查。
"""

from __future__ import annotations

import re

# 顺序有意义：身份证号必须先于手机号匹配，否则手机号模式会吃掉身份证号中间的
# 11 位数字（如 110101199003071234 里的 19900307123）导致残留。
# 中文与数字同属 \w，`\b` 在“身份证号110…”之间不成立，因此用数字前后断言代替。
_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # 邮箱
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),  # 身份证号
    re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),  # 手机号
    re.compile(r"(?<!\d)\d{3}-\d{4}-\d{4}(?!\d)"),  # 座机分段
    re.compile(r"(?:学号|工号|学籍号)[:：\s]*[A-Za-z0-9]{6,20}"),  # 学号/工号
]

_REPLACEMENT = "[已脱敏]"


def redact_pii(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub(_REPLACEMENT, text)
    return text
