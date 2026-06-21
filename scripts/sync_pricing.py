#!/usr/bin/env python3
"""从 OpenRouter ``/api/v1/models`` 全量同步模型定价到 ``default_pricing.py``。

用法::

    uv run python scripts/sync_pricing.py

数据源是 OpenRouter 公开端点（无需 API key），其 ``pricing`` 字段以 USD / token
计，脚本换算为 USD / 百万 token。

- **全量拉取**：遍历 OpenRouter 目录的**全部**模型，不再维护手挑清单。每个模型的
  key 取 slug 去掉厂商前缀（``z-ai/glm-5.2`` → ``glm-5.2``）并小写，便于
  :func:`cost_control.cost.match_pricing`（精确 > 前缀 > 子串，最长优先）命中实际调用名。
- **过滤无价条目**：input 与 output 均为 0 的模型（``:free`` 免费版 / 无定价数据）跳过
  ——它们对成本核算无贡献，计入等于当作「未定价」噪声。
- **键冲突保守取高**：不同厂商重托管同一模型名会产生键冲突，此时**保留 input 单价
  更高者**（与 cache 字段缺省按 input 价占位一致的「保守不低估成本」哲学），并在末尾
  报告冲突数。迭代顺序按 slug 排序保证可复现。
- ``MANUAL``：OpenRouter 未上架的模型（人工维护，随厂商调价更新），合并时覆盖同名自动条目。
- cache 字段（``input_cache_read`` / ``input_cache_write``）缺失时按 input 价占位。

生成后建议运行::

    uv run ruff format cost_control/default_pricing.py
    uv run pytest tests/test_cost.py
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# OpenRouter 未上架的模型（人工维护；参考各厂商官方定价，人民币按 ≈7.2 折算 USD）。
# 合并时覆盖同名自动条目（厂商官方价优先于第三方重托管）。
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


def bare_key(slug: str) -> str:
    """slug → 匹配用 key：去掉前导 ``~``（OpenRouter 别名 / 下架标记）与厂商前缀，小写。

    ``z-ai/glm-5.2`` → ``glm-5.2``；``openai/gpt-4o:free`` → ``gpt-4o:free``。
    保留 ``:free`` / ``:nitro`` / ``:thinking`` 等后缀（各自有独立定价）。
    """
    name = slug.lstrip("~")
    if "/" in name:
        name = name.split("/", 1)[1]
    return name.lower()


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

由 ``scripts/sync_pricing.py`` 从 OpenRouter ``/api/v1/models`` **全量**自动生成
（最新快照）。**请勿手改**——重跑 ``uv run python scripts/sync_pricing.py`` 即可刷新；
OpenRouter 未覆盖的模型（如豆包）在脚本的 ``MANUAL`` 区人工维护。

字段含义：
    input          —— 非缓存输入 token（对应 ``ProviderStat.token_input_other``）
    input_cached   —— 缓存命中输入 token（对应 ``ProviderStat.token_input_cached``）
    output         —— 输出 token（对应 ``ProviderStat.token_output``）
    cache_creation —— 缓存写入 token（Anthropic 原生字段；非 Anthropic 模型无此概念，
                      OpenRouter 未提供时按 input 价占位，保守不低估成本）

key 取 slug 去厂商前缀后的小写名（``z-ai/glm-5.2`` → ``glm-5.2``），不同厂商重托管
同名模型冲突时保留 input 单价更高者（保守）。运行时由 :func:`cost_control.config.get_pricing`
合并：本表（出厂默认）⊕ 用户在 ``pricing`` 配置项的自定义覆盖。模型名匹配走
:func:`cost_control.cost.match_pricing`（精确 > 前缀 > 子串，最长优先），故实际调用中的
变体名（厂商前缀、版本 / 日期后缀、大小写差异）通常能自动命中预设，无需逐个配置。
"""
'''


def main() -> None:
    models = fetch_models()
    table: dict[str, dict[str, float]] = {}
    collisions = 0
    skipped_unpriced = 0
    for slug in sorted(models):
        prices = extract_prices(models[slug].get("pricing") or {})
        if prices["input"] == 0 and prices["output"] == 0:
            skipped_unpriced += 1
            continue
        key = bare_key(slug)
        existing = table.get(key)
        if existing is None:
            table[key] = prices
        elif prices["input"] > existing["input"]:
            table[key] = prices  # 保守：保留单价更高的重托管版本
            collisions += 1
        else:
            collisions += 1
    table.update(MANUAL)  # 厂商官方价覆盖同名自动条目

    lines: list[str] = [HEADER, "from __future__ import annotations", ""]
    lines.append("DEFAULT_PRICING: dict[str, dict[str, float]] = {")
    for key in sorted(table):
        lines.append(render_entry(key, table[key]))
    lines.append("}")
    lines.append("")

    out_path = "cost_control/default_pricing.py"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    synced = len(table) - len(MANUAL)
    print(
        f"✅ 已写入 {out_path}：{len(table)} 个模型"
        f"（{synced} 个来自 OpenRouter 全量，{len(MANUAL)} 个人工维护）"
    )
    print(
        f"   OpenRouter 总模型 {len(models)}，跳过无价 {skipped_unpriced}，"
        f"键冲突 {collisions}（保守取高）"
    )


if __name__ == "__main__":
    main()
