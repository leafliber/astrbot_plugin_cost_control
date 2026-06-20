"""内置常见模型的默认定价（USD / 百万 token）。

由 ``scripts/sync_pricing.py`` 从 OpenRouter ``/api/v1/models`` 自动生成（最新快照）。
**请勿手改 OpenRouter 拉取的条目**——调整 ``scripts/sync_pricing.py`` 的 ``TARGETS``
后重跑 ``uv run python scripts/sync_pricing.py`` 即可刷新；``MANUAL`` 区为 OpenRouter
未覆盖的模型（如豆包），需人工维护。OpenRouter 未提供 cache 字段时按 input 价占位。

字段含义：
    input          —— 非缓存输入 token（对应 ``ProviderStat.token_input_other``）
    input_cached   —— 缓存命中输入 token（对应 ``ProviderStat.token_input_cached``）
    output         —— 输出 token（对应 ``ProviderStat.token_output``）
    cache_creation —— 缓存写入 token（Anthropic 原生字段；非 Anthropic 模型无此概念，
                      OpenRouter 未提供时按 input 价占位，保守不低估成本）

运行时由 :func:`cost_control.config.get_pricing` 合并：本表（出厂默认）⊕ 用户在
``pricing`` 配置项的自定义覆盖。模型名匹配走 :func:`cost_control.cost.match_pricing`
（精确 > 前缀 > 关键词模糊），故实际调用中的变体名（厂商前缀、版本 / 日期后缀、大小写
差异）通常能自动命中预设，无需逐个配置。
"""

from __future__ import annotations

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {
        "input": 3.0,
        "input_cached": 0.3,
        "output": 15.0,
        "cache_creation": 3.75,
    },
    "claude-opus-4": {"input": 15.0, "input_cached": 1.5, "output": 75.0, "cache_creation": 18.75},
    "claude-haiku-4-5": {"input": 1.0, "input_cached": 0.1, "output": 5.0, "cache_creation": 1.25},
    "gpt-4o": {"input": 2.5, "input_cached": 1.25, "output": 10.0, "cache_creation": 2.5},
    "gpt-4o-mini": {"input": 0.15, "input_cached": 0.075, "output": 0.6, "cache_creation": 0.15},
    "gpt-4.1": {"input": 2.0, "input_cached": 0.5, "output": 8.0, "cache_creation": 2.0},
    "gpt-4.1-mini": {"input": 0.4, "input_cached": 0.1, "output": 1.6, "cache_creation": 0.4},
    "gemini-2.5-pro": {
        "input": 1.25,
        "input_cached": 0.125,
        "output": 10.0,
        "cache_creation": 0.375,
    },
    "gemini-2.5-flash": {
        "input": 0.3,
        "input_cached": 0.03,
        "output": 2.5,
        "cache_creation": 0.083333,
    },
    "deepseek-chat": {
        "input": 0.2002,
        "input_cached": 0.2002,
        "output": 0.8001,
        "cache_creation": 0.2002,
    },
    "deepseek-reasoner": {"input": 0.7, "input_cached": 0.7, "output": 2.5, "cache_creation": 0.7},
    "glm-4.5": {"input": 0.6, "input_cached": 0.11, "output": 2.2, "cache_creation": 0.6},
    "glm-4.5-air": {"input": 0.13, "input_cached": 0.025, "output": 0.85, "cache_creation": 0.13},
    "glm-4.6": {"input": 0.43, "input_cached": 0.08, "output": 1.74, "cache_creation": 0.43},
    "glm-4.7": {"input": 0.4, "input_cached": 0.08, "output": 1.75, "cache_creation": 0.4},
    "qwen3-max": {"input": 0.78, "input_cached": 0.156, "output": 3.9, "cache_creation": 0.975},
    "qwen3.7-max": {"input": 1.25, "input_cached": 0.25, "output": 3.75, "cache_creation": 1.5625},
    "qwen3.6-plus": {
        "input": 0.325,
        "input_cached": 0.325,
        "output": 1.95,
        "cache_creation": 0.40625,
    },
    "qwen3.7-plus": {"input": 0.32, "input_cached": 0.064, "output": 1.28, "cache_creation": 0.4},
    "qwen3.6-flash": {
        "input": 0.1875,
        "input_cached": 0.1875,
        "output": 1.125,
        "cache_creation": 0.234375,
    },
    "kimi-k2": {"input": 0.57, "input_cached": 0.57, "output": 2.3, "cache_creation": 0.57},
    "kimi-k2.6": {"input": 0.67, "input_cached": 0.2, "output": 3.5, "cache_creation": 0.67},
    "doubao-pro-32k": {"input": 0.11, "input_cached": 0.11, "output": 0.28, "cache_creation": 0.11},
    "doubao-lite-32k": {
        "input": 0.04,
        "input_cached": 0.04,
        "output": 0.08,
        "cache_creation": 0.04,
    },
}
