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

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .config import get_pricing


@lru_cache(maxsize=4096)
def _normalize_model_name(name: str) -> str:
    """把模型名 / provider_id 规范化为统一比较形式（用于模糊匹配）。

    处理实际调用中常见的写法差异：

    - **去命名空间前缀**：取最后一段路径（``a/b/c`` → ``c``）——剥离 OpenRouter /
      NewAPI 风格的厂商前缀（``minimax/MiniMax-M2.7`` → ``MiniMax-M2.7``、
      ``newapi/moonshotai/kimi-k2.6`` → ``kimi-k2.6``）。
    - **统一分隔符**：下划线 / 空格 / 点 → 连字符（``MiniMax_M2.7`` → ``MiniMax-M2-7``），
      随后折叠重复连字符、去首尾连字符——让 ``claude-sonnet-4-5``（连字符）与
      ``claude-sonnet-4.5``（点）这类版本号写法对齐。
    - **小写**。

    内置 ``DEFAULT_PRICING`` 的 key 是小写、连字符分隔、无前缀的 slug；本函数把 target
    拉到同形态，使精确 / 前缀匹配能直接命中（而非仅靠子串兜底）。对 key 自身也规范一遍，
    保证双向一致。结果带 ``lru_cache``，DEFAULT_PRICING 约 300 键重复调用零开销。
    """
    s = name.rsplit("/", 1)[-1].strip()
    s = s.replace("_", "-").replace(" ", "-").replace(".", "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-").lower()


def _best_match_key(target: str | None, table: Mapping[str, Any]) -> str | None:
    """在 ``table`` 的键中为 ``target`` 选最佳匹配键（模糊匹配，应对各种写法）。

    四级回退，每级取**最长候选**（最具体优先），命中即返回：

    1. **原样精确**（大小写敏感）：``target`` 原形直接命中 table；
    2. **规范化精确**：target 与 key 经 :func:`_normalize_model_name` 拉到同形态
       （剥前缀、统一分隔符、小写）后相等——命中厂商前缀（``minimax/MiniMax-M2.7``）、
       多层命名空间（``newapi/moonshotai/kimi-k2.6``）、下划线 / 空格分隔
       （``MiniMax_M2.7``）、大小写差异（``GLM-4.5``）；
    3. **规范化前缀**：规范化后的 target 以规范化后的 key 开头——处理版本 / 日期后缀
       （``claude-sonnet-4-5-20250929`` → ``claude-sonnet-4.5``、
       ``anthropic/claude-sonnet-4-5-20250929``、``qwen3-max-20241115``）；
    4. **规范化学串**：规范化后的 target 包含规范化后的 key——最后兜底。

    该函数同时驱动 :func:`match_pricing`（默认价按模型名匹配）与 :func:`resolve_pricing`
    的用户定价 provider_id 模糊匹配，故两者规则一致。
    """
    if not target:
        return None
    if target in table:
        return target
    nt = _normalize_model_name(target)
    if not nt:
        return None
    normed = [(_normalize_model_name(k), k) for k in table]
    exact = [k for nk, k in normed if nk == nt]
    if exact:
        exact.sort(key=len, reverse=True)
        return exact[0]
    candidates = [k for nk, k in normed if nk and nt.startswith(nk)]
    if not candidates:
        candidates = [k for nk, k in normed if nk and nk in nt]
    if candidates:
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return None


def match_pricing(
    model: str | None,
    pricing: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    """按模型名匹配**默认单价表**（:func:`_best_match_key` 三级算法）。

    仅作用于 :func:`get_pricing` 返回的 ``defaults`` 空间（key=模型名，per_token）。
    用户按 provider_id 的覆盖在 :func:`resolve_pricing` 中优先处理，不经过本函数。

    Args:
        model: 实际模型名（可能带厂商前缀、版本 / 日期后缀、变体）。
        pricing: 默认单价表（``get_pricing(...)["defaults"]``）。

    Returns:
        匹配到的单价 dict，或 ``None``（无任何匹配）。
    """
    key = _best_match_key(model, pricing)
    return pricing[key] if key is not None else None


def resolve_pricing(
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
) -> dict[str, Any] | None:
    """解析生效的计费规则（优先级：provider_id 用户定价 > 模型名默认 > 未定价）。

    **provider_id 模糊匹配**：用户定价 dict 的 key 不必与 provider_id 精确相等，
    走 :func:`_best_match_key` 三级算法（精确 > 前缀 > 子串，最长优先）——
    故用户把 ``deepseek`` 配为 key 即可命中实际 provider_id
    ``deepseek-official-01``（子串）；最长优先保证 ``gpt-4`` 优先于 ``gpt``
    命中 ``gpt-4o-mini``。仅做「key 是 provider_id 的子串 / 前缀」方向匹配。

    Args:
        provider_id: Provider ID（用户定价模糊匹配）。
        model: 模型名（默认表匹配）。
        pricing: :func:`get_pricing` 返回的 ``{"defaults", "user"}`` 结构。

    Returns:
        规范化规则 dict，或 ``None``（未定价，成本 0）：

        - ``{"mode":"per_token","input","input_cached","output","cache_creation"?}``
        - ``{"mode":"per_turn","price"}``
        - ``{"mode":"per_request","price"}``
    """
    user = pricing.get("user") if isinstance(pricing, dict) else None
    if isinstance(user, dict) and provider_id:
        key = _best_match_key(provider_id, user)
        if key is not None:
            rule = user[key]
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

    .. note::

        本函数返回的是**原始计费货币金额**（当 entry 指定了 ``currency`` 时，
        实际是该货币金额而非 USD，但历史调用方均按 USD 处理）。新代码应优先使用
        :func:`compute_cost_with_currency` 获取金额 + 货币代码，再按需换算。
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


def compute_cost_with_currency(
    usage: dict[str, Any],
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
) -> tuple[float, str]:
    """按生效定价把单条 usage 换算为**原始计费货币**成本，返回 (金额, 货币代码)（纯函数）。

    与 :func:`compute_cost_value` 的区别：返回值附带定价规则的 ``currency`` 字段
    （默认 ``"USD"``），便于调用方按汇率换算到主货币。

    Args:
        usage: 聚合用量 dict。
        provider_id: Provider ID（用户定价匹配）。
        model: 模型名（默认表匹配）。
        pricing: :func:`get_pricing` 返回的 ``{"defaults", "user"}`` 结构。

    Returns:
        ``(原始货币金额, 货币代码)``。未匹配定价返回 ``(0.0, "USD")``。
        per_request 单条返回 ``(0.0, 货币代码)``（聚合在调用方处理）。
    """
    rule = resolve_pricing(provider_id, model, pricing)
    if rule is None:
        return 0.0, "USD"
    cur = str(rule.get("currency", "USD") or "USD").strip().upper() or "USD"
    mode = rule.get("mode", "per_token")
    if mode == "per_token":
        return _cost_per_token(usage, rule), cur
    if mode == "per_turn":
        return float(rule.get("price", 0.0) or 0.0), cur
    # per_request：单条无法独立计费
    return 0.0, cur


def compute_cost_in_main(
    usage: dict[str, Any],
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
    main_currency: str,
    rates: dict[str, float],
) -> float:
    """按生效定价算成本并换算到主货币（纯函数）。

    先取原始计费货币金额（:func:`compute_cost_with_currency`），再按汇率换算到
    ``main_currency``。未匹配定价返回 0.0。

    Args:
        usage: 聚合用量 dict。
        provider_id: Provider ID（用户定价匹配）。
        model: 模型名（默认表匹配）。
        pricing: :func:`get_pricing` 返回的结构。
        main_currency: 主货币代码。
        rates: 生效汇率表。

    Returns:
        主货币成本（float，四舍五入 6 位）。
    """
    from .exchange_rates import convert

    raw_cost, cur = compute_cost_with_currency(usage, provider_id, model, pricing)
    if raw_cost <= 0:
        return 0.0
    return round(convert(raw_cost, cur, main_currency, rates), 6)


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


def compute_row_cost_in_main(
    row: dict[str, Any],
    pricing: dict[str, Any],
    main_currency: str,
    rates: dict[str, float],
) -> float:
    """算单条聚合行的成本并换算到主货币（纯函数）。

    与 :func:`compute_row_cost` 同，但先取原始计费货币金额，再按汇率换算到
    ``main_currency``。
    """
    from .exchange_rates import convert

    try:
        provider_id = row.get("provider_id") or None
        model = row.get("provider_model") or row.get("key")
        rule = resolve_pricing(provider_id, model, pricing)
        if rule is None:
            return 0.0
        cur = str(rule.get("currency", "USD") or "USD").strip().upper() or "USD"
        mode = rule.get("mode", "per_token")
        if mode == "per_token":
            raw = _cost_per_token(row, rule)
        else:
            raw = int(row.get("count", 0) or 0) * float(rule.get("price", 0.0) or 0.0)
        return round(convert(raw, cur, main_currency, rates), 6)
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


def compute_cost_grouped_in_main(
    rows: list[dict[str, Any]],
    pricing: dict[str, Any],
    main_currency: str,
    rates: dict[str, float],
) -> float:
    """对聚合行求总成本并换算到主货币（纯函数）。

    等于各行 :func:`compute_row_cost_in_main` 之和（四舍五入 6 位）。
    """
    total = 0.0
    for r in rows or []:
        total += compute_row_cost_in_main(r, pricing, main_currency, rates)
    return round(total, 6)


class CostMixin:
    """按生效定价计算 USD 成本的 Mixin。"""

    def get_pricing(self) -> dict[str, Any]:
        """返回当前生效的定价结构（``{"defaults", "user"}``，见 :func:`get_pricing`）。"""
        return get_pricing(getattr(self, "cfg", None))

    def get_main_currency(self) -> str:
        """返回当前主货币代码（默认 ``"$"``）。"""
        from .config import get_currency_symbol

        return get_currency_symbol(getattr(self, "cfg", None))

    def get_rates(self) -> dict[str, float]:
        """返回当前生效汇率表（合并 config 与 DEFAULT_RATES）。"""
        from .config import get_rates

        return get_rates(getattr(self, "cfg", None))

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
