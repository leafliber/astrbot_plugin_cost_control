"""``BudgetMixin`` / ``ScheduleMixin`` 纯函数单测。

覆盖日 / 月窗口计算、四维阈值比较、token 聚合、cron 表达式转换。
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cost_control.budget import (
    check_dimensions,
    day_window_start,
    month_window_start,
    resolve_tz,
    total_tokens,
    truncate_contexts,
)
from cost_control.config import (
    enabled_strategies,
    migrate_legacy_policy,
    normalize_strategy,
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


# ===== 超限策略链纯函数 =====


def test_normalize_strategy_valid():
    s = normalize_strategy(
        {
            "action": "fallback_provider",
            "provider_ids": ["p1", "p2"],
            "token_limit": 1000,
            "message": "x",
            "enabled": False,
        }
    )
    assert s["action"] == "fallback_provider"
    assert s["provider_ids"] == ["p1", "p2"]
    assert s["token_limit"] == 1000
    assert s["message"] == "x"
    assert s["enabled"] is False


def test_normalize_strategy_non_dict_defaults_stop():
    s = normalize_strategy("garbage")
    assert s["action"] == "stop_llm"
    assert s["provider_ids"] == []
    assert s["token_limit"] == 0
    assert s["enabled"] is True


def test_normalize_strategy_invalid_action_falls_back():
    s = normalize_strategy({"action": "bogus"})
    assert s["action"] == "stop_llm"


def test_normalize_strategy_provider_ids_from_string():
    assert normalize_strategy({"provider_ids": "a, b ,c"})["provider_ids"] == ["a", "b", "c"]


def test_normalize_strategy_provider_ids_from_list():
    assert normalize_strategy({"provider_ids": ["x", 1, "", "  "]})["provider_ids"] == ["x", "1"]


def test_normalize_strategy_token_limit_bad_to_zero():
    assert normalize_strategy({"token_limit": "abc"})["token_limit"] == 0
    assert normalize_strategy({"token_limit": -5})["token_limit"] == 0


def test_migrate_legacy_policy_fallback():
    out = migrate_legacy_policy(
        {
            "action": "fallback_provider",
            "fallback_provider_id": "p1",
            "fallback_token_limit": 2000,
        }
    )
    assert len(out) == 1
    assert out[0]["action"] == "fallback_provider"
    assert out[0]["provider_ids"] == ["p1"]
    assert out[0]["token_limit"] == 2000


def test_migrate_legacy_policy_fallback_no_id():
    out = migrate_legacy_policy({"action": "fallback_provider"})
    assert out[0]["provider_ids"] == []


def test_migrate_legacy_policy_stop():
    out = migrate_legacy_policy({"action": "stop_llm"})
    assert len(out) == 1
    assert out[0]["action"] == "stop_llm"


def test_migrate_legacy_policy_empty():
    assert migrate_legacy_policy({}) == []
    assert migrate_legacy_policy(None) == []


def test_enabled_strategies_filters_and_preserves_order():
    raw = [
        {"action": "fallback_provider", "provider_ids": ["a"], "enabled": True},
        {"action": "stop_llm", "enabled": False},  # 禁用 → 过滤
        {"action": "stop_llm", "enabled": True},
    ]
    out = enabled_strategies(raw)
    assert [s["action"] for s in out] == ["fallback_provider", "stop_llm"]
    assert out[0]["provider_ids"] == ["a"]


def test_enabled_strategies_non_list():
    assert enabled_strategies(None) == []
    assert enabled_strategies("nope") == []


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
