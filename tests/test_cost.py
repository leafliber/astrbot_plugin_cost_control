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


def test_match_pricing_keyword_substring():
    # 实际调用名带厂商前缀 / 变体后缀，前缀匹配不到时靠关键词子串模糊命中预设
    prices = match_pricing("dashscope/qwen3-max-20241115", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 0.78


def test_match_pricing_case_insensitive():
    # 大小写不敏感：精确 / 前缀 / 子串各级均支持
    assert match_pricing("QWEN3-MAX", DEFAULT_PRICING)["input"] == 0.78
    assert match_pricing("GLM-4.5", DEFAULT_PRICING)["input"] == 0.6


def test_match_pricing_longest_wins():
    # 多个 key 同时命中时取最长（最具体）：gpt-4o-mini 优先于 gpt-4o
    prices = match_pricing("gpt-4o-mini-2024-07-18", DEFAULT_PRICING)
    assert prices is not None
    assert prices["input"] == 0.15


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
    # gpt-4o-mini: input 0.15 / cached 0.075 / output 0.6
    cost = compute_cost_value(usage, "gpt-4o-mini", DEFAULT_PRICING)
    # 1M*0.15 + 1M*0.075 + 0.5M*0.6 = 0.15 + 0.075 + 0.30 = 0.525
    assert abs(cost - 0.525) < 1e-9


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
