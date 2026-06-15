"""``AnalyticsMixin`` 纯函数单测：报表窗口边界与补充记录聚合。"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from cost_control.analytics import _aggregate_supplements, report_window_start
from cost_control.budget import day_window_start, month_window_start

_TZ = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 6, 15, 5, 0, 0, tzinfo=UTC)  # 北京 13:00


def test_report_window_daily():
    refresh = "00:00"
    assert report_window_start("daily", _NOW, _TZ, refresh) == day_window_start(refresh, _NOW, _TZ)


def test_report_window_weekly_is_six_days_before_daily():
    refresh = "00:00"
    daily = day_window_start(refresh, _NOW, _TZ)
    assert report_window_start("weekly", _NOW, _TZ, refresh) == daily - timedelta(days=6)


def test_report_window_monthly():
    refresh = "00:00"
    assert report_window_start("monthly", _NOW, _TZ, refresh) == month_window_start(_NOW, _TZ)


def test_report_window_unknown_defaults_daily():
    refresh = "09:00"
    assert report_window_start("nonsense", _NOW, _TZ, refresh) == day_window_start(
        refresh, _NOW, _TZ
    )


class _Sup:
    """duck-typed CostSupplement（供 _aggregate_supplements 的 getattr 访问）。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_aggregate_empty():
    r = _aggregate_supplements([])
    assert r["cache_hit_rate"] == 0.0
    assert r["cache_samples"] == 0
    assert r["avg_injection"] == 0
    assert r["by_session"] == []


def test_aggregate_with_data():
    sups = [
        _Sup(
            umo="s1",
            token_input_other=100,
            token_input_cached=100,
            token_output=50,
            cache_read=100,
            cache_creation=0,
            injection_total=200,
        ),
        _Sup(
            umo="s1",
            token_input_other=200,
            token_input_cached=0,
            token_output=0,
            cache_read=0,
            cache_creation=0,
            injection_total=100,
        ),
        _Sup(
            umo="s2",
            token_input_other=0,
            token_input_cached=0,
            token_output=0,
            cache_read=None,
            cache_creation=None,
            injection_total=None,
        ),
    ]
    r = _aggregate_supplements(sups)
    # 命中率样本：前两条（第三条无数据 hit_rate=-1 不计入）
    assert r["cache_samples"] == 2
    # 注入样本：前两条（第三条 None 不计入）
    assert r["injection_samples"] == 2
    assert r["avg_injection"] == 150  # (200+100)/2
    # by_session 按 token 降序：s1=450, s2=0
    assert r["by_session"][0]["umo"] == "s1"
    assert r["by_session"][0]["tokens"] == 450
    assert r["by_session"][1]["umo"] == "s2"
