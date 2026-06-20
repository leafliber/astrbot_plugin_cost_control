"""成本计算单测：定价匹配、三种计费模式、provider_id 优先级（纯函数）。"""

from cost_control.config import DEFAULT_PRICING
from cost_control.cost import (
    compute_cost_grouped,
    compute_cost_value,
    compute_row_cost,
    match_pricing,
    resolve_pricing,
)


def pricing_struct(user=None):
    """构造 get_pricing 返回的 {defaults, user} 结构（测试辅助）。"""
    return {"defaults": {m: dict(p) for m, p in DEFAULT_PRICING.items()}, "user": user or {}}


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
