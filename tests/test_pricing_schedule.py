"""分时定价配置、匹配和成本组合回归测试。"""

from datetime import UTC, datetime

import pytest

from cost_control.config import normalize_pricing_schedules
from cost_control.cost import compute_cost_value, resolve_effective_pricing
from cost_control.pricing_schedule import (
    PricingScheduleValidationError,
    match_pricing_period,
    scheduled_provider_ids,
)


def _raw_schedule(periods, timezone="Asia/Shanghai"):
    return {"enabled": True, "timezone": timezone, "periods": periods}


def _multiplier_period(**overrides):
    period = {
        "id": "peak",
        "name": "峰时",
        "weekdays": [1, 2, 3, 4, 5],
        "start": "09:00",
        "end": "18:00",
        "adjustment": {"type": "multiplier", "value": 2},
    }
    period.update(overrides)
    return period


def test_normalize_and_match_boundaries_in_iana_timezone():
    schedules = normalize_pricing_schedules(
        {"prov": _raw_schedule([_multiplier_period()])}, strict=True
    )
    schedule = schedules["prov"]
    # 2026-08-31 是周一；上海 09:00/18:00 分别为 UTC 01:00/10:00。
    assert match_pricing_period(schedule, datetime(2026, 8, 31, 0, 59, tzinfo=UTC)) is None
    assert match_pricing_period(schedule, datetime(2026, 8, 31, 1, 0, tzinfo=UTC))["id"] == "peak"
    assert match_pricing_period(schedule, datetime(2026, 8, 31, 9, 59, tzinfo=UTC))["id"] == "peak"
    assert match_pricing_period(schedule, datetime(2026, 8, 31, 10, 0, tzinfo=UTC)) is None


def test_cross_midnight_uses_previous_weekday_for_early_hours():
    schedules = normalize_pricing_schedules(
        {"prov": _raw_schedule([_multiplier_period(start="23:00", end="07:00", weekdays=[1])])},
        strict=True,
    )
    schedule = schedules["prov"]
    # 周二上海 06:30 仍属于周一夜间段；周一 06:30 不属于。
    assert match_pricing_period(schedule, datetime(2026, 8, 31, 22, 30, tzinfo=UTC))
    assert match_pricing_period(schedule, datetime(2026, 8, 30, 22, 30, tzinfo=UTC)) is None


def test_overlap_is_rejected_including_cross_midnight():
    raw = {
        "prov": _raw_schedule(
            [
                _multiplier_period(id="night", start="22:00", end="07:00", weekdays=[1]),
                _multiplier_period(id="early", start="06:00", end="08:00", weekdays=[2]),
            ]
        )
    }
    with pytest.raises(PricingScheduleValidationError, match="重叠"):
        normalize_pricing_schedules(raw, strict=True)


def test_invalid_timezone_and_multiplier_are_rejected():
    with pytest.raises(PricingScheduleValidationError, match="IANA"):
        normalize_pricing_schedules(
            {"prov": _raw_schedule([_multiplier_period()], "Mars/Olympus")}, strict=True
        )
    with pytest.raises(PricingScheduleValidationError, match="0–100"):
        normalize_pricing_schedules(
            {
                "prov": _raw_schedule(
                    [_multiplier_period(adjustment={"type": "multiplier", "value": -0.5})]
                )
            },
            strict=True,
        )
    with pytest.raises(PricingScheduleValidationError, match="0–100"):
        normalize_pricing_schedules(
            {"prov": _raw_schedule([_multiplier_period(adjustment={"type": "multiplier"})])},
            strict=True,
        )


def test_zero_schedule_multiplier_is_accepted():
    schedules = normalize_pricing_schedules(
        {
            "prov": _raw_schedule(
                [_multiplier_period(adjustment={"type": "multiplier", "value": 0})]
            )
        },
        strict=True,
    )
    assert schedules["prov"]["periods"][0]["adjustment"] == {"type": "multiplier", "value": 0.0}


def _pricing(schedule, *, user=None, multiplier=None):
    return {
        "defaults": {},
        "user": user
        or {
            "prov": {
                "mode": "per_token",
                "input": 1.0,
                "input_cached": 0.0,
                "output": 2.0,
                "cache_creation": 0.0,
                "currency": "USD",
                "configured": {
                    "input": True,
                    "input_cached": True,
                    "output": True,
                    "cache_creation": True,
                },
            }
        },
        "schedules": {"prov": schedule},
        "timezone": "Asia/Shanghai",
        "multipliers": {"cluster": multiplier} if multiplier else {},
        "provider_clusters": {"prov": "cluster"} if multiplier else {},
    }


def test_multiplier_period_composes_with_cluster_multiplier_and_marks_usage():
    schedule = normalize_pricing_schedules(
        {"prov": _raw_schedule([_multiplier_period(weekdays=[1, 2, 3, 4, 5, 6, 7])])},
        strict=True,
    )["prov"]
    pricing = _pricing(schedule, multiplier=3)
    usage = {
        "token_input_other": 1_000_000,
        "created_at": datetime(2026, 8, 31, 2, 0, tzinfo=UTC),
    }
    assert compute_cost_value(usage, "prov", "m", pricing) == pytest.approx(6.0)
    assert usage["_pricing_period_id"] == "peak"


def test_zero_schedule_multiplier_zeroes_cost_even_with_cluster_multiplier():
    # 0 分时倍率 = 该时段免费；与任何聚类倍率叠加仍为 0，不被兜底回 1 倍。
    schedule = normalize_pricing_schedules(
        {
            "prov": _raw_schedule(
                [
                    _multiplier_period(
                        weekdays=[1, 2, 3, 4, 5, 6, 7],
                        adjustment={"type": "multiplier", "value": 0},
                    )
                ]
            )
        },
        strict=True,
    )["prov"]
    pricing = _pricing(schedule, multiplier=3)
    usage = {
        "token_input_other": 1_000_000,
        "created_at": datetime(2026, 8, 31, 2, 0, tzinfo=UTC),
    }
    assert compute_cost_value(usage, "prov", "m", pricing) == 0.0
    assert usage["_pricing_period_id"] == "peak"


def test_override_period_can_change_mode_and_inherit_currency():
    schedule = normalize_pricing_schedules(
        {
            "prov": _raw_schedule(
                [
                    _multiplier_period(
                        weekdays=[1, 2, 3, 4, 5, 6, 7],
                        adjustment={
                            "type": "override",
                            "rule": {"mode": "per_turn", "price": 0.25},
                        },
                    )
                ]
            )
        },
        strict=True,
    )["prov"]
    pricing = _pricing(
        schedule,
        user={"prov": {"mode": "per_turn", "price": 1.0, "currency": "CNY"}},
    )
    rule = resolve_effective_pricing("prov", "m", pricing, datetime(2026, 8, 31, 2, 0, tzinfo=UTC))
    assert rule and rule["mode"] == "per_turn" and rule["price"] == 0.25
    assert rule["currency"] == "CNY"


def test_model_schedule_wins_and_missing_time_falls_back_to_base():
    provider_schedule = normalize_pricing_schedules(
        {"prov": _raw_schedule([_multiplier_period(weekdays=[1, 2, 3, 4, 5, 6, 7])])},
        strict=True,
    )["prov"]
    model_schedule = normalize_pricing_schedules(
        {
            "prov|m": _raw_schedule(
                [
                    _multiplier_period(
                        weekdays=[1, 2, 3, 4, 5, 6, 7],
                        adjustment={"type": "multiplier", "value": 4},
                    )
                ]
            )
        },
        strict=True,
    )["prov|m"]
    pricing = _pricing(provider_schedule)
    pricing["schedules"]["prov|m"] = model_schedule
    at = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
    assert resolve_effective_pricing("prov", "m", pricing, at)["input"] == 4.0
    assert resolve_effective_pricing("prov", "m", pricing, None)["input"] == 1.0
    assert scheduled_provider_ids(pricing["schedules"]) == {"prov"}
