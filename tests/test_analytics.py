"""``AnalyticsMixin`` 纯函数单测：报表窗口边界与补充记录聚合。"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from cost_control.analytics import (
    _aggregate_supplements,
    compare_windows,
    report_window_start,
)
from cost_control.budget import day_window_start
from cost_control.config import DEFAULT_PRICING

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
    daily = day_window_start(refresh, _NOW, _TZ)
    assert report_window_start("monthly", _NOW, _TZ, refresh) == daily - timedelta(days=29)


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


def test_aggregate_with_pricing_sums_cost():
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            injection_total=None,
        ),
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            injection_total=None,
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}})
    # 两条各 1M input gpt-4o = $2.5，合计 $5.0
    assert r["by_session"][0]["umo"] == "s1"
    assert abs(r["by_session"][0]["cost"] - 5.0) < 1e-6


def test_aggregate_unpriced_model_cost_zero():
    sups = [
        _Sup(
            umo="s1",
            provider_model="nonexistent-model-xyz",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            injection_total=None,
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}})
    assert r["by_session"][0]["cost"] == 0.0


def test_aggregate_no_pricing_cost_zero():
    # pricing=None（向后兼容）时 cost 字段仍存在且为 0
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            injection_total=None,
        ),
    ]
    r = _aggregate_supplements(sups)
    assert r["by_session"][0]["cost"] == 0.0


def test_aggregate_prefers_fixed_cost_amount_over_current_pricing():
    # 固化口径（与明细页一致）：cost_amount=7.2 CNY 按当前汇率换算 = 1.0 USD；
    # 不按当前定价重算（gpt-4o 1M input 重算会是 2.5）。
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=7.2,
            currency_symbol="CNY",
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}}, "USD", None)
    assert abs(r["by_session"][0]["cost"] - 1.0) < 1e-6


def test_aggregate_unbackfilled_row_recalcs_with_current_pricing():
    # 无固化值的历史行：回退按当前定价重算（gpt-4o 1M input = 2.5 USD）。
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}}, "USD", None)
    assert abs(r["by_session"][0]["cost"] - 2.5) < 1e-6


def test_aggregate_fixed_and_recalc_rows_sum():
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
        ),
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=1.0,
            currency_symbol="USD",
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}}, "USD", None)
    assert abs(r["by_session"][0]["cost"] - 3.5) < 1e-6


def test_aggregate_per_request_counts_distinct_request_ids():
    # per_request 单条固化恒 0，报表须按 distinct (provider, request_id) 计一次。
    pricing = {"defaults": {}, "user": {"p1|m": {"mode": "per_request", "price": 0.01}}}
    sups = [
        _Sup(
            umo="s1",
            provider_id="p1",
            provider_model="m",
            request_id="r1",
            token_input_other=100,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=0.0,
            currency_symbol="USD",
        ),
        _Sup(
            umo="s1",
            provider_id="p1",
            provider_model="m",
            request_id="r1",
            token_input_other=200,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=0.0,
            currency_symbol="USD",
        ),
        _Sup(
            umo="s1",
            provider_id="p1",
            provider_model="m",
            request_id="r2",
            token_input_other=300,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=0.0,
            currency_symbol="USD",
        ),
    ]
    r = _aggregate_supplements(sups, pricing, "USD", None)
    # 3 次 LLM 调用、2 个用户请求 → 0.02（而非逐行恒 0，也非 3×0.01）。
    assert r["by_session"][0]["count"] == 3
    assert abs(r["by_session"][0]["cost"] - 0.02) < 1e-9


def test_aggregate_row_calc_failure_skipped_not_fatal(monkeypatch):
    # 回归：一条坏记录（如 tiered_expr 运行时除零）不得把整份报表打挂成空，
    # 只损失该行成本，其余行（含固化行）正常计入。
    import cost_control.analytics as analytics_mod
    from cost_control.cost import TieredExprEvaluationError

    def boom(*args, **kwargs):
        raise TieredExprEvaluationError("ZeroDivisionError")

    monkeypatch.setattr(analytics_mod, "compute_cost_in_main", boom)
    sups = [
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
        ),
        _Sup(
            umo="s1",
            provider_model="gpt-4o",
            token_input_other=1_000_000,
            token_input_cached=0,
            token_output=0,
            cache_creation=None,
            cost_amount=1.0,
            currency_symbol="USD",
        ),
    ]
    r = _aggregate_supplements(sups, {"defaults": DEFAULT_PRICING, "user": {}}, "USD", None)
    assert r["by_session"][0]["count"] == 2
    assert abs(r["by_session"][0]["cost"] - 1.0) < 1e-6


def test_safe_row_cost_in_main_swallows_row_failure(monkeypatch):
    # 回归：build_report 的 cost_by_model 逐行累加同样不能被单行异常打断。
    import cost_control.analytics as analytics_mod
    from cost_control.cost import TieredExprEvaluationError

    def boom(*args, **kwargs):
        raise TieredExprEvaluationError("ZeroDivisionError")

    monkeypatch.setattr(analytics_mod, "compute_row_cost_in_main", boom)
    assert (
        analytics_mod._safe_row_cost_in_main(
            {"provider_model": "m"}, {"defaults": {}, "user": {}}, "USD", None
        )
        == 0.0
    )


def test_compare_windows_daily():
    refresh = "00:00"
    cur_start, cur_end, prev_start, prev_end = compare_windows("daily", _NOW, _TZ, refresh)
    daily = day_window_start(refresh, _NOW, _TZ)
    assert cur_start == daily
    assert cur_end == _NOW
    assert prev_start == daily - timedelta(days=1)
    assert prev_end == daily


def test_compare_windows_weekly():
    refresh = "00:00"
    cur_start, cur_end, prev_start, prev_end = compare_windows("weekly", _NOW, _TZ, refresh)
    wk = report_window_start("weekly", _NOW, _TZ, refresh)
    assert cur_start == wk
    assert prev_start == wk - timedelta(days=7)
    assert prev_end == wk


def test_compare_windows_monthly():
    refresh = "00:00"
    cur_start, cur_end, prev_start, prev_end = compare_windows("monthly", _NOW, _TZ, refresh)
    mo = report_window_start("monthly", _NOW, _TZ, refresh)
    assert cur_start == mo
    assert prev_start == mo - timedelta(days=30)
    assert prev_end == mo


def test_compare_windows_unknown_defaults_daily():
    refresh = "00:00"
    _, _, prev_start, prev_end = compare_windows("nonsense", _NOW, _TZ, refresh)
    daily = day_window_start(refresh, _NOW, _TZ)
    assert prev_start == daily - timedelta(days=1)
    assert prev_end == daily
