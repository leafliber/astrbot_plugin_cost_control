"""成本计算 Mixin。

根据生效的定价结构（:func:`cost_control.config.get_pricing` 返回的
``{"defaults", "user"}``）把 token 用量 / 调用次数 / 请求数换算为 USD 成本。

支持三种计费模式（``mode``）：

- ``per_token``：按 input / input_cached / output（+cache_creation）token 计费，
  USD / 百万 token。内置 ``DEFAULT_PRICING`` 即此模式（按模型名匹配）。
- ``per_turn``：每次 LLM 调用（每条 ProviderStat / CostSupplement 记录）固定 USD/次，
  仅计 LLM 调用次数（不含非 LLM tool 执行）。
- ``per_request``：每次用户请求固定 USD/次；一次请求可能含多次 function-calling LLM
  调用，按 distinct ``request_id`` 计数。

匹配优先级（:func:`resolve_pricing`）：用户定价按 ``provider_id`` 命中 → 内置默认按
模型名匹配（per_token）→ 未定价（成本 0）。

核心计算逻辑抽成模块级纯函数（``resolve_pricing`` / ``compute_cost_value``），便于
单元测试；``CostMixin`` 仅做配置读取与委托。

**per_request 数据局限**：``ProviderStat`` 主表无 ``request_id``，只有补充表
``CostSupplement`` 有。故 per_request 仅在 supplement 路径（records 明细、按用户成本）
精确；主表聚合路径（总览 / 预算 / 日报 / analytics）按 per_turn 近似——见各调用点。
"""

from __future__ import annotations

from typing import Any

from .config import get_pricing


def match_pricing(
    model: str | None,
    pricing: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    """按模型名匹配**默认单价表**（精确 > 前缀 > 关键词模糊，均大小写不敏感）。

    仅作用于 :func:`get_pricing` 返回的 ``defaults`` 空间（key=模型名，per_token）。
    用户按 provider_id 的覆盖在 :func:`resolve_pricing` 中优先处理，不经过本函数。

    逐级回退，每级取**最长候选**（最具体的 key 优先），命中即返回：

    1. **精确匹配**（原名）；
    2. **前缀匹配**：``model`` 以某单价 key 开头——处理带日期 / 版本后缀的模型名
       （如 ``claude-sonnet-4-5-20250929`` → ``claude-sonnet-4-5``）；
    3. **关键词模糊**：``model`` 包含某单价 key 作为子串——让实际调用中的变体名
       命中内置预设（如厂商前缀 ``provider/qwen-max``、带后缀 ``qwen-max-20241115``、
       大小写差异 ``GLM-4.5``）。

    Args:
        model: 实际模型名（可能带厂商前缀、版本 / 日期后缀、变体）。
        pricing: 默认单价表（``get_pricing(...)["defaults"]``）。

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


def resolve_pricing(
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
) -> dict[str, Any] | None:
    """解析生效的计费规则（优先级：provider_id 用户定价 > 模型名默认 > 未定价）。

    Args:
        provider_id: Provider ID（用户定价的 key）。
        model: 模型名（默认表的 key）。
        pricing: :func:`get_pricing` 返回的 ``{"defaults", "user"}`` 结构。

    Returns:
        规范化规则 dict，或 ``None``（未定价，成本 0）：

        - ``{"mode":"per_token","input","input_cached","output","cache_creation"?}``
        - ``{"mode":"per_turn","price"}``
        - ``{"mode":"per_request","price"}``
    """
    user = pricing.get("user") if isinstance(pricing, dict) else None
    if isinstance(user, dict) and provider_id and provider_id in user:
        rule = user[provider_id]
        if isinstance(rule, dict) and rule.get("mode"):
            return rule
    defaults = pricing.get("defaults") if isinstance(pricing, dict) else None
    if isinstance(defaults, dict):
        prices = match_pricing(model, defaults)
        if prices:
            return {"mode": "per_token", **prices}
    return None


def _cost_per_token(usage: dict[str, Any], rule: dict[str, Any]) -> float:
    """per_token 模式成本（USD）。单价 USD / 百万 token。"""
    input_price = float(rule.get("input", 0.0) or 0.0)
    cached_price = float(rule.get("input_cached", 0.0) or 0.0)
    output_price = float(rule.get("output", 0.0) or 0.0)
    # cache_creation 单价缺省时按 input 价计（缓存写入通常等同或略高于输入）。
    creation_price = float(rule.get("cache_creation", input_price) or input_price)
    cost = (
        int(usage.get("token_input_other", 0) or 0) * input_price
        + int(usage.get("token_input_cached", 0) or 0) * cached_price
        + int(usage.get("token_output", 0) or 0) * output_price
        + int(usage.get("cache_creation") or 0) * creation_price
    ) / 1_000_000.0
    return float(cost)


def compute_cost_value(
    usage: dict[str, Any],
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
) -> float:
    """按生效定价把单条 usage 换算为 USD 成本（纯函数，可单测）。

    单条记录场景下：

    - per_token：按 token 四类计算。
    - per_turn：一次调用固定 ``price``（单条 = 1 次）。
    - per_request：**返回 0**——单条无法独立计费（需 distinct request_id 聚合），
      聚合路径见调用方（按 count 近似或按 supplement distinct 精确）。

    Args:
        usage: 聚合用量 dict（``token_input_other`` / ``token_input_cached`` /
            ``token_output`` / ``cache_creation``?）。
        provider_id: Provider ID（用户定价匹配）。
        model: 模型名（默认表匹配）。
        pricing: :func:`get_pricing` 返回的 ``{"defaults", "user"}`` 结构。

    Returns:
        USD 成本（float）。未匹配定价 / per_request 单条时返回 0.0。
    """
    rule = resolve_pricing(provider_id, model, pricing)
    if rule is None:
        return 0.0
    mode = rule.get("mode", "per_token")
    if mode == "per_token":
        return _cost_per_token(usage, rule)
    if mode == "per_turn":
        return float(rule.get("price", 0.0) or 0.0)
    # per_request：单条无法独立计费，聚合在调用方处理
    return 0.0


def compute_row_cost(row: dict[str, Any], pricing: dict[str, Any]) -> float:
    """算单条**聚合行**的成本（供 grouped 聚合路径，纯函数）。

    行须含 ``provider_id`` / ``provider_model`` / ``count`` / token 字段：

    - per_token：按 token 四类计算（token 已在该维度聚合）。
    - per_turn：``count × price``（count = LLM 调用次数）。
    - per_request：**主表无 request_id**，按 ``count × price`` 近似（精确仅 supplement 路径）。
    - 未匹配定价：0.0。
    """
    try:
        provider_id = row.get("provider_id") or None
        model = row.get("provider_model") or row.get("key")
        rule = resolve_pricing(provider_id, model, pricing)
        if rule is None:
            return 0.0
        mode = rule.get("mode", "per_token")
        if mode == "per_token":
            return _cost_per_token(row, rule)
        return int(row.get("count", 0) or 0) * float(rule.get("price", 0.0) or 0.0)
    except Exception:
        return 0.0


def compute_cost_grouped(
    rows: list[dict[str, Any]],
    pricing: dict[str, Any],
) -> float:
    """对 ``query_usage_grouped(by="provider_model")`` 返回的聚合行求总成本（纯函数）。

    等于各行 :func:`compute_row_cost` 之和（四舍五入 6 位）。per_request 在主表
    路径按 per_turn 近似（主表无 request_id）。
    """
    total = 0.0
    for r in rows or []:
        total += compute_row_cost(r, pricing)
    return round(total, 6)


class CostMixin:
    """按生效定价计算 USD 成本的 Mixin。"""

    def get_pricing(self) -> dict[str, Any]:
        """返回当前生效的定价结构（``{"defaults", "user"}``，见 :func:`get_pricing`）。"""
        return get_pricing(getattr(self, "cfg", None))

    async def compute_cost(
        self,
        usage: dict[str, Any],
        provider_id: str | None,
        model: str | None,
    ) -> float:
        """按生效定价把单条 usage 换算为 USD 成本。

        Args:
            usage: 聚合用量 dict。
            provider_id: Provider ID（用户定价匹配）。
            model: 模型名（默认表匹配）。

        Returns:
            USD 成本（float）。
        """
        return compute_cost_value(usage, provider_id, model, self.get_pricing())
