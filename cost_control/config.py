"""配置读取辅助。

提供模块级的 ``get_config`` / ``get_pricing`` 函数与 ``CONFIG_DEFAULTS`` 字典，
供各 Mixin 统一读取 ``self.cfg``（merged 配置）。内置默认单价表 ``DEFAULT_PRICING``
定义在 :mod:`cost_control.default_pricing`（便于单独更新），此处 re-export 以保持
``cost_control.config.DEFAULT_PRICING`` 访问路径不变。
不做成 Mixin，避免污染 ``Main`` 的继承链。

由于 AstrBot 重载时会裁剪 ``_conf_schema.json`` 之外的配置键（``check_config_integrity``），
本插件的**详细配置**（budgets / pricing / budget_overrides / 各模块阈值等）存于
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
    # 5 维全局默认预算（int token 上限）。
    "budgets": {
        "per_session_daily": 0,
        "per_user_daily": 0,
        "per_model_daily": 0,
        "global_daily": 0,
        "global_monthly": 0,
    },
    # 5 维全局默认花费预算（float USD）。
    "budgets_cost": {
        "per_session_daily": 0.0,
        "per_user_daily": 0.0,
        "per_model_daily": 0.0,
        "global_daily": 0.0,
        "global_monthly": 0.0,
    },
    "pricing": {},  # 用户自定义定价，key=provider_id，value 按 mode（见 get_pricing）
    # 局部阈值（每条规则挂自己的 on_exceeded；优先级高于全局 5 维）。
    # 规则对象形如：
    #   {"enabled": bool, "target_type": "umo"|"provider"|"user",
    #    "target_value": str, "token_limit": int (0=不限),
    #    "cost_limit": float (0=不限),
    #    "on_exceeded": "stop"|"fallback"|"warn",
    #    "stop_message": str,
    #    "fallback_provider_ids": [str, ...],
    #    "fallback_token_limit": int}
    "budget_overrides": [],
    # 备用 Provider 库（Panel 3）：id 与 on_exceeded=fallback 的规则共享。
    # 形如：[{"id": "prov_x", "enabled": bool, "note": str}]
    "fallback_providers": [],
    # 全局默认超限处理（当 override 未命中且全局 5 维超限时生效）。
    # "stop" / "fallback" / "warn"。
    "default_on_exceeded": "stop",
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


# 合法 target_type 白名单。
_VALID_TARGET_TYPES = ("umo", "provider", "user")
# 合法 on_exceeded 白名单。
_VALID_ON_EXCEEDED = ("stop", "fallback", "warn")
# 合法 default_on_exceeded 同 on_exceeded（复用同一组值）。


def normalize_budget_override(raw: Any) -> dict[str, Any] | None:
    """规范化单条局部阈值规则（纯函数；非法值一律兜底；空规则返回 None）。

    Returns:
        规范化后的 dict；非法 / 缺字段不可恢复时返回 ``None``（调用方应丢弃该条）。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    target_type = str(raw.get("target_type") or "").strip().lower()
    if target_type not in _VALID_TARGET_TYPES:
        return None
    target_value = str(raw.get("target_value") or "").strip()
    if not target_value:
        return None
    try:
        token_limit = max(0, int(raw.get("token_limit", 0) or 0))
    except (TypeError, ValueError):
        token_limit = 0
    try:
        cost_limit = max(0.0, float(raw.get("cost_limit", 0) or 0))
    except (TypeError, ValueError):
        cost_limit = 0.0
    on_exceeded = str(raw.get("on_exceeded") or "").strip().lower()
    if on_exceeded not in _VALID_ON_EXCEEDED:
        on_exceeded = "stop"
    stop_message = str(raw.get("stop_message", "") or "")
    pids_raw = raw.get("fallback_provider_ids") or []
    pids: list[str] = []
    if isinstance(pids_raw, str):
        pids = [p.strip() for p in pids_raw.split(",") if p.strip()]
    elif isinstance(pids_raw, (list, tuple)):
        for p in pids_raw:
            s = str(p).strip()
            if s:
                pids.append(s)
    try:
        fallback_token_limit = max(0, int(raw.get("fallback_token_limit", 0) or 0))
    except (TypeError, ValueError):
        fallback_token_limit = 0
    enabled = bool(raw.get("enabled", True))
    return {
        "enabled": enabled,
        "target_type": target_type,
        "target_value": target_value,
        "token_limit": token_limit,
        "cost_limit": cost_limit,
        "on_exceeded": on_exceeded,
        "stop_message": stop_message,
        "fallback_provider_ids": pids,
        "fallback_token_limit": fallback_token_limit,
    }


def normalize_fallback_provider(raw: Any) -> dict[str, Any] | None:
    """规范化备用 Provider 库条目（纯函数；非法值返回 None 丢弃）。"""
    if not isinstance(raw, dict) or not raw:
        return None
    pid = str(raw.get("id") or "").strip()
    if not pid:
        return None
    note = str(raw.get("note", "") or "")
    enabled = bool(raw.get("enabled", True))
    return {"id": pid, "enabled": enabled, "note": note}


def enabled_overrides(overrides: Any) -> list[dict[str, Any]]:
    """返回已启用且字段合法的 override（保持原顺序，纯函数）。

    每条过 :func:`normalize_budget_override`（字段齐全）；返回 ``None`` 者跳过。
    """
    if not isinstance(overrides, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for ov in overrides:
        n = normalize_budget_override(ov)
        if n is not None and n.get("enabled", True):
            out.append(n)
    return out


def enabled_fallback_providers(items: Any) -> list[dict[str, Any]]:
    """返回已启用的备用 Provider（纯函数）。"""
    if not isinstance(items, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        n = normalize_fallback_provider(it)
        if n is not None and n.get("enabled", True):
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


def get_pricing(config: dict[str, Any] | None) -> dict[str, Any]:
    """返回生效的定价结构：``{"defaults": {model: {...}}, "user": {provider_id: {...}}}``。

    两套 key 空间并存、互不合并：

    - ``defaults``：内置出厂默认表 ``DEFAULT_PRICING``（key=模型名，隐含 per_token），
      由 :func:`cost_control.cost.match_pricing` 按模型名模糊匹配。
    - ``user``：用户在 ``pricing`` 项配置的覆盖（key=**provider_id**）。每条按
      ``mode``（``per_token``/``per_turn``/``per_request``）规范化；缺 ``mode`` 视为
      ``per_token``（兼容旧结构）。优先级高于 defaults——见
      :func:`cost_control.cost.resolve_pricing`。

    Args:
        config: 插件配置字典。

    Returns:
        ``{"defaults": {model: {input,...}}, "user": {provider_id: {mode,...}}}``。
        user entry 形如：
        - ``{"mode":"per_token","input":f,"input_cached":f,"output":f,"cache_creation":f|None}``
        - ``{"mode":"per_turn","price":f}`` / ``{"mode":"per_request","price":f}``
    """
    defaults: dict[str, dict[str, float]] = {
        model: dict(prices) for model, prices in DEFAULT_PRICING.items()
    }
    user: dict[str, dict[str, Any]] = {}
    user_pricing = get_config(config, "pricing", {}) or {}
    if isinstance(user_pricing, dict):
        for pid, entry in user_pricing.items():
            norm = _normalize_user_entry(entry)
            if norm is not None:
                user[str(pid)] = norm
    return {"defaults": defaults, "user": user}


def _normalize_user_entry(entry: Any) -> dict[str, Any] | None:
    """规范化一条用户定价 entry（key=provider_id 的 value）。非法返回 ``None``。

    识别 ``mode``（缺省 per_token），按 mode 校验数值：
    - per_token：input/input_cached/output（float>=0，缺 0.0）、cache_creation（float>=0 或 None）。
    - per_turn/per_request：price（float>=0）。
    非法字段回退 0.0 / None；mode 非法或 entry 非 dict 返回 None。
    """
    if not isinstance(entry, dict):
        return None
    mode = str(entry.get("mode") or "per_token").strip().lower()
    if mode not in ("per_token", "per_turn", "per_request"):
        return None
    if mode == "per_token":
        out: dict[str, Any] = {"mode": "per_token"}
        for f in ("input", "input_cached", "output"):
            out[f] = _to_float_or_zero(entry.get(f))
        cc = entry.get("cache_creation")
        if cc is None:
            out["cache_creation"] = None
        else:
            out["cache_creation"] = _to_float_or_zero(cc)
        return out
    # per_turn / per_request
    return {"mode": mode, "price": _to_float_or_zero(entry.get("price"))}


def _to_float_or_zero(v: Any) -> float:
    try:
        f = float(v)
        return f if f >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


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
