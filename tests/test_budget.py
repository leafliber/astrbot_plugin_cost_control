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
