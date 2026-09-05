"""论文查重模块（paper_check）——三级漏斗本地查重引擎。

流程：指纹粗筛（SimHash + LSH 分块倒排）→ 编辑相似度精排 →
语义通道兜底 → 片段归并 → 对照式 HTML 报告。
详见模块 README 与各文件 docstring。
"""

__version__ = "0.1.0"
