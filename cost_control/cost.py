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
    """按模型名匹配单价表（精确 > 前缀 > 关键词模糊，均大小写不敏感）。

    逐级回退，每级取**最长候选**（最具体的 key 优先），命中即返回：

    1. **精确匹配**（原名）；
    2. **前缀匹配**：``model`` 以某单价 key 开头——处理带日期 / 版本后缀的模型名
       （如 ``claude-sonnet-4-5-20250929`` → ``claude-sonnet-4-5``）；
    3. **关键词模糊**：``model`` 包含某单价 key 作为子串——让实际调用中的变体名
       命中内置预设（如厂商前缀 ``provider/qwen-max``、带后缀 ``qwen-max-20241115``、
       大小写差异 ``GLM-4.5``）。

    Args:
        model: 实际模型名（可能带厂商前缀、版本 / 日期后缀、变体）。
        pricing: 单价表（``get_pricing`` 返回）。

    Returns:
        匹配到的单价 dict，或 ``None``（无任何匹配）。
    """
    if not model:
        return None
    if model in pricing:
        return pricing[model]
    ml = model.lower()
    candidates = [name for name in pricing if ml.startswith(name.lower())]
    if not candidates:
        # 关键词模糊：模型名包含单价 key（子串，大小写不敏感）
        candidates = [name for name in pricing if name.lower() in ml]
    if candidates:
        candidates.sort(key=len, reverse=True)  # 最长（最具体）优先
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
