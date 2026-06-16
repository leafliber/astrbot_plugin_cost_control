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
    # 超限处理策略链（有序列表）：超限时按序求值。fallback_provider 按
    # provider_ids 列表逐个尝试备用 Provider；stop_llm 硬拦截。详见 budget.py。
    "over_limit_strategies": [
        {"action": "stop_llm", "message": "", "enabled": True},
    ],
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


# 超限策略合法动作白名单。
_VALID_STRATEGY_ACTIONS = ("fallback_provider", "stop_llm")


def normalize_strategy(raw: Any) -> dict[str, Any]:
    """规范化单条超限策略（纯函数，非法值一律兜底，绝不抛异常）。

    返回形如 ``{"action","provider_ids","token_limit","message","enabled"}`` 的 dict。
    非法 / 缺失 action → ``"stop_llm"``；``provider_ids`` 容错为 ``list[str]``
    （接受单字符串、逗号分隔串、列表）。

    Args:
        raw: 原始策略对象（通常来自用户配置或前端）。

    Returns:
        规范化后的策略 dict。
    """
    if not isinstance(raw, dict):
        return {
            "action": "stop_llm",
            "provider_ids": [],
            "token_limit": 0,
            "message": "",
            "enabled": True,
        }
    action = str(raw.get("action") or "").strip()
    if action not in _VALID_STRATEGY_ACTIONS:
        action = "stop_llm"
    pids_raw = raw.get("provider_ids")
    pids: list[str] = []
    if isinstance(pids_raw, str):
        pids = [p.strip() for p in pids_raw.split(",") if p.strip()]
    elif isinstance(pids_raw, (list, tuple)):
        for p in pids_raw:
            s = str(p).strip()
            if s:
                pids.append(s)
    try:
        token_limit = max(0, int(raw.get("token_limit", 0) or 0))
    except (TypeError, ValueError):
        token_limit = 0
    message = str(raw.get("message", "") or "")
    enabled = bool(raw.get("enabled", True))
    return {
        "action": action,
        "provider_ids": pids,
        "token_limit": token_limit,
        "message": message,
        "enabled": enabled,
    }


def migrate_legacy_policy(policy: Any) -> list[dict[str, Any]]:
    """把遗留的单对象 ``over_limit_policy`` 迁移为 1 元素策略列表（纯函数）。

    ``action=="fallback_provider"`` 时保留原 ``fallback_provider_id`` →
    ``provider_ids``、``fallback_token_limit`` → ``token_limit``；其余（含
    ``stop_llm``）迁移为单条 ``stop_llm`` 策略。空 / 非对象返回 ``[]``。
    """
    if not isinstance(policy, dict) or not policy:
        return []
    action = str(policy.get("action") or "").strip()
    if action == "fallback_provider":
        pid = str(policy.get("fallback_provider_id", "") or "").strip()
        try:
            tl = max(0, int(policy.get("fallback_token_limit", 0) or 0))
        except (TypeError, ValueError):
            tl = 0
        return [
            {
                "action": "fallback_provider",
                "provider_ids": [pid] if pid else [],
                "token_limit": tl,
                "message": "",
                "enabled": True,
            }
        ]
    return [
        {
            "action": "stop_llm",
            "provider_ids": [],
            "token_limit": 0,
            "message": "",
            "enabled": True,
        }
    ]


def enabled_strategies(strategies: Any) -> list[dict[str, Any]]:
    """返回已启用且 action 合法的策略（保持原顺序，纯函数）。

    输入先逐条过 :func:`normalize_strategy`（保证字段齐全 / 合法），
    再过滤 ``enabled`` 为假者。
    """
    if not isinstance(strategies, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for s in strategies:
        n = normalize_strategy(s)
        if n["enabled"] and n["action"] in _VALID_STRATEGY_ACTIONS:
            out.append(n)
    return out


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
