"""配置读取辅助。

提供模块级的 ``get_config`` / ``get_pricing`` 函数与 ``CONFIG_DEFAULTS`` 字典，
供各 Mixin 统一读取 ``self.cfg``（merged 配置）。内置默认单价表 ``DEFAULT_PRICING``
定义在 :mod:`cost_control.default_pricing`（便于单独更新），此处 re-export 以保持
``cost_control.config.DEFAULT_PRICING`` 访问路径不变。
不做成 Mixin，避免污染 ``Main`` 的继承链。

由于 AstrBot 重载时会裁剪 ``_conf_schema.json`` 之外的配置键（``check_config_integrity``），
本插件的**详细配置**（budgets / pricing / over_limit_strategies / 各模块阈值等）存于
插件自有的 ``config.json``（data 目录，:func:`load_plugin_config` / :func:`save_plugin_config`），
运行时由 ``Main`` 合并为 ``self.cfg`` 供读取；``_conf_schema.json`` 仅保留各功能开关。

阶段 1：默认值结构 + 定价表 + 读取函数 + 插件自有配置文件读写。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .default_pricing import DEFAULT_PRICING

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
    # 花费预算（USD）：与 budgets(token) 同 5 维、独立生效。任一维度的 token 或
    # cost 超出即触发 over_limit_strategies。0.0 表示该维度不限花费。
    "budgets_cost": {
        "per_session_daily": 0.0,
        "per_user_daily": 0.0,
        "per_model_daily": 0.0,
        "global_daily": 0.0,
        "global_monthly": 0.0,
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


# schema 中保留的「功能开关」键（保存时分离开关与详细配置）。
# 顶层标量开关 + 各 object 内的 enable/detect 布尔（路径以 tuple 表示嵌套）。
SWITCH_KEYS: tuple[tuple[str | int, ...], ...] = (
    ("enabled",),
    ("platforms",),
    ("alerts", "enabled"),
    ("cache_diag", "detect_context_reset"),
    ("cache_diag", "detect_system_prompt_change"),
    ("cache_diag", "detect_tools_change"),
    ("cache_diag", "detect_order_drift"),
    ("prompt_optimizer", "enabled"),
    ("attribution", "enabled"),
    ("schedule", "enable_daily_report"),
)


def deep_merge(base: Any, *overrides: Any) -> Any:
    """递归合并多个 dict（后者覆盖前者；非 dict 值直接覆盖）。纯函数。

    用于把 ``CONFIG_DEFAULTS`` ⊕ 插件配置文件 ⊕ ``self.config``(开关) 合并为
    运行时 ``self.cfg``。``base`` / ``overrides`` 中非 dict 的项按整体替换处理。
    """
    if not isinstance(base, dict):
        # 以第一个 dict 为起点；若全非 dict，返回最后一个 override（或 base）。
        for ov in overrides:
            base = ov
        return base
    merged: dict[Any, Any] = dict(base)
    for ov in overrides:
        if not isinstance(ov, dict):
            # 非 dict 覆盖直接整体替换
            return ov
        for k, v in ov.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k] = deep_merge(merged[k], v)
            else:
                merged[k] = v
    return merged


def _config_path(data_dir: str) -> str:
    return os.path.join(data_dir, "config.json")


def load_plugin_config(data_dir: str) -> dict[str, Any]:
    """读取插件自有配置文件（``data_dir/config.json``）。失败 / 不存在返回 ``{}``。

    AstrBot 不触碰此文件（不受 schema 裁剪影响），是详细配置的持久来源。
    """
    try:
        path = _config_path(data_dir)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_plugin_config(data_dir: str, cfg: dict[str, Any]) -> None:
    """原子写插件自有配置文件（先写临时文件再 ``os.replace``，避免半写损坏）。"""
    path = _config_path(data_dir)
    tmp = path + ".tmp"
    os.makedirs(data_dir, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def switches_from_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """从 AstrBot 配置（``self.config``，仅含 schema 开关）抽取开关子集，供合并时覆盖。"""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for path in SWITCH_KEYS:
        # 沿路径取值；存在则写回 out 的同结构
        cur_src: Any = raw
        val: Any = _MISSING
        for seg in path:
            if isinstance(cur_src, dict) and seg in cur_src:
                cur_src = cur_src[seg]
                val = cur_src
            else:
                val = _MISSING
                break
        if val is not _MISSING:
            _set_path(out, path, val)
    return out


_MISSING: Any = object()


def _set_path(d: dict[str, Any], path: tuple[str | int, ...], val: Any) -> None:
    for seg in path[:-1]:
        d = d.setdefault(str(seg), {})  # type: ignore[assignment]
    d[str(path[-1])] = val


def coerce_to_default_type(value: Any, default: Any) -> Any:
    """按 ``default`` 的类型强转 ``value``（校验用户输入，纯函数）。

    bool/int/float/str/list 照型强转（数值 ``<0`` 归 0，非法回退默认）；dict 时若
    default 有固定子键则逐子键按其默认类型强转（缺失补默认），default 为空 dict
    （如 pricing）则接受任意 dict。用于保存配置前的类型校验。
    """
    # 注意：bool 是 int 子类，必须先判 bool。
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return int(default)
    if isinstance(default, float):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return float(default)
    if isinstance(default, str):
        return str(value)
    if isinstance(default, list):
        return list(value) if isinstance(value, (list, tuple)) else list(default)
    if isinstance(default, dict):
        if default:
            src = value if isinstance(value, dict) else {}
            return {k: coerce_to_default_type(src.get(k), dv) for k, dv in default.items()}
        return dict(value) if isinstance(value, dict) else {}
    return value
