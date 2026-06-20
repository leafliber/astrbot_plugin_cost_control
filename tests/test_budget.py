"""``BudgetMixin`` / ``ScheduleMixin`` 纯函数单测。

覆盖日 / 月窗口计算、四维阈值比较、token 聚合、cron 表达式转换。
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cost_control.budget import (
    BudgetMixin,
    _groups_cost,
    check_dimensions,
    check_dimensions_dual,
    day_window_start,
    default_on_exceeded,
    get_budget_overrides,
    get_fallback_providers,
    match_override,
    month_window_start,
    resolve_tz,
    total_tokens,
    truncate_contexts,
)
from cost_control.config import (
    DEFAULT_PRICING,
    enabled_fallback_providers,
    enabled_overrides,
    normalize_budget_override,
    normalize_fallback_provider,
)
from cost_control.schedule import hhmm_to_cron

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_day_window_start_after_refresh():
    # 06-14 16:00 UTC = 06-15 00:00 Shanghai；当日 00:00（本地）已过
    now = datetime(2026, 6, 14, 16, 0, tzinfo=UTC)
    assert day_window_start("00:00", now, SHANGHAI) == datetime(2026, 6, 14, 16, 0, tzinfo=UTC)


def test_day_window_start_before_refresh():
    # 06-14 00:00 UTC = 06-14 08:00 Shanghai；本地 09:00 未到，回退昨日 09:00
    # （= 06-13 01:00 UTC）
    now = datetime(2026, 6, 14, 0, 0, tzinfo=UTC)
    assert day_window_start("09:00", now, SHANGHAI) == datetime(2026, 6, 13, 1, 0, tzinfo=UTC)


def test_day_window_start_invalid_fallback():
    now = datetime(2026, 6, 14, 16, 0, tzinfo=UTC)
    assert day_window_start("garbage", now, SHANGHAI) == datetime(2026, 6, 14, 16, 0, tzinfo=UTC)


def test_month_window_start():
    # 06-14 12:30 UTC = 06-14 20:30 Shanghai；本月 1 日 00:00（本地）= 05-31 16:00 UTC
    now = datetime(2026, 6, 14, 12, 30, tzinfo=UTC)
    assert month_window_start(now, SHANGHAI) == datetime(2026, 5, 31, 16, 0, tzinfo=UTC)


def test_check_dimensions_no_limits():
    r = check_dimensions({"per_session_daily": 999}, {"per_session_daily": 0})
    assert r["exceeded"] is False
    assert r["dim"] is None


def test_check_dimensions_first_exceeded_wins():
    used = {
        "per_session_daily": 50,
        "per_model_daily": 200,
        "global_daily": 300,
    }
    limits = {"per_session_daily": 100, "per_model_daily": 150}
    r = check_dimensions(used, limits)
    assert r["exceeded"] is True
    assert r["dim"] == "per_model_daily"
    assert r["used"] == 200
    assert r["limit"] == 150


def test_check_dimensions_local_dim_priority():
    used = {"per_session_daily": 200, "global_daily": 5000}
    limits = {"per_session_daily": 100, "global_daily": 10000}
    r = check_dimensions(used, limits)
    assert r["dim"] == "per_session_daily"


def test_check_dimensions_not_exceeded():
    r = check_dimensions({"per_session_daily": 50}, {"per_session_daily": 100})
    assert r["exceeded"] is False


def test_total_tokens_sums_three_classes():
    usage = {"token_input_other": 100, "token_input_cached": 50, "token_output": 200}
    assert total_tokens(usage) == 350


def test_total_tokens_missing_fields():
    assert total_tokens({}) == 0


def test_hhmm_to_cron():
    assert hhmm_to_cron("09:00") == "0 9 * * *"
    assert hhmm_to_cron("00:30") == "30 0 * * *"


def test_hhmm_to_cron_invalid():
    assert hhmm_to_cron("bad") == "0 9 * * *"


class _FakeCtx:
    """模拟 Context：get_config 返回固定 dict。"""

    def __init__(self, cfg: dict | None) -> None:
        self._cfg = cfg

    def get_config(self, umo: str | None = None):
        return self._cfg


def test_resolve_tz_reads_config():
    assert resolve_tz(_FakeCtx({"timezone": "UTC"})) == ZoneInfo("UTC")
    assert resolve_tz(_FakeCtx({"timezone": "Asia/Tokyo"})) == ZoneInfo("Asia/Tokyo")


def test_resolve_tz_fallback_on_missing_or_bad():
    assert resolve_tz(_FakeCtx({})) == ZoneInfo("Asia/Shanghai")
    assert resolve_tz(_FakeCtx({"timezone": "bogus/zone"})) == ZoneInfo("Asia/Shanghai")
    assert resolve_tz(None) == ZoneInfo("Asia/Shanghai")


# ===== 局部阈值 override 纯函数 =====


def test_normalize_budget_override_valid():
    s = normalize_budget_override(
        {
            "enabled": True,
            "target_type": "umo",
            "target_value": "qq:123",
            "token_limit": 1000,
            "cost_limit": 0.5,
            "on_exceeded": "fallback",
            "fallback_provider_ids": ["p1", "p2"],
            "fallback_token_limit": 500,
        }
    )
    assert s is not None
    assert s["target_type"] == "umo"
    assert s["token_limit"] == 1000
    assert s["cost_limit"] == 0.5
    assert s["on_exceeded"] == "fallback"
    assert s["fallback_provider_ids"] == ["p1", "p2"]
    assert s["fallback_token_limit"] == 500


def test_normalize_budget_override_invalid_target_type_returns_none():
    assert normalize_budget_override({"target_type": "bogus", "target_value": "x"}) is None


def test_normalize_budget_override_empty_target_value_returns_none():
    assert normalize_budget_override({"target_type": "umo", "target_value": ""}) is None
    assert normalize_budget_override({"target_type": "umo"}) is None


def test_normalize_budget_override_non_dict_returns_none():
    assert normalize_budget_override("garbage") is None
    assert normalize_budget_override(None) is None
    assert normalize_budget_override([]) is None


def test_normalize_budget_override_invalid_on_exceeded_falls_back_stop():
    s = normalize_budget_override(
        {"target_type": "provider", "target_value": "p1", "on_exceeded": "nope"}
    )
    assert s["on_exceeded"] == "stop"


def test_normalize_budget_override_negative_limits_clamped():
    s = normalize_budget_override(
        {"target_type": "user", "target_value": "u1", "token_limit": -5, "cost_limit": -1.5}
    )
    assert s["token_limit"] == 0
    assert s["cost_limit"] == 0.0


def test_normalize_budget_override_string_provider_ids_split():
    s = normalize_budget_override(
        {
            "target_type": "umo",
            "target_value": "x",
            "on_exceeded": "fallback",
            "fallback_provider_ids": "a, b ,c",
        }
    )
    assert s["fallback_provider_ids"] == ["a", "b", "c"]


def test_enabled_overrides_filters_and_preserves_order():
    raw = [
        {
            "enabled": True,
            "target_type": "umo",
            "target_value": "a",
            "token_limit": 100,
        },
        {"enabled": False, "target_type": "umo", "target_value": "b", "token_limit": 100},
        {"target_type": "umo", "target_value": "c", "cost_limit": 1.0},  # 缺 enabled → True
        {"target_type": "bogus", "target_value": "d"},  # 非法 target_type → 丢弃
        {"target_type": "umo", "target_value": ""},  # 空 target_value → 丢弃
    ]
    out = enabled_overrides(raw)
    assert [o["target_value"] for o in out] == ["a", "c"]


def test_enabled_overrides_non_list():
    assert enabled_overrides(None) == []
    assert enabled_overrides("nope") == []
    assert enabled_overrides(123) == []


def test_match_override_umo():
    ov = normalize_budget_override({"target_type": "umo", "target_value": "qq:1"})
    assert match_override(ov, "qq:1", user_id="u", provider_id="p") == "qq:1"
    assert match_override(ov, "qq:2", user_id="u", provider_id="p") is None


def test_match_override_provider_requires_id():
    ov = normalize_budget_override({"target_type": "provider", "target_value": "p1"})
    assert match_override(ov, "qq:1", user_id="u", provider_id="p1") == "p1"
    assert match_override(ov, "qq:1", user_id="u", provider_id=None) is None
    assert match_override(ov, "qq:1", user_id="u", provider_id="p2") is None


def test_match_override_user_requires_id():
    ov = normalize_budget_override({"target_type": "user", "target_value": "u1"})
    assert match_override(ov, "qq:1", user_id="u1", provider_id="p") == "u1"
    assert match_override(ov, "qq:1", user_id=None, provider_id="p") is None
    assert match_override(ov, "qq:1", user_id="u2", provider_id="p") is None


def test_match_override_garbage():
    assert match_override(None, "umo", None, None) is None
    assert match_override({}, "umo", None, None) is None


def test_default_on_exceeded_validates():
    assert default_on_exceeded({"default_on_exceeded": "stop"}) == "stop"
    assert default_on_exceeded({"default_on_exceeded": "fallback"}) == "fallback"
    assert default_on_exceeded({"default_on_exceeded": "warn"}) == "warn"
    assert default_on_exceeded({"default_on_exceeded": "bogus"}) == "stop"
    assert default_on_exceeded({}) == "stop"
    assert default_on_exceeded(None) == "stop"


# ===== 备用 Provider 库 =====


def test_normalize_fallback_provider_valid():
    p = normalize_fallback_provider({"id": "p1", "enabled": True, "note": "本地"})
    assert p == {"id": "p1", "enabled": True, "note": "本地"}


def test_normalize_fallback_provider_invalid():
    assert normalize_fallback_provider({}) is None
    assert normalize_fallback_provider({"id": ""}) is None
    assert normalize_fallback_provider(None) is None
    assert normalize_fallback_provider("nope") is None


def test_enabled_fallback_providers_filters_disabled():
    raw = [
        {"id": "a", "enabled": True},
        {"id": "b", "enabled": False},
        {"id": "c"},  # 默认 enabled
    ]
    out = enabled_fallback_providers(raw)
    assert [p["id"] for p in out] == ["a", "c"]


def test_get_budget_overrides_and_fallbacks_from_cfg():
    cfg = {
        "budget_overrides": [
            {"enabled": True, "target_type": "umo", "target_value": "x", "token_limit": 1},
            {"enabled": False, "target_type": "umo", "target_value": "y", "token_limit": 1},
        ],
        "fallback_providers": [{"id": "p1", "enabled": True}],
    }
    assert len(get_budget_overrides(cfg)) == 1
    assert get_fallback_providers(cfg) == [{"id": "p1", "enabled": True, "note": ""}]
    assert get_budget_overrides(None) == []
    assert get_fallback_providers(None) == []


def test_truncate_contexts_no_limit_returns_all():
    ctx = ["a", "b", "c"]
    assert truncate_contexts(ctx, 0) == ["a", "b", "c"]


def test_truncate_contexts_keeps_recent_within_limit():
    # 每条约 100 token（400 ASCII 字符 × 0.25）
    a, b, c = "a" * 400, "b" * 400, "c" * 400
    out = truncate_contexts([a, b, c], 250)
    # 总 300 > 250，保留最近两条（b、c），原序
    assert out == [b, c]


def test_truncate_contexts_empty():
    assert truncate_contexts([], 100) == []
    assert truncate_contexts(None, 100) == []


# ===== 预算双指标（token / cost） =====


def test_check_dimensions_dual_token_exceeded():
    r = check_dimensions_dual(
        {"global_daily": 200}, {"global_daily": 0}, {"global_daily": 100}, {"global_daily": 0}
    )
    assert r["exceeded"] is True
    assert r["dim"] == "global_daily"
    assert r["metric"] == "token"
    assert r["used"] == 200 and r["limit"] == 100


def test_check_dimensions_dual_cost_exceeded():
    r = check_dimensions_dual(
        {"global_daily": 0}, {"global_daily": 6.0}, {"global_daily": 0}, {"global_daily": 5.0}
    )
    assert r["exceeded"] is True
    assert r["metric"] == "cost"
    assert r["limit"] == 5.0


def test_check_dimensions_dual_both_token_wins():
    # 同维 token 与 cost 都超 → 优先报 token
    r = check_dimensions_dual(
        {"global_daily": 200}, {"global_daily": 9.0}, {"global_daily": 100}, {"global_daily": 5.0}
    )
    assert r["metric"] == "token"


def test_check_dimensions_dual_neither():
    r = check_dimensions_dual(
        {"global_daily": 50}, {"global_daily": 1.0}, {"global_daily": 100}, {"global_daily": 5.0}
    )
    assert r["exceeded"] is False
    assert r["dim"] is None and r["metric"] is None


def test_check_dimensions_dual_dim_priority():
    # per_session（更局部）token 超 优先于 global cost 超
    r = check_dimensions_dual(
        {"per_session_daily": 200, "global_daily": 0},
        {"per_session_daily": 0, "global_daily": 9.0},
        {"per_session_daily": 100, "global_daily": 0},
        {"per_session_daily": 0, "global_daily": 5.0},
    )
    assert r["dim"] == "per_session_daily"
    assert r["metric"] == "token"


def test_check_dimensions_dual_zero_limits_skipped():
    r = check_dimensions_dual(
        {"global_daily": 9999}, {"global_daily": 9999.0}, {"global_daily": 0}, {"global_daily": 0}
    )
    assert r["exceeded"] is False


def test_groups_cost_multi_model():
    groups = [
        {
            "key": "gpt-4o",
            "token_input_other": 1_000_000,
            "token_input_cached": 0,
            "token_output": 0,
        },
        {
            "key": "gpt-4o-mini",
            "token_input_other": 1_000_000,
            "token_input_cached": 0,
            "token_output": 0,
        },
    ]
    # gpt-4o 1M input = $2.5；gpt-4o-mini 1M input = $0.15 → 合计 $2.65
    assert abs(_groups_cost(groups, DEFAULT_PRICING) - 2.65) < 1e-6


def test_groups_cost_unpriced_zero():
    groups = [
        {
            "key": "nonexistent-xyz",
            "token_input_other": 1_000_000,
            "token_input_cached": 0,
            "token_output": 0,
        },
    ]
    assert _groups_cost(groups, DEFAULT_PRICING) == 0.0


def test_groups_cost_empty():
    assert _groups_cost([], DEFAULT_PRICING) == 0.0


class _BudgetStub(BudgetMixin):
    """仅带 config 的 BudgetMixin 实例（测 get_budgets_cost，不触 DB/context）。"""


def test_get_budgets_cost_defaults():
    b = _BudgetStub()
    b.cfg = {}
    out = b.get_budgets_cost()
    assert set(out.keys()) == {
        "per_session_daily",
        "per_user_daily",
        "per_model_daily",
        "global_daily",
        "global_monthly",
    }
    assert all(v == 0.0 for v in out.values())


def test_get_budgets_cost_override_and_bad():
    b = _BudgetStub()
    b.cfg = {"budgets_cost": {"global_daily": 5.5, "per_model_daily": "abc"}}
    out = b.get_budgets_cost()
    assert out["global_daily"] == 5.5
    assert out["per_model_daily"] == 0.0  # 非法值跳过，保留默认 0.0
