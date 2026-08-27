"""成本计算 Mixin。

根据生效的定价结构（:func:`cost_control.config.get_pricing` 返回的
``{"defaults", "user", "multipliers"}``，运行时再附加 ``provider_clusters``）把
token 用量 / 调用次数 / 请求数换算为 USD 成本。

支持五种计费模式（``mode``）：
- ``per_token``：按 input / input_cached / output（+cache_creation）token 计费，
  USD / 百万 token。内置 ``DEFAULT_PRICING`` 即此模式（按模型名匹配）。
- ``per_turn``：每次 LLM 调用（每条 ProviderStat / CostSupplement 记录）固定 USD/次，
  仅计 LLM 调用次数（不含非 LLM tool 执行）。
- ``per_request``：每次用户请求固定 USD/次；一次请求可能含多次 function-calling LLM
  调用，按 distinct ``request_id`` 计数。
- ``per_tier``：基础价 + context tier（按上下文长度阶梯覆盖）+ service tier（倍率）。
- ``tiered_expr``：New API 兼容表达式动态计费（:mod:`cost_control.expr_eval`）。

匹配优先级（:func:`resolve_pricing`）：模型级手工价 ``provider_id|model`` → 用户确认/
自动匹配的候选价格（price_catalog）→ 旧 provider 级手工价（模糊）→ 内置默认按模型名
匹配（per_token）→ 未定价（成本 0）。

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

from .config import get_enabled_price_sources, get_pricing


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


_PER_TOKEN_FIELDS: tuple[str, ...] = ("input", "input_cached", "output", "cache_creation")


def resolve_pricing(
    provider_id: str | None,
    model: str | None,
    pricing: dict[str, Any],
) -> dict[str, Any] | None:
    """解析生效的计费规则（六级优先级，见模块 docstring）。

    1. 模型级手工价 ``user["provider_id|model"]``（精确，不参与模糊匹配）。
    2. **用户确认**的候选价格（``selections`` → ``catalog``）。
    3. 旧 provider 级手工价 ``user[provider_id]`` 模糊匹配（精确 > 前缀 > 子串，最长优先）。
    4. **自动匹配**的目录候选（未确认，唯一高置信才应用）——排在 legacy 手工价之后，
       避免首次同步目录后静默覆盖存量 provider 级手工定价。
    5. 内置默认表按模型名匹配（per_token）。
    6. 未定价 → None（成本 0）。

    ``pricing`` 通过注入 ``catalog`` / ``selections`` 键携带价格目录与选择（缺省为空）。
    用户 per_token 未配置字段经 :func:`_inherit_per_token` 从候选/默认价继承（AC7）。

    Returns:
        规范化规则 dict（``per_token``/``per_turn``/``per_request``/``per_tier``/
        ``tiered_expr``），或 ``None``。
    """
    user = pricing.get("user") if isinstance(pricing, dict) else None
    user = user if isinstance(user, dict) else {}
    rule: dict[str, Any] | None = None
    if provider_id and model:
        # 1. 模型级手工价（精确 key）
        exact = user.get(f"{provider_id}|{model}")
        if isinstance(exact, dict) and exact.get("mode"):
            return _apply_cluster_multiplier(
                _inherit_per_token(exact, provider_id, model, pricing), provider_id, pricing
            )
    # 2. 用户确认的候选价格——不依赖手工定价存在
    if provider_id:
        sel = _resolve_confirmed_selection(provider_id, model, pricing)
        if sel is not None:
            return _apply_cluster_multiplier(sel, provider_id, pricing)
    # 3. 旧 provider 级手工价（模糊，排除模型级 key）
    if provider_id:
        legacy = {k: v for k, v in user.items() if "|" not in k}
        key = _best_match_key(provider_id, legacy)
        if key is not None:
            candidate = user[key]
            if isinstance(candidate, dict) and candidate.get("mode"):
                rule = _inherit_per_token(candidate, provider_id, model, pricing)
    if rule is not None:
        return _apply_cluster_multiplier(rule, provider_id, pricing)
    # 4. 自动匹配候选（未确认）——legacy 手工价之后
    if provider_id:
        auto = _resolve_auto_selection(provider_id, model, pricing)
        if auto is not None:
            return _apply_cluster_multiplier(auto, provider_id, pricing)
    # 5. 内置默认表
    defaults = pricing.get("defaults") if isinstance(pricing, dict) else None
    matched_model: str | None = None
    if isinstance(defaults, dict):
        matched_model = _best_match_key(model, defaults)
        if matched_model is not None:
            prices = defaults[matched_model]
            if isinstance(prices, dict) and prices:
                rule = {"mode": "per_token", **prices}
    if rule is None:
        return None
    return _apply_cluster_multiplier(rule, provider_id, pricing)


def _apply_cluster_multiplier(
    rule: dict[str, Any],
    provider_id: str | None,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """按 AstrBot Provider Source 聚类倍率调整规则副本，不修改基础价格。

    命中层级统一套用倍率（PR 承诺语义）：per_token 四字段、per_tier 的 base 与
    context_tiers 单价、tiered_expr 的表达式输出整体；per_turn/per_request 的
    ``price``。per_tier 的 service_tiers 是相对 base 的倍率，随 base 自动缩放，
    不额外相乘。
    """
    multipliers = pricing.get("multipliers") if isinstance(pricing, dict) else None
    provider_clusters = pricing.get("provider_clusters") if isinstance(pricing, dict) else None
    if (
        not provider_id
        or not isinstance(multipliers, dict)
        or not isinstance(provider_clusters, dict)
    ):
        return rule
    cluster_id = provider_clusters.get(provider_id)
    if not cluster_id:
        return rule
    try:
        multiplier = float(multipliers.get(cluster_id, 1.0) or 1.0)
    except (TypeError, ValueError):
        return rule
    if multiplier <= 0 or abs(multiplier - 1.0) <= 1e-12:
        return rule

    def _scale_fields(target: dict[str, Any]) -> dict[str, Any]:
        out = dict(target)
        for fld in _PER_TOKEN_FIELDS:
            value = out.get(fld)
            if value is not None:
                out[fld] = float(value or 0.0) * multiplier
        return out

    out = dict(rule)
    mode = out.get("mode", "per_token")
    if mode == "per_token":
        out = _scale_fields(out)
    elif mode == "per_tier":
        if isinstance(out.get("base"), dict):
            out["base"] = _scale_fields(out["base"])
        tiers = []
        for tier in out.get("context_tiers") or []:
            tiers.append(_scale_fields(tier) if isinstance(tier, dict) else tier)
        out["context_tiers"] = tiers
    elif mode == "tiered_expr":
        # 表达式输出（$/1M）整体乘倍率，等价于每个价格分支乘倍率；求值时应用。
        out["cluster_multiplier"] = multiplier
    elif out.get("price") is not None:
        out["price"] = float(out.get("price", 0.0) or 0.0) * multiplier
    return out


def _resolve_confirmed_selection(
    provider_id: str | None, model: str | None, pricing: dict[str, Any]
) -> dict[str, Any] | None:
    """解析**用户确认**的候选选择（``selections`` → ``catalog``）。"""
    if not provider_id or not model or not isinstance(pricing, dict):
        return None
    selections = pricing.get("selections")
    catalog = pricing.get("catalog")
    if isinstance(selections, dict) and isinstance(catalog, dict):
        per_model = selections.get(provider_id)
        if isinstance(per_model, dict):
            sel = per_model.get(model)
            if isinstance(sel, dict):
                price_key = sel.get("price_key")
                price = catalog.get(price_key) if price_key else None
                if isinstance(price, dict):
                    return _catalog_price_to_rule(price)
    return None


def _resolve_auto_selection(
    provider_id: str | None, model: str | None, pricing: dict[str, Any]
) -> dict[str, Any] | None:
    """解析**自动匹配**的目录候选（未确认；仅唯一高置信时应用）。"""
    if not provider_id or not model or not isinstance(pricing, dict):
        return None
    catalog_obj = pricing.get("catalog_obj")
    if catalog_obj is None:
        return None
    try:
        from .price_catalog import select_auto_candidate

        enabled_sources = pricing.get("enabled_sources")
        source_filter = (
            set(enabled_sources) if isinstance(enabled_sources, (list, set, tuple)) else None
        )
        # get_pricing 已按 (provider, model) 预算自动匹配，热路径直接查表；
        # 未命中才回退实时匹配（走 find_candidates 缓存）。
        auto_selected = pricing.get("auto_selected")
        if isinstance(auto_selected, dict):
            pids = (provider_id, None) if provider_id else (None,)
            for pid in pids:
                per_model = auto_selected.get(pid)
                if not isinstance(per_model, dict):
                    continue
                price_key = per_model.get(model)
                if not price_key:
                    continue
                entry = pricing.get("catalog", {}).get(price_key)
                if isinstance(entry, dict):
                    return _catalog_price_to_rule(entry)
        auto = select_auto_candidate(catalog_obj.find_candidates(model, sources=source_filter))
        if auto is not None:
            return _catalog_price_to_rule(auto.price.to_dict())
    except Exception:
        pass
    return None


def _resolve_selection(
    provider_id: str | None, model: str | None, pricing: dict[str, Any]
) -> dict[str, Any] | None:
    """确认选择优先，缺失时应用唯一高置信的目录候选（供字段继承回落）。"""
    sel = _resolve_confirmed_selection(provider_id, model, pricing)
    if sel is not None:
        return sel
    return _resolve_auto_selection(provider_id, model, pricing)


def _catalog_field_values(price: dict[str, Any]) -> dict[str, Any]:
    """把目录的 prompt/completion 字段映射为计费规则的 input/output 字段。"""
    output = price.get("output")
    if output is None:
        output = price.get("completion")
    return {
        "input": price.get("input") if price.get("input") is not None else price.get("prompt"),
        "input_cached": (
            price.get("input_cached")
            if price.get("input_cached") is not None
            else (
                price.get("cache_read")
                if price.get("cache_read") is not None
                else price.get("cache")
            )
        ),
        "output": output,
        "cache_creation": price.get("cache_creation"),
    }


def _catalog_context_tier_to_rule(tier: dict[str, Any]) -> dict[str, Any] | None:
    """将 catalog context tier 转为 per_tier 的用户规则字段。"""
    try:
        threshold = int(tier.get("threshold_tokens"))
    except (TypeError, ValueError):
        return None
    return {"threshold_tokens": threshold, **_catalog_field_values(tier)}


def _catalog_service_tier_to_rule(
    tier: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any] | None:
    """将 catalog service tier 的绝对单价换算为 per_tier 的字段倍率。"""
    match = str(tier.get("match") or tier.get("service_tier") or "").strip()
    if not match:
        return None
    out: dict[str, Any] = {"match": match}
    absolute = _catalog_field_values(tier)
    for field in _PER_TOKEN_FIELDS:
        multiplier_key = f"{field}_multiplier"
        if tier.get(multiplier_key) is not None:
            out[multiplier_key] = tier[multiplier_key]
            continue
        value = absolute.get(field)
        base_value = base.get(field)
        if value is None or base_value in (None, 0):
            continue
        try:
            out[multiplier_key] = float(value) / float(base_value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return out if len(out) > 1 else None


def _catalog_price_to_rule(price: dict[str, Any]) -> dict[str, Any] | None:
    """把目录价格条目转为内部计费规则，兼容 catalog 与用户规则的不同字段名。"""
    mode = str(price.get("mode") or "per_token")
    cur = str(price.get("currency") or "USD").strip().upper() or "USD"
    if mode == "per_token":
        return {"mode": "per_token", "currency": cur, **_catalog_field_values(price)}
    if mode == "per_tier":
        base = _catalog_field_values(price)
        context_tiers = [
            converted
            for raw in (price.get("context_tiers") or [])
            if isinstance(raw, dict)
            if (converted := _catalog_context_tier_to_rule(raw)) is not None
        ]
        service_tiers = [
            converted
            for raw in (price.get("service_tiers") or [])
            if isinstance(raw, dict)
            if (converted := _catalog_service_tier_to_rule(raw, base)) is not None
        ]
        return {
            "mode": "per_tier",
            "currency": cur,
            "base": base,
            "context_tiers": context_tiers,
            "service_tiers": service_tiers,
        }
    if mode == "tiered_expr":
        return {"mode": "tiered_expr", "expr": price.get("expr"), "currency": cur}
    if mode in ("per_turn", "per_request"):
        return {"mode": mode, "price": price.get("price", 0.0), "currency": cur}
    return None


def _inherit_per_token(
    rule: dict[str, Any], provider_id: str | None, model: str | None, pricing: dict[str, Any]
) -> dict[str, Any]:
    """用户 per_token 未配置字段从候选/默认价继承；无回落时缺省字段按 0。

    仅作用于带 ``configured`` 标志的用户 per_token 规则；defaults/catalog 规则字段完整
    （无 ``configured``），直接返回。
    """
    if rule.get("mode") != "per_token":
        return rule
    configured = rule.get("configured")
    if not isinstance(configured, dict):
        return rule
    fallback = _fallback_per_token(provider_id, model, pricing)
    out = dict(rule)
    for f in _PER_TOKEN_FIELDS:
        if configured.get(f) and out.get(f) is not None:
            continue  # 用户显式提供
        fb = fallback.get(f) if isinstance(fallback, dict) else None
        if fb is not None:
            out[f] = fb
        elif f != "cache_creation":
            out[f] = 0.0  # cache_creation 留 None，由 _cost_per_token 回退 input
    return out


def _fallback_per_token(
    provider_id: str | None, model: str | None, pricing: dict[str, Any]
) -> dict[str, Any] | None:
    """per_token 继承的回落价：候选（catalog per_token）优先，其次内置默认表。"""
    sel = _resolve_selection(provider_id, model, pricing)
    if sel is not None and sel.get("mode") == "per_token":
        return sel
    defaults = pricing.get("defaults") if isinstance(pricing, dict) else None
    if isinstance(defaults, dict):
        d = match_pricing(model, defaults)
        if d:
            return {"mode": "per_token", **d}
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


def _tier_field_prices(usage: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    """per_tier 当前生效字段价：base ⊕ context tier（阈值升序取最后 ``len > threshold``）。"""
    base = rule.get("base") if isinstance(rule.get("base"), dict) else {}
    prices: dict[str, Any] = dict(base)
    total_in = (
        int(usage.get("token_input_other", 0) or 0)
        + int(usage.get("token_input_cached", 0) or 0)
        + int(usage.get("cache_creation") or 0)
    )
    tiers = (x for x in (rule.get("context_tiers") or []) if isinstance(x, dict))
    for t in sorted(tiers, key=lambda x: int(x.get("threshold_tokens", 0) or 0)):
        try:
            thr = int(t.get("threshold_tokens", 0) or 0)
        except (TypeError, ValueError):
            continue
        if total_in > thr:
            for f in _PER_TOKEN_FIELDS:
                if t.get(f) is not None:
                    prices[f] = t[f]
    return prices


def _cost_per_tier(usage: dict[str, Any], rule: dict[str, Any]) -> float:
    """per_tier 模式成本（USD）：context tier 覆盖 + service tier 倍率，再四类 token 计价。"""
    prices = _tier_field_prices(usage, rule)
    # service tier 倍率（来自 billing_context.service_tier 或 usage.service_tier）
    ctx = usage.get("billing_context")
    svc = (ctx.get("service_tier") if isinstance(ctx, dict) else None) or usage.get("service_tier")
    if svc:
        for s in rule.get("service_tiers") or []:
            if not isinstance(s, dict):
                continue
            if str(s.get("match", "")).lower() != str(svc).lower():
                continue
            for f in _PER_TOKEN_FIELDS:
                m = s.get(f + "_multiplier")
                if m is not None and prices.get(f) is not None:
                    prices[f] = float(prices[f]) * float(m)
    input_price = float(prices.get("input") or 0.0)
    cached_price = float(prices.get("input_cached") or 0.0)
    output_price = float(prices.get("output") or 0.0)
    creation_price = float(prices.get("cache_creation") or input_price)
    cost = (
        int(usage.get("token_input_other", 0) or 0) * input_price
        + int(usage.get("token_input_cached", 0) or 0) * cached_price
        + int(usage.get("token_output", 0) or 0) * output_price
        + int(usage.get("cache_creation") or 0) * creation_price
    ) / 1_000_000.0
    return float(cost)


def _cost_tiered_expr(usage: dict[str, Any], rule: dict[str, Any]) -> float:
    """tiered_expr 模式成本（USD）：表达式原始输出 ``/1_000_000``，回填 ``_matched_tier``。"""
    from .expr_eval import eval_tiered_expr

    expr = str(rule.get("expr") or "")
    if not expr:
        return 0.0
    other = int(usage.get("token_input_other", 0) or 0)
    cached = int(usage.get("token_input_cached", 0) or 0)
    output = int(usage.get("token_output", 0) or 0)
    cc = int(usage.get("cache_creation") or 0)
    cc1h = int(usage.get("cache_creation_1h") or 0)
    if cc1h <= 0:
        # DB 聚合路径不单列 cc1h：按 billing_context.cache_ttl_1h 标志从 cc 中拆出
        # （supplement 采集时同口径：命中 1h TTL 时全部缓存写按 1h 单价计）。
        ctx0 = usage.get("billing_context")
        if isinstance(ctx0, dict) and ctx0.get("cache_ttl_1h") and cc > 0:
            cc1h = cc
    # cc1h 是 cc 的子集：从 cc 中扣除，避免表达式 cc*x + cc1h*y 双重计费。
    cc_bill = max(cc - cc1h, 0)
    variables = {
        "p": float(other),
        "c": float(output),
        "len": float(other + cached + cc),
        "cr": float(cached),
        "cc": float(cc_bill),
        "cc1h": float(cc1h),
        "img": 0.0,
        "img_o": 0.0,
        "ai": 0.0,
        "ao": 0.0,
    }
    ctx = usage.get("billing_context")
    context: dict[str, Any] = dict(ctx) if isinstance(ctx, dict) else {}
    if "created_at" not in context and usage.get("created_at") is not None:
        context["created_at"] = usage.get("created_at")
    try:
        raw, matched = eval_tiered_expr(expr, variables, context)
    except Exception:
        return 0.0
    # 聚类倍率（_apply_cluster_multiplier 注入）作用于表达式输出整体；
    # 仅当调用方显式请求回填时写入 _matched_tier，避免污染入参 dict。
    try:
        multiplier = float(rule.get("cluster_multiplier", 1.0) or 1.0)
    except (TypeError, ValueError):
        multiplier = 1.0
    if usage.get("_capture_matched_tier"):
        usage["_matched_tier"] = matched  # 供持久化回填（supplement 显式请求）
    return float(raw) * multiplier / 1_000_000.0


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
    if mode == "per_tier":
        return _cost_per_tier(usage, rule)
    if mode == "tiered_expr":
        return _cost_tiered_expr(usage, rule)
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
    if mode == "per_tier":
        return _cost_per_tier(usage, rule), cur
    if mode == "tiered_expr":
        return _cost_tiered_expr(usage, rule), cur
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
        if mode == "per_tier":
            # 聚合近似：tier 按聚合 len 选择，无 per-record service_tier
            return _cost_per_tier(row, rule)
        if mode == "tiered_expr":
            return _cost_tiered_expr(row, rule)  # 聚合近似
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
        elif mode == "per_tier":
            raw = _cost_per_tier(row, rule)  # 聚合近似
        elif mode == "tiered_expr":
            raw = _cost_tiered_expr(row, rule)  # 聚合近似
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
        """返回当前生效的定价结构，并注入价格目录、候选选择与供应商聚类映射。

        ``catalog``/``selections`` 供 :func:`resolve_pricing` 做候选价格解析；
        ``provider_clusters`` 供 :func:`_apply_cluster_multiplier` 套用聚类倍率；
        目录不可用或损坏时降级为空（仅靠 defaults/user），保证成本计算永不被打断。
        """
        cfg = getattr(self, "cfg", None)
        pricing = get_pricing(cfg)
        pricing["enabled_sources"] = get_enabled_price_sources(cfg)
        try:
            from .config import get_price_selections
            from .price_catalog import load_catalog
            from .pricing_clusters import provider_cluster_map_from_config

            pricing["selections"] = get_price_selections(cfg)
            astrbot_config = self.context.get_config() or {}
            pricing["provider_clusters"] = provider_cluster_map_from_config(astrbot_config)
            data_dir = getattr(self, "_data_dir", None)
            if not data_dir:
                get_data_dir = getattr(self, "get_data_dir", None)
                data_dir = str(get_data_dir()) if callable(get_data_dir) else None
            if data_dir:
                catalog = load_catalog(str(data_dir))
                # flat_prices 带进程级缓存，热路径避免每次全量 to_dict 重建。
                pricing["catalog"] = catalog.flat_prices()
                pricing["catalog_obj"] = catalog
                pricing["auto_selected"] = self._compute_auto_selected(
                    astrbot_config, catalog, pricing
                )
        except Exception:
            pricing.setdefault("provider_clusters", {})
            pass
        return pricing

    def _compute_auto_selected(
        self,
        astrbot_config: dict[str, Any],
        catalog: Any,
        pricing: dict[str, Any],
    ) -> dict[str, dict[str, str]]:
        """按 (provider_id, model) 预算自动匹配价格键，供热路径查表。

        与 /pricing 端点的实时计算同口径：主模型 + model_list 启用项，跳过已确认选择。
        """
        from .price_catalog import select_auto_candidate

        result: dict[str, dict[str, str]] = {}
        selections = pricing.get("selections") or {}
        enabled_sources = pricing.get("enabled_sources")
        source_filter = (
            set(enabled_sources) if isinstance(enabled_sources, (list, set, tuple)) else None
        )
        prov_list = astrbot_config.get("provider") if isinstance(astrbot_config, dict) else None
        if not isinstance(prov_list, list):
            return result
        for p in prov_list:
            try:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("id") or "").strip()
                if not pid:
                    continue
                models: list[str] = []
                main_model = str(p.get("model") or "").strip()
                if main_model:
                    models.append(main_model)
                model_list = p.get("model_list")
                if isinstance(model_list, list):
                    for item in model_list:
                        if not isinstance(item, dict):
                            continue
                        if item.get("enable") is False:
                            continue
                        name = str(item.get("model_name") or item.get("model") or "").strip()
                        legacy_cfg = item.get("model_config")
                        if not name and isinstance(legacy_cfg, dict):
                            name = str(legacy_cfg.get("model") or "").strip()
                        if name and name not in models:
                            models.append(name)
                legacy_model_config = p.get("model_config")
                if isinstance(legacy_model_config, dict):
                    name = str(legacy_model_config.get("model") or "").strip()
                    if name and name not in models:
                        models.append(name)
                per_provider = selections.get(pid) if isinstance(selections, dict) else None
                confirmed = {
                    m
                    for m, sel in (per_provider.items() if isinstance(per_provider, dict) else [])
                    if isinstance(sel, dict) and sel.get("confirmed")
                }
                for model in models:
                    if model in confirmed:
                        continue
                    auto = select_auto_candidate(
                        catalog.find_candidates(model, sources=source_filter)
                    )
                    if auto is not None:
                        result.setdefault(pid, {})[model] = auto.price_key
            except Exception:
                continue
        return result

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
