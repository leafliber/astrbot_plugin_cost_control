"""成本计算 Mixin。

根据用户配置的模型单价表（pricing），把 token 用量换算为 USD 成本。
单价以「美元 / 百万 token」为单位。核心计算逻辑抽成模块级纯函数
``compute_cost_value``，便于单元测试；``CostMixin`` 仅做配置读取与委托。

阶段 1 实现。
"""

from __future__ import annotations

from typing import Any

from .config import get_pricing


def match_pricing(
    model: str | None,
    pricing: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    """按模型名匹配单价表。精确匹配优先，否则取最长公共前缀（处理带日期后缀的模型名）。

    例如模型 ``claude-sonnet-4-5-20250929`` 会匹配到 ``claude-sonnet-4-5``。
    """
    if not model:
        return None
    if model in pricing:
        return pricing[model]
    candidates = [name for name in pricing if model.startswith(name)]
    if candidates:
        candidates.sort(key=len, reverse=True)
        return pricing[candidates[0]]
    return None


def compute_cost_value(
    usage: dict[str, Any],
    model: str | None,
    pricing: dict[str, dict[str, float]],
) -> float:
    """按单价表把 usage 换算为 USD 成本（纯函数，可单测）。

    Args:
        usage: ``UsageQueryMixin.query_usage`` 返回的聚合用量 dict，字段：
            ``token_input_other`` / ``token_input_cached`` / ``token_output`` /
            ``cache_creation``（可选，来自补充采集）。
        model: 模型名，用于匹配单价。
        pricing: 单价表（``get_pricing`` 返回）。

    Returns:
        USD 成本（float）。模型无匹配单价时返回 0.0。
    """
    prices = match_pricing(model, pricing)
    if not prices:
        return 0.0
    input_price = float(prices.get("input", 0.0) or 0.0)
    cached_price = float(prices.get("input_cached", 0.0) or 0.0)
    output_price = float(prices.get("output", 0.0) or 0.0)
    # cache_creation 单价缺省时按 input 价计（缓存写入通常等同或略高于输入）。
    creation_price = float(prices.get("cache_creation", input_price) or input_price)

    cost = (
        int(usage.get("token_input_other", 0) or 0) * input_price
        + int(usage.get("token_input_cached", 0) or 0) * cached_price
        + int(usage.get("token_output", 0) or 0) * output_price
        + int(usage.get("cache_creation") or 0) * creation_price
    ) / 1_000_000.0
    return float(cost)


class CostMixin:
    """按模型单价表计算 USD 成本的 Mixin。"""

    def get_pricing(self) -> dict[str, dict[str, float]]:
        """返回当前生效的模型单价表（默认表 + 用户配置覆盖）。"""
        return get_pricing(getattr(self, "cfg", None))

    async def compute_cost(self, usage: dict[str, Any], model: str | None) -> float:
        """按模型单价把 usage 换算为 USD 成本。

        Args:
            usage: ``UsageQueryMixin.query_usage`` 返回的聚合用量 dict。
            model: 模型名，用于匹配单价表。

        Returns:
            USD 成本（float）。
        """
        return compute_cost_value(usage, model, self.get_pricing())
