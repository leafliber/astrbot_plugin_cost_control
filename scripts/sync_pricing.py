#!/usr/bin/env python3
"""从 OpenRouter ``/api/v1/models`` 同步最新模型定价到 ``default_pricing.py``。

用法::

    uv run python scripts/sync_pricing.py

数据源是 OpenRouter 公开端点（无需 API key），其 ``pricing`` 字段以 USD / token
计，脚本换算为 USD / 百万 token。

- ``TARGETS``：插件 model key → OpenRouter slug 映射。key 取各厂商官方 API 常见形式，
  便于 :func:`cost_control.cost.match_pricing`（精确 > 前缀 > 关键词模糊）命中实际
  调用名。改这里即可增删模型。
- ``MANUAL``：OpenRouter 未覆盖的模型（人工维护，随厂商调价更新）。
- cache 字段（``input_cache_read`` / ``input_cache_write``）缺失时按 input 价占位
  （保守不低估成本）。

生成后建议运行::

    uv run ruff format cost_control/default_pricing.py
    uv run pytest tests/test_cost.py
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# 插件 model key → OpenRouter slug。
TARGETS: dict[str, str] = {
    # Anthropic（官方 API 用连字符版本号，如 claude-sonnet-4-5-20250929）
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4.5",
    "claude-opus-4": "anthropic/claude-opus-4",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
    # OpenAI
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4.1": "openai/gpt-4.1",
    "gpt-4.1-mini": "openai/gpt-4.1-mini",
    # Google
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    # DeepSeek（官方 API：deepseek-chat / deepseek-reasoner）
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-reasoner": "deepseek/deepseek-r1",
    # 智谱 GLM
    "glm-4.5": "z-ai/glm-4.5",
    "glm-4.5-air": "z-ai/glm-4.5-air",
    "glm-4.6": "z-ai/glm-4.6",
    "glm-4.7": "z-ai/glm-4.7",
    # 阿里通义千问 Qwen（最新 Qwen3.x 系列）
    "qwen3-max": "qwen/qwen3-max",
    "qwen3.7-max": "qwen/qwen3.7-max",
    "qwen3.6-plus": "qwen/qwen3.6-plus",
    "qwen3.7-plus": "qwen/qwen3.7-plus",
    "qwen3.6-flash": "qwen/qwen3.6-flash",
    # 月之暗面 Kimi
    "kimi-k2": "moonshotai/kimi-k2",
    "kimi-k2.6": "moonshotai/kimi-k2.6",
}

# OpenRouter 未上架的模型（人工维护；参考各厂商官方定价，人民币按 ≈7.2 折算 USD）。
MANUAL: dict[str, dict[str, float]] = {
    # 字节豆包 Doubao（火山引擎）
    "doubao-pro-32k": {
        "input": 0.11,
        "input_cached": 0.11,
        "output": 0.28,
        "cache_creation": 0.11,
    },
    "doubao-lite-32k": {
        "input": 0.04,
        "input_cached": 0.04,
        "output": 0.08,
        "cache_creation": 0.04,
    },
}

FIELDS = ("input", "input_cached", "output", "cache_creation")


def fetch_models() -> dict[str, dict[str, Any]]:
    """拉取 OpenRouter 模型列表，返回 ``{slug: model}``。"""
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL, headers={"User-Agent": "cost_control/sync_pricing"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        data = json.load(resp)
    return {m["id"]: m for m in data.get("data", [])}


def to_usd_per_m(token_price: Any) -> float | None:
    """USD/token → USD/百万 token（None / 非正 / 非法 → None）。"""
    if token_price is None:
        return None
    try:
        v = float(token_price)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return round(v * 1_000_000, 6)


def extract_prices(pricing: dict[str, Any]) -> dict[str, float]:
    """把 OpenRouter pricing 字段映射为本插件的 4 字段单价（USD/百万 token）。

    cache 字段缺失时按 input 价占位（保守不低估）。
    """
    inp = to_usd_per_m(pricing.get("prompt")) or 0.0
    out = to_usd_per_m(pricing.get("completion")) or 0.0
    cached = to_usd_per_m(pricing.get("input_cache_read"))
    creation = to_usd_per_m(pricing.get("input_cache_write"))
    return {
        "input": inp,
        "input_cached": cached if cached is not None else inp,
        "output": out,
        "cache_creation": creation if creation is not None else inp,
    }


def render_entry(key: str, prices: dict[str, float], indent: str = "    ") -> str:
    """渲染单条 ``"key": {...},``；超 100 字符时拆多行（避免 E501）。"""
    inner = ", ".join(f'"{f}": {prices[f]!r}' for f in FIELDS)
    single = f'{indent}"{key}": {{{inner}}},'
    if len(single) <= 100:
        return single
    lines = [f'{indent}"{key}": {{']
    for f in FIELDS:
        lines.append(f'{indent}    "{f}": {prices[f]!r},')
    lines.append(f"{indent}}},")
    return "\n".join(lines)


HEADER = '''"""内置常见模型的默认定价（USD / 百万 token）。

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
'''


def main() -> None:
    models = fetch_models()
    table: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for key, slug in TARGETS.items():
        model = models.get(slug)
        if not model:
            missing.append(f"{key} -> {slug}")
            continue
        table[key] = extract_prices(model.get("pricing") or {})
    table.update(MANUAL)

    if missing:
        print("⚠️  OpenRouter 未找到以下映射（已跳过，可在脚本中修正 slug）：")
        for item in missing:
            print(f"   - {item}")

    lines: list[str] = [HEADER, "from __future__ import annotations", ""]
    lines.append("DEFAULT_PRICING: dict[str, dict[str, float]] = {")
    for key, prices in table.items():
        lines.append(render_entry(key, prices))
    lines.append("}")
    lines.append("")

    out_path = "cost_control/default_pricing.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    synced = len(table) - len(MANUAL)
    print(
        f"✅ 已写入 {out_path}：{len(table)} 个模型"
        f"（{synced} 个来自 OpenRouter，{len(MANUAL)} 个人工维护）"
    )


if __name__ == "__main__":
    main()
