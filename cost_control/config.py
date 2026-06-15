"""配置读取辅助。

提供模块级的 ``get_config`` / ``get_pricing`` 函数与 ``CONFIG_DEFAULTS``、
``DEFAULT_PRICING`` 字典，供各 Mixin 统一读取 ``self.config``。不做成 Mixin，
避免污染 ``Main`` 的继承链。

阶段 1：默认值结构 + 定价表 + 读取函数。
"""

from __future__ import annotations

from typing import Any

# 内置常见模型的默认定价（USD / 百万 token）。
# 字段含义：
#   input          —— 非缓存输入 token（对应 ProviderStat.token_input_other）
#   input_cached   —— 缓存命中输入 token（对应 ProviderStat.token_input_cached）
#   output         —— 输出 token（对应 ProviderStat.token_output）
#   cache_creation —— 缓存写入 token（Anthropic 原生字段，从 raw_completion 解析）
# 价格随 provider 政策变动，用户可在 _conf_schema.json 的 pricing 项覆盖或新增。
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {
        "input": 3.0,
        "input_cached": 0.3,
        "output": 15.0,
        "cache_creation": 3.75,
    },
    "claude-sonnet-4-5-20250929": {
        "input": 3.0,
        "input_cached": 0.3,
        "output": 15.0,
        "cache_creation": 3.75,
    },
    "claude-opus-4": {
        "input": 15.0,
        "input_cached": 1.5,
        "output": 75.0,
        "cache_creation": 18.75,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "input_cached": 0.1,
        "output": 5.0,
        "cache_creation": 1.25,
    },
    "claude-3-5-sonnet": {
        "input": 3.0,
        "input_cached": 0.3,
        "output": 15.0,
        "cache_creation": 3.75,
    },
    "gpt-4o": {"input": 2.5, "input_cached": 1.25, "output": 10.0, "cache_creation": 2.5},
    "gpt-4o-mini": {"input": 0.15, "input_cached": 0.075, "output": 0.6, "cache_creation": 0.15},
    "gpt-4.1": {"input": 2.0, "input_cached": 0.5, "output": 8.0, "cache_creation": 2.0},
    "gpt-4.1-mini": {"input": 0.4, "input_cached": 0.1, "output": 1.6, "cache_creation": 0.4},
    "deepseek-chat": {
        "input": 0.27,
        "input_cached": 0.07,
        "output": 1.1,
        "cache_creation": 0.27,
    },
    "deepseek-reasoner": {
        "input": 0.55,
        "input_cached": 0.14,
        "output": 2.19,
        "cache_creation": 0.55,
    },
    "gemini-2.5-pro": {
        "input": 1.25,
        "input_cached": 0.31,
        "output": 10.0,
        "cache_creation": 1.25,
    },
    "gemini-2.5-flash": {
        "input": 0.3,
        "input_cached": 0.075,
        "output": 2.5,
        "cache_creation": 0.3,
    },
}

# 配置默认值。键名与 ``_conf_schema.json`` 的顶层项一一对应。
# object 类型的配置项用嵌套 dict 表示默认值。
CONFIG_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "platforms": [],
    "budgets": {
        "per_session_daily": 0,
        "per_user_daily": 0,
        "per_model_daily": 0,
        "global_daily": 0,
        "global_monthly": 0,
    },
    "pricing": {},  # 用户自定义覆盖 DEFAULT_PRICING；为空时只用默认表
    "over_limit_policy": {
        "action": "stop_llm",
        "fallback_provider_id": "",
        "fallback_token_limit": 0,
        "block_wake_words_after_limit": False,
    },
    "refresh_time": "00:00",
    "match_unique_session": False,
    "cache_diag": {
        "detect_context_reset": True,
        "detect_system_prompt_change": True,
        "detect_tools_change": True,
        "detect_order_drift": True,
        "cache_hit_rate_alert_threshold": 0,
    },
    "alerts": {
        "enabled": True,
        "cooldown_seconds": 300,
        "daily_report_time": "09:00",
        "daily_report_to": [],
    },
    "prompt_optimizer": {
        "enabled": True,
        "provider_id": "",
        "max_static_analysis_length": 8000,
    },
    "attribution": {
        "enabled": True,
        "sample_rate": 100,
    },
    "schedule": {
        "enable_daily_report": False,
        "retain_days": 90,
    },
}


def get_config(config: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """从插件配置字典读取指定键的值。

    若 ``config`` 为 None 或缺少该键，则回退到 ``CONFIG_DEFAULTS[key]``，
    若默认值也不存在则返回传入的 ``default``。

    Args:
        config: 插件配置字典（通常是 ``Main.config``）。
        key: 顶层配置项键名，与 ``_conf_schema.json`` 一致。
        default: 当 config 与 CONFIG_DEFAULTS 都缺失时返回的兜底值。

    Returns:
        配置值。
    """
    if config and key in config:
        return config[key]
    return CONFIG_DEFAULTS.get(key, default)


def get_pricing(config: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """返回生效的模型单价表（默认表 + 用户配置覆盖）。

    用户在 ``pricing`` 项配置的模型会与 ``DEFAULT_PRICING`` 合并：同名模型按字段
    覆盖（用户填的字段生效，未填的字段保留默认）；用户新增的模型直接加入。

    Args:
        config: 插件配置字典。

    Returns:
        形如 ``{"gpt-4o": {"input": 2.5, "input_cached": 1.25, "output": 10.0,
        "cache_creation": 2.5}}`` 的字典（USD / 百万 token）。
    """
    merged: dict[str, dict[str, float]] = {
        model: dict(prices) for model, prices in DEFAULT_PRICING.items()
    }
    user_pricing = get_config(config, "pricing", {}) or {}
    if isinstance(user_pricing, dict):
        for model, prices in user_pricing.items():
            if not isinstance(prices, dict):
                continue
            base = dict(merged.get(model, {}))
            for field, value in prices.items():
                if value is None:
                    continue
                try:
                    base[field] = float(value)
                except (TypeError, ValueError):
                    continue
            merged[model] = base
    return merged
