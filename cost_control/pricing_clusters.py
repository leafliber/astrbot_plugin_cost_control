"""AstrBot Provider Source 定价聚类与倍率规范化。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def normalize_pricing_multipliers(raw: Any) -> dict[str, float]:
    """规范化 ``{provider_source_id: multiplier}``。

    AstrBot 的 Provider Source ID 是用户可配置字符串，不能限制为插件内置白名单。
    这里只校验 ID 非空、长度合理，以及倍率处于 0–100；0 表示该分组计零成本，
    1 倍无需持久化。
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        source_id = str(key or "").strip()
        if not source_id or len(source_id) > 256:
            continue
        try:
            multiplier = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(multiplier) or multiplier < 0 or multiplier > 100:
            continue
        if abs(multiplier - 1.0) > 1e-12:
            out[source_id] = multiplier
    return out


def provider_cluster_map_from_config(config: Any) -> dict[str, str]:
    """从 AstrBot 主配置生成 ``provider_id → provider_source_id`` 精确映射。

    旧配置或独立 Provider 没有 ``provider_source_id`` 时，以 Provider ID 自身作为
    聚类 ID，确保它仍可单独设置倍率，且不会误归入其它供应商。
    """
    if not isinstance(config, Mapping):
        return {}
    providers = config.get("provider")
    if not isinstance(providers, list):
        return {}
    out: dict[str, str] = {}
    for item in providers:
        if not isinstance(item, Mapping):
            continue
        provider_id = str(item.get("id") or "").strip()
        if not provider_id:
            continue
        source_id = str(item.get("provider_source_id") or "").strip()
        out[provider_id] = source_id or provider_id
    return out


def build_provider_pricing_clusters(
    provider_models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 AstrBot Provider Source 聚合当前已有 Provider/模型，保持配置顺序。"""
    grouped: dict[str, dict[str, Any]] = {}
    for provider in provider_models:
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id:
            continue
        source_id = str(provider.get("supplier_id") or provider_id).strip() or provider_id
        source_name = str(provider.get("supplier_name") or source_id).strip() or source_id
        cluster = grouped.setdefault(
            source_id,
            {"id": source_id, "name": source_name, "provider_ids": []},
        )
        cluster["provider_ids"].append(provider_id)
    return list(grouped.values())
