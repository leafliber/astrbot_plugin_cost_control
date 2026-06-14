"""``CostMixin`` 单测：验证模型单价匹配与 USD 成本计算（纯函数）。"""

from cost_control.config import DEFAULT_PRICING
from cost_control.cost import compute_cost_value, match_pricing


def test_match_pricing_exact():
    prices = match_pricing("gpt-4o", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 2.5


def test_match_pricing_prefix():
    # 带日期后缀的模型名按最长前缀匹配到 claude-sonnet-4-5
    prices = match_pricing("claude-sonnet-4-5-20250929", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 3.0


def test_match_pricing_unknown():
    assert match_pricing("nonexistent-model-xyz", DEFAULT_PRICING) is None


def test_match_pricing_empty():
    assert match_pricing("", DEFAULT_PRICING) is None
    assert match_pricing(None, DEFAULT_PRICING) is None


def test_compute_cost_basic_input():
    usage = {"token_input_other": 1_000_000, "token_input_cached": 0, "token_output": 0}
    cost = compute_cost_value(usage, "gpt-4o", DEFAULT_PRICING)
    assert cost == 2.5  # 1M input * $2.5/M


def test_compute_cost_mixed():
    usage = {
        "token_input_other": 1_000_000,
        "token_input_cached": 1_000_000,
        "token_output": 500_000,
    }
    cost = compute_cost_value(usage, "gpt-4o", DEFAULT_PRICING)
    # 1M*2.5 + 1M*1.25 + 0.5M*10 = 2.5 + 1.25 + 5.0 = 8.75
    assert abs(cost - 8.75) < 1e-9


def test_compute_cost_unknown_model():
    usage = {"token_input_other": 1_000_000, "token_input_cached": 0, "token_output": 0}
    assert compute_cost_value(usage, "nonexistent-model-xyz", DEFAULT_PRICING) == 0.0


def test_compute_cost_cache_creation_anthropic():
    usage = {
        "token_input_other": 0,
        "token_input_cached": 0,
        "token_output": 0,
        "cache_creation": 1_000_000,
    }
    cost = compute_cost_value(usage, "claude-sonnet-4-5", DEFAULT_PRICING)
    assert abs(cost - 3.75) < 1e-9


def test_compute_cost_handles_missing_fields():
    # usage 缺字段时应按 0 处理，不抛异常
    cost = compute_cost_value({}, "gpt-4o", DEFAULT_PRICING)
    assert cost == 0.0
