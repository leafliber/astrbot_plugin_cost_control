"""成本计算单测：定价匹配、三种计费模式、provider_id 优先级（纯函数）。"""

from __future__ import annotations

import pytest

from cost_control.config import DEFAULT_PRICING
from cost_control.cost import (
    compute_cost_grouped,
    compute_cost_value,
    compute_row_cost,
    match_pricing,
    resolve_pricing,
)


def pricing_struct(user=None, multipliers=None, provider_clusters=None):
    """构造 get_pricing 返回的定价结构（测试辅助）。"""
    return {
        "defaults": {m: dict(p) for m, p in DEFAULT_PRICING.items()},
        "user": user or {},
        "multipliers": multipliers or {},
        "provider_clusters": provider_clusters or {},
    }


# ===== match_pricing（仅作用于 defaults 表，签名未变）=====


def test_match_pricing_exact():
    prices = match_pricing("gpt-4o", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 2.5


def test_match_pricing_prefix():
    prices = match_pricing("claude-sonnet-4-5-20250929", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 3.0


def test_match_pricing_unknown():
    assert match_pricing("nonexistent-model-xyz", DEFAULT_PRICING) is None


def test_match_pricing_empty():
    assert match_pricing("", DEFAULT_PRICING) is None
    assert match_pricing(None, DEFAULT_PRICING) is None


def test_match_pricing_keyword_substring():
    prices = match_pricing("dashscope/qwen3-max-20241115", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 0.78


def test_match_pricing_case_insensitive():
    assert match_pricing("QWEN3-MAX", DEFAULT_PRICING)["input"] == 0.78
    assert match_pricing("GLM-4.5", DEFAULT_PRICING)["input"] == 0.6


def test_match_pricing_longest_wins():
    prices = match_pricing("gpt-4o-mini-2024-07-18", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 0.15


# ===== 模糊匹配：各种写法变体（厂商前缀 / 命名空间 / 分隔符 / 版本号对齐）=====


def test_match_pricing_vendor_prefix():
    # OpenRouter / NewAPI 风格：厂商前缀 + 大小写混合
    assert match_pricing("minimax/MiniMax-M2.7", DEFAULT_PRICING)["input"] == 0.3
    assert match_pricing("deepseek/deepseek-v4-pro", DEFAULT_PRICING)["input"] == 0.572808


def test_match_pricing_nested_namespace():
    # 多层命名空间前缀：剥到最后一段
    assert match_pricing("newapi/moonshotai/kimi-k2.6", DEFAULT_PRICING)["input"] == 0.95


def test_match_pricing_underscore_and_space():
    # 下划线 / 空格分隔统一为连字符
    assert match_pricing("MiniMax_M2.7", DEFAULT_PRICING)["input"] == 0.3
    assert match_pricing("MiniMax M2.7", DEFAULT_PRICING)["input"] == 0.3


def test_match_pricing_version_dot_alignment():
    # 版本号写法对齐：4-5（连字符）↔ 4.5（点）；带厂商前缀 + 日期后缀同时成立
    assert match_pricing("anthropic/claude-sonnet-4-5-20250929", DEFAULT_PRICING)["input"] == 3.0


# ===== resolve_pricing（provider_id 优先 > 模型名默认 > 未定价）=====


def test_resolve_user_per_token_wins_over_default():
    user = {"prov_x": {"mode": "per_token", "input": 9.9, "input_cached": 0, "output": 0}}
    rule = resolve_pricing("prov_x", "gpt-4o", pricing_struct(user))
    assert rule is not None
    assert rule["mode"] == "per_token"
    assert rule["input"] == 9.9  # 用户价覆盖默认 2.5


def test_resolve_default_when_no_user():
    rule = resolve_pricing(None, "gpt-4o", pricing_struct())
    assert rule is not None
    assert rule["mode"] == "per_token"
    assert rule["input"] == 2.5


def test_resolve_unpriced():
    assert resolve_pricing("unknown_prov", "nonexistent-model-xyz", pricing_struct()) is None


def test_resolve_per_turn():
    user = {"prov_x": {"mode": "per_turn", "price": 0.01}}
    rule = resolve_pricing("prov_x", "anything", pricing_struct(user))
    assert rule == {"mode": "per_turn", "price": 0.01}


# ===== resolve_pricing 的 provider_id 模糊匹配（精确 > 前缀 > 子串，最长优先）=====


def test_resolve_user_provider_id_prefix():
    user = {"deepseek": {"mode": "per_turn", "price": 0.02}}
    # provider_id 以配置 key 开头（带后缀），前缀匹配命中
    rule = resolve_pricing("deepseek-official-01", "anything", pricing_struct(user))
    assert rule == {"mode": "per_turn", "price": 0.02}


def test_resolve_user_provider_id_substring():
    user = {"glm": {"mode": "per_token", "input": 5.0, "input_cached": 0, "output": 0}}
    # provider_id 包含配置 key 作为子串
    rule = resolve_pricing("my-zhipu-glm-provider", "anything", pricing_struct(user))
    assert rule is not None
    assert rule["input"] == 5.0


def test_resolve_user_provider_id_longest_wins():
    user = {
        "gpt": {"mode": "per_turn", "price": 0.001},
        "gpt-4": {"mode": "per_turn", "price": 0.01},
    }
    # 两个 key 都是前缀，取最长（最具体）的 gpt-4
    rule = resolve_pricing("gpt-4o-mini-proc", "anything", pricing_struct(user))
    assert rule == {"mode": "per_turn", "price": 0.01}


def test_resolve_user_provider_id_case_insensitive():
    user = {"openai": {"mode": "per_turn", "price": 0.03}}
    # 前缀 / 子串匹配大小写不敏感
    rule = resolve_pricing("OpenAI-Prod", "anything", pricing_struct(user))
    assert rule == {"mode": "per_turn", "price": 0.03}


def test_resolve_user_provider_id_no_false_substring():
    # 配置 key 是 provider_id 的超集时不应误匹配（仅 key-in-provider_id 方向）
    user = {"openai-chat-prod": {"mode": "per_turn", "price": 0.05}}
    assert resolve_pricing("openai", "anything", pricing_struct(user)) is None


# ===== compute_cost_value（新签名：usage, provider_id, model, pricing）=====


def test_compute_cost_basic_input():
    usage = {"token_input_other": 1_000_000, "token_input_cached": 0, "token_output": 0}
    cost = compute_cost_value(usage, None, "gpt-4o", pricing_struct())
    assert cost == 2.5


def test_compute_cost_mixed():
    usage = {
        "token_input_other": 1_000_000,
        "token_input_cached": 1_000_000,
        "token_output": 500_000,
    }
    cost = compute_cost_value(usage, None, "gpt-4o-mini", pricing_struct())
    assert abs(cost - 0.525) < 1e-9


def test_compute_cost_unknown_model():
    usage = {"token_input_other": 1_000_000, "token_input_cached": 0, "token_output": 0}
    assert compute_cost_value(usage, None, "nonexistent-model-xyz", pricing_struct()) == 0.0


def test_compute_cost_cache_creation_anthropic():
    usage = {
        "token_input_other": 0,
        "token_input_cached": 0,
        "token_output": 0,
        "cache_creation": 1_000_000,
    }
    cost = compute_cost_value(usage, None, "claude-sonnet-4-5", pricing_struct())
    assert abs(cost - 3.75) < 1e-9


def test_compute_cost_handles_missing_fields():
    assert compute_cost_value({}, None, "gpt-4o", pricing_struct()) == 0.0


def test_compute_cost_per_turn_single_row():
    # per_turn 单条 = 1 次 → price
    user = {"prov_x": {"mode": "per_turn", "price": 0.02}}
    cost = compute_cost_value({}, "prov_x", "any", pricing_struct(user))
    assert cost == 0.02


def test_compute_cost_per_request_single_row_is_zero():
    # per_request 单条无法独立计费 → 0（需 distinct request_id 聚合）
    user = {"prov_x": {"mode": "per_request", "price": 0.05}}
    cost = compute_cost_value({}, "prov_x", "any", pricing_struct(user))
    assert cost == 0.0


def test_compute_cost_applies_default_model_cluster_multiplier():
    usage = {"token_input_other": 1_000_000, "token_input_cached": 0, "token_output": 0}
    cost = compute_cost_value(
        usage,
        "gpt-primary",
        "gpt-4o",
        pricing_struct(
            multipliers={"source-main": 1.5},
            provider_clusters={"gpt-primary": "source-main"},
        ),
    )
    assert cost == 3.75


def test_compute_cost_applies_cluster_multiplier_after_user_override():
    user = {"prov_x": {"mode": "per_turn", "price": 0.02}}
    cost = compute_cost_value(
        {},
        "prov_x",
        "anthropic/claude-sonnet-4.5",
        pricing_struct(user, {"source-main": 2}, {"prov_x": "source-main"}),
    )
    assert cost == 0.04


# ===== compute_row_cost / compute_cost_grouped（聚合行）=====


def test_compute_row_cost_per_turn_uses_count():
    user = {"prov_x": {"mode": "per_turn", "price": 0.01}}
    row = {"provider_id": "prov_x", "provider_model": "m", "count": 7}
    assert compute_row_cost(row, pricing_struct(user)) == 0.07


def test_compute_row_cost_per_token():
    row = {
        "provider_id": None,
        "provider_model": "gpt-4o",
        "count": 3,
        "token_input_other": 1_000_000,
        "token_input_cached": 0,
        "token_output": 0,
    }
    assert compute_row_cost(row, pricing_struct()) == 2.5


def test_compute_cost_grouped_mixed():
    user = {"prov_x": {"mode": "per_turn", "price": 0.01}}
    rows = [
        {
            "provider_id": None,
            "provider_model": "gpt-4o",
            "count": 1,
            "token_input_other": 1_000_000,
            "token_input_cached": 0,
            "token_output": 0,
        },
        {"provider_id": "prov_x", "provider_model": "m", "count": 10},
    ]
    # 2.5 (per_token) + 10*0.01 (per_turn)
    assert abs(compute_cost_grouped(rows, pricing_struct(user)) - 2.6) < 1e-9


# ===== 多源价格目录：五级优先级链 + 继承 + 新计费模式 =====


def pricing_full(user=None, catalog=None, selections=None):
    """带 catalog/selections 的完整 pricing 结构（测试辅助）。"""
    p = pricing_struct(user)
    p["catalog"] = catalog or {}
    p["selections"] = selections or {}
    return p


def _per_token_partial(**kw):
    """构造只显式配部分字段的用户 per_token 规则（含 configured 标志）。"""
    cfg = {f: (f in kw) for f in ("input", "input_cached", "output", "cache_creation")}
    rule = {"mode": "per_token", "configured": cfg}
    for f in cfg:
        rule[f] = kw.get(f)
    return rule


# ---- 五级优先级 ----


def test_priority_model_scoped_manual_beats_selection():
    user = {"prov|gpt-4o": {"mode": "per_turn", "price": 0.5}}
    catalog = {"or:gpt-4o": {"mode": "per_token", "input": 1.0, "output": 2.0}}
    selections = {"prov": {"gpt-4o": {"price_key": "or:gpt-4o"}}}
    rule = resolve_pricing("prov", "gpt-4o", pricing_full(user, catalog, selections))
    assert rule["mode"] == "per_turn" and rule["price"] == 0.5


def test_priority_selection_beats_legacy_manual():
    user = {"prov": {"mode": "per_turn", "price": 0.5}}
    catalog = {"or:gpt-4o": {"mode": "per_token", "input": 1.0, "output": 2.0}}
    selections = {"prov": {"gpt-4o": {"price_key": "or:gpt-4o"}}}
    rule = resolve_pricing("prov", "gpt-4o", pricing_full(user, catalog, selections))
    assert rule["mode"] == "per_token" and rule["input"] == 1.0


def test_priority_legacy_manual_beats_default():
    user = {"prov": {"mode": "per_turn", "price": 0.5}}
    rule = resolve_pricing("prov", "gpt-4o", pricing_full(user))
    assert rule["mode"] == "per_turn"


def test_priority_default_fallback():
    rule = resolve_pricing("prov", "gpt-4o", pricing_full())
    assert rule["mode"] == "per_token" and rule["input"] == 2.5


def test_selection_effective_without_user_pricing():
    """无任何手工定价时，已确认的候选选择仍必须生效（回归：曾因嵌在 user 判断内失效）。"""
    catalog = {"or:gpt-4o": {"mode": "per_token", "input": 1.0, "output": 2.0}}
    selections = {
        "prov": {
            "gpt-4o": {
                "price_key": "or:gpt-4o",
                "confirmed": True,
                "auto": False,
                "score": 1,
                "reason": "exact",
            }
        }
    }
    rule = resolve_pricing("prov", "gpt-4o", pricing_full(None, catalog, selections))
    assert rule is not None
    assert rule["mode"] == "per_token" and rule["input"] == 1.0


def test_model_scoped_key_not_fuzzy_matched():
    # "prov|gpt-4o" 是模型级精确 key，不应被 provider 模糊匹配命中其它模型
    user = {"prov|gpt-4o": {"mode": "per_turn", "price": 0.5}}
    assert resolve_pricing("prov", "zzz-no-such-model", pricing_full(user)) is None


def test_selection_missing_price_key_returns_none_then_default():
    # selection 指向的 price_key 不在 catalog → 跳过候选，落到默认表
    selections = {"prov": {"gpt-4o": {"price_key": "or:missing"}}}
    rule = resolve_pricing("prov", "gpt-4o", pricing_full(selections=selections))
    assert rule["mode"] == "per_token" and rule["input"] == 2.5


# ---- per_token 部分字段继承（AC7）----


def test_per_token_partial_inherits_from_default():
    user = {"prov": _per_token_partial(input=9.9)}
    usage = {"token_input_other": 1_000_000, "token_output": 1_000_000}
    # input 1M*9.9 + output 1M*10.0(继承默认) = 19.9
    cost = compute_cost_value(usage, "prov", "gpt-4o", pricing_full(user))
    assert abs(cost - 19.9) < 1e-9


def test_per_token_model_scoped_inherits_from_selection():
    # 模型级手工价 input 生效，output 从候选继承 2.0（候选优先于默认）
    user = {"prov|gpt-4o": _per_token_partial(input=9.9)}
    catalog = {"or:gpt-4o": {"mode": "per_token", "input": 1.0, "output": 2.0}}
    selections = {"prov": {"gpt-4o": {"price_key": "or:gpt-4o"}}}
    usage = {"token_input_other": 1_000_000, "token_output": 1_000_000}
    cost = compute_cost_value(usage, "prov", "gpt-4o", pricing_full(user, catalog, selections))
    assert abs(cost - 11.9) < 1e-9  # 9.9 + 2.0


def test_per_token_explicit_zero_not_inherited():
    # 显式 0 视为已配置，不继承
    user = {"prov": _per_token_partial(input=9.9, output=0.0)}
    usage = {"token_input_other": 1_000_000, "token_output": 1_000_000}
    cost = compute_cost_value(usage, "prov", "gpt-4o", pricing_full(user))
    assert abs(cost - 9.9) < 1e-9  # output 保持 0


def test_per_token_no_fallback_zero():
    # 无候选无默认 → 未配置字段按 0（旧行为）
    user = {"prov": _per_token_partial(input=9.9)}
    usage = {"token_input_other": 1_000_000, "token_output": 1_000_000}
    cost = compute_cost_value(usage, "prov", "zzz-no-such", pricing_full(user))
    assert abs(cost - 9.9) < 1e-9  # 仅 input 计费


# ---- per_tier ----


def _per_tier_rule(base, context_tiers=None, service_tiers=None):
    return {
        "mode": "per_tier",
        "base": base,
        "context_tiers": context_tiers or [],
        "service_tiers": service_tiers or [],
    }


def test_per_tier_context_tier_override():
    rule = _per_tier_rule(
        {"input": 1.0, "output": 2.0},
        context_tiers=[{"threshold_tokens": 1000, "input": 5.0, "output": 10.0}],
    )
    user = {"prov": rule}
    short = compute_cost_value(
        {"token_input_other": 500, "token_output": 100}, "prov", "m", pricing_full(user)
    )
    assert abs(short - (500 * 1.0 + 100 * 2.0) / 1e6) < 1e-12
    long = compute_cost_value(
        {"token_input_other": 2000, "token_output": 100}, "prov", "m", pricing_full(user)
    )
    assert abs(long - (2000 * 5.0 + 100 * 10.0) / 1e6) < 1e-12


def test_per_tier_service_tier_multiplier():
    rule = _per_tier_rule(
        {"input": 1.0, "output": 2.0},
        service_tiers=[{"match": "priority", "input_multiplier": 2.0, "output_multiplier": 2.0}],
    )
    user = {"prov": rule}
    base = compute_cost_value(
        {"token_input_other": 1000, "token_output": 100}, "prov", "m", pricing_full(user)
    )
    assert abs(base - (1000 * 1.0 + 100 * 2.0) / 1e6) < 1e-12
    prio = compute_cost_value(
        {
            "token_input_other": 1000,
            "token_output": 100,
            "billing_context": {"service_tier": "priority"},
        },
        "prov",
        "m",
        pricing_full(user),
    )
    assert abs(prio - (1000 * 2.0 + 100 * 4.0) / 1e6) < 1e-12


def test_per_tier_row_aggregate():
    user = {"prov": _per_tier_rule({"input": 1.0, "output": 2.0})}
    row = {
        "provider_id": "prov",
        "provider_model": "m",
        "count": 3,
        "token_input_other": 1_000_000,
        "token_output": 0,
    }
    assert abs(compute_row_cost(row, pricing_full(user)) - 1.0) < 1e-9


# ---- tiered_expr ----


def test_tiered_expr_computation_and_tier_backfill():
    user = {"prov": {"mode": "tiered_expr", "expr": 'tier("std", p*1.5 + c*7.5)'}}
    # _matched_tier 仅在调用方显式请求时回填（不污染外来 dict，如聚合行）
    usage = {"token_input_other": 100000, "token_output": 5000, "_capture_matched_tier": True}
    cost = compute_cost_value(usage, "prov", "m", pricing_full(user))
    assert abs(cost - (100000 * 1.5 + 5000 * 7.5) / 1e6) < 1e-12
    assert usage["_matched_tier"] == "std"
    # 未请求回填的 usage dict 不被写入
    plain = {"token_input_other": 100000, "token_output": 5000}
    compute_cost_value(plain, "prov", "m", pricing_full(user))
    assert "_matched_tier" not in plain


def test_tiered_expr_invalid_returns_zero():
    user = {"prov": {"mode": "tiered_expr", "expr": "p * * c"}}
    usage = {"token_input_other": 100}
    assert compute_cost_value(usage, "prov", "m", pricing_full(user)) == 0.0


def test_tiered_expr_row_aggregate():
    user = {"prov": {"mode": "tiered_expr", "expr": "p * 2"}}
    row = {
        "provider_id": "prov",
        "provider_model": "m",
        "count": 1,
        "token_input_other": 1_000_000,
        "token_output": 0,
    }
    assert abs(compute_row_cost(row, pricing_full(user)) - 2.0) < 1e-9


def test_unique_catalog_candidate_applies_without_persisted_selection():
    """唯一高置信候选应参与实际结算，不只在定价页面展示。"""
    from cost_control.price_catalog import CatalogPrice, PriceCatalog

    price = CatalogPrice(
        source="newapi:gateway",
        source_model_id="private-model",
        prompt=3.0,
        completion=7.0,
    )
    catalog = PriceCatalog(prices={price.price_key: price})
    pricing = pricing_struct()
    pricing["catalog"] = {price.price_key: price.to_dict()}
    pricing["catalog_obj"] = catalog
    usage = {"token_input_other": 1_000_000, "token_output": 1_000_000}
    assert compute_cost_value(usage, "prov", "private-model", pricing) == 10.0


def test_ambiguous_catalog_candidates_do_not_auto_apply():
    """两个高置信候选不能自动选择，必须留给用户在 WebUI 中确认。"""
    from cost_control.price_catalog import CatalogPrice, PriceCatalog

    first = CatalogPrice(source="modelsdev", source_model_id="private-model", prompt=3.0)
    second = CatalogPrice(source="openrouter", source_model_id="private-model", prompt=4.0)
    catalog = PriceCatalog(prices={first.price_key: first, second.price_key: second})
    pricing = pricing_struct()
    pricing["catalog"] = {
        first.price_key: first.to_dict(),
        second.price_key: second.to_dict(),
    }
    pricing["catalog_obj"] = catalog
    assert resolve_pricing("prov", "private-model", pricing) is None


def test_disabled_source_does_not_auto_apply_but_confirmed_selection_still_applies():
    """停用源不自动匹配，但历史确认选择保持有效。"""
    from cost_control.price_catalog import CatalogPrice, PriceCatalog

    price = CatalogPrice(
        source="openrouter", source_model_id="private-model", prompt=3.0, completion=7.0
    )
    catalog = PriceCatalog(prices={price.price_key: price})
    pricing = pricing_struct()
    pricing["catalog"] = {price.price_key: price.to_dict()}
    pricing["catalog_obj"] = catalog
    pricing["enabled_sources"] = []
    assert resolve_pricing("prov", "private-model", pricing) is None

    pricing["selections"] = {
        "prov": {"private-model": {"price_key": price.price_key, "confirmed": True}}
    }
    rule = resolve_pricing("prov", "private-model", pricing)
    assert rule is not None
    assert rule["input"] == 3.0


# ===== 审计修复回归 =====


def test_cluster_multiplier_applies_to_per_tier():
    """聚类倍率须作用于 per_tier 的 base 与 context_tiers（命中层级统一套用）。"""
    user = {
        "prov": {
            "mode": "per_tier",
            "base": {"input": 2.0, "output": 4.0},
            "context_tiers": [{"threshold_tokens": 1000, "input": 1.0}],
        }
    }
    pricing = pricing_struct(user, multipliers={"c1": 2.0}, provider_clusters={"prov": "c1"})
    rule = resolve_pricing("prov", "m", pricing)
    assert rule["base"]["input"] == pytest.approx(4.0)
    assert rule["base"]["output"] == pytest.approx(8.0)
    assert rule["context_tiers"][0]["input"] == pytest.approx(2.0)


def test_cluster_multiplier_applies_to_tiered_expr():
    """聚类倍率须作用于 tiered_expr 的表达式输出整体。"""
    user = {"prov": {"mode": "tiered_expr", "expr": "p * 2"}}
    pricing = pricing_struct(user, multipliers={"c1": 2.0}, provider_clusters={"prov": "c1"})
    usage = {"token_input_other": 1_000_000}
    assert compute_cost_value(usage, "prov", "m", pricing) == pytest.approx(4.0)


def test_auto_candidate_after_legacy_manual_price():
    """未确认的自动候选不得覆盖存量 provider 级手工价；确认选择仍优先。"""
    from cost_control.price_catalog import CatalogPrice, PriceCatalog

    price = CatalogPrice(source="modelsdev", source_model_id="m", prompt=10.0, completion=20.0)
    cat = PriceCatalog()
    cat.prices[price.price_key] = price
    user = {"prov": {"mode": "per_token", "input": 1.0, "output": 1.0}}

    pricing = pricing_full(user)
    pricing["catalog"] = cat.flat_prices()
    pricing["catalog_obj"] = cat
    rule = resolve_pricing("prov", "m", pricing)
    assert rule is not None and rule["input"] == pytest.approx(1.0)  # legacy 手工价胜出

    pricing2 = pricing_full(user)
    pricing2["catalog"] = cat.flat_prices()
    pricing2["catalog_obj"] = cat
    pricing2["selections"] = {"prov": {"m": {"price_key": price.price_key}}}
    rule2 = resolve_pricing("prov", "m", pricing2)
    assert rule2 is not None and rule2["input"] == pytest.approx(10.0)  # 确认选择最优先


def test_auto_candidate_applies_when_no_manual_price():
    """无手工价时自动候选（唯一高置信）仍生效。"""
    from cost_control.price_catalog import CatalogPrice, PriceCatalog

    price = CatalogPrice(source="modelsdev", source_model_id="m", prompt=10.0, completion=20.0)
    cat = PriceCatalog()
    cat.prices[price.price_key] = price
    pricing = pricing_full({})
    pricing["catalog"] = cat.flat_prices()
    pricing["catalog_obj"] = cat
    rule = resolve_pricing("prov", "m", pricing)
    assert rule is not None and rule["input"] == pytest.approx(10.0)


def test_tiered_expr_no_cc1h_double_billing():
    """cc1h 是 cc 的子集，表达式 cc*x + cc1h*y 不得重复计费。"""
    user = {"prov": {"mode": "tiered_expr", "expr": "cc * 1.25 + cc1h * 2"}}
    usage = {"token_input_other": 0, "cache_creation": 1000, "cache_creation_1h": 1000}
    cost = compute_cost_value(usage, "prov", "m", pricing_full(user))
    assert cost == pytest.approx(1000 * 2 / 1e6)


def test_tiered_expr_derives_cc1h_from_billing_context():
    """DB 聚合路径不单列 cc1h：按 billing_context.cache_ttl_1h 从 cc 拆出。"""
    user = {"prov": {"mode": "tiered_expr", "expr": "cc * 1.25 + cc1h * 2"}}
    usage = {
        "token_input_other": 0,
        "cache_creation": 1000,
        "billing_context": {"cache_ttl_1h": True},
    }
    cost = compute_cost_value(usage, "prov", "m", pricing_full(user))
    assert cost == pytest.approx(1000 * 2 / 1e6)
