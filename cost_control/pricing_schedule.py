"""分时定价策略的规范化与时间段匹配。

``pricing_schedules`` 是现有基础定价之上的独立层。该模块只处理时间语义和
通用结构，不依赖 AstrBot；嵌套定价规则的规范化由调用方通过 ``normalize_rule``
注入，避免与 :mod:`cost_control.config` 形成循环依赖。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


class PricingScheduleValidationError(ValueError):
    """分时定价配置不合法。"""


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MAX_PERIODS = 64


def _fail(message: str, *, strict: bool) -> None:
    if strict:
        raise PricingScheduleValidationError(message)


def _minute_of_day(value: Any) -> int | None:
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _normalize_weekdays(value: Any, *, strict: bool, label: str) -> list[int] | None:
    raw = value if isinstance(value, (list, tuple, set)) else []
    if not raw:
        raw = list(range(1, 8))
    out: list[int] = []
    for item in raw:
        try:
            day = int(item)
        except (TypeError, ValueError):
            _fail(f"{label}.weekdays 必须是 1–7 的数组", strict=strict)
            return None
        if day < 1 or day > 7:
            _fail(f"{label}.weekdays 只能包含 1–7", strict=strict)
            return None
        if day not in out:
            out.append(day)
    out.sort()
    return out


def _period_intervals(period: dict[str, Any]) -> list[tuple[int, int]]:
    """把周期规则展开为一周内的分钟区间，用于检测重叠。"""
    intervals: list[tuple[int, int]] = []
    for weekday in period["weekdays"]:
        day_start = (weekday - 1) * 1440
        if period.get("all_day"):
            intervals.append((day_start, day_start + 1440))
            continue
        start = int(period["start_minute"])
        end = int(period["end_minute"])
        finish = day_start + end if start < end else day_start + 1440 + end
        begin = day_start + start
        if finish <= 10080:
            intervals.append((begin, finish))
        else:
            intervals.append((begin, 10080))
            intervals.append((0, finish - 10080))
    return intervals


def _validate_no_overlap(periods: list[dict[str, Any]], *, strict: bool) -> bool:
    expanded: list[tuple[int, int, str]] = []
    for period in periods:
        if not period.get("enabled", True):
            continue
        for begin, end in _period_intervals(period):
            expanded.append((begin, end, str(period.get("name") or period.get("id"))))
    expanded.sort(key=lambda item: (item[0], item[1]))
    for idx in range(1, len(expanded)):
        prev = expanded[idx - 1]
        cur = expanded[idx]
        if cur[0] < prev[1]:
            _fail(f"时间段“{prev[2]}”与“{cur[2]}”存在重叠", strict=strict)
            return False
    return True


def normalize_pricing_schedules(
    raw: Any,
    normalize_rule: Callable[[Any], dict[str, Any] | None],
    *,
    strict: bool = False,
) -> dict[str, dict[str, Any]]:
    """规范化完整 ``pricing_schedules`` 映射。

    非严格模式用于加载历史/人工编辑配置：非法目标被跳过。严格模式用于 Web API
    保存：发现首个错误即抛 :class:`PricingScheduleValidationError`。
    """
    if not isinstance(raw, dict):
        _fail("pricing_schedules 必须是对象（key=provider_id 或 provider_id|model）", strict=strict)
        return {}
    result: dict[str, dict[str, Any]] = {}
    for target, schedule in raw.items():
        target_s = str(target or "").strip()
        if not target_s:
            _fail("pricing_schedules 的目标不能为空", strict=strict)
            continue
        normalized = normalize_pricing_schedule(
            schedule,
            normalize_rule,
            strict=strict,
            label=f"pricing_schedules[{target_s}]",
        )
        if normalized is not None:
            result[target_s] = normalized
    return result


def normalize_pricing_schedule(
    raw: Any,
    normalize_rule: Callable[[Any], dict[str, Any] | None],
    *,
    strict: bool = False,
    label: str = "pricing_schedule",
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        _fail(f"{label} 必须是对象", strict=strict)
        return None
    timezone = str(raw.get("timezone") or "").strip()
    if timezone:
        try:
            ZoneInfo(timezone)
        except Exception:
            _fail(f"{label}.timezone 不是合法 IANA 时区：{timezone}", strict=strict)
            return None
    periods_raw = raw.get("periods") or []
    if not isinstance(periods_raw, list):
        _fail(f"{label}.periods 必须是数组", strict=strict)
        return None
    if len(periods_raw) > _MAX_PERIODS:
        _fail(f"{label}.periods 不能超过 {_MAX_PERIODS} 条", strict=strict)
        return None

    periods: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(periods_raw):
        plabel = f"{label}.periods[{idx}]"
        if not isinstance(item, dict):
            _fail(f"{plabel} 必须是对象", strict=strict)
            continue
        period_id = str(item.get("id") or f"period_{idx + 1}").strip()
        if not period_id or period_id in seen_ids:
            _fail(f"{plabel}.id 不能为空且不能重复", strict=strict)
            continue
        seen_ids.add(period_id)
        name = str(item.get("name") or period_id).strip() or period_id
        weekdays = _normalize_weekdays(item.get("weekdays"), strict=strict, label=plabel)
        if weekdays is None:
            continue
        all_day = bool(item.get("all_day", False))
        start_raw = str(item.get("start") or "00:00").strip()
        end_raw = str(item.get("end") or "00:00").strip()
        start_minute = _minute_of_day(start_raw)
        end_minute = _minute_of_day(end_raw)
        if not all_day and (start_minute is None or end_minute is None):
            _fail(f"{plabel} 的 start/end 必须为 HH:MM", strict=strict)
            continue
        if not all_day and start_minute == end_minute:
            _fail(f"{plabel} 起止时间相同时请启用 all_day", strict=strict)
            continue

        adjustment = item.get("adjustment")
        if not isinstance(adjustment, dict):
            _fail(f"{plabel}.adjustment 必须是对象", strict=strict)
            continue
        adjustment_type = str(adjustment.get("type") or "").strip().lower()
        normalized_adjustment: dict[str, Any]
        if adjustment_type == "multiplier":
            # 0 是合法分时倍率（该时段计零成本），仅拒绝负数与非数值。
            try:
                value = float(adjustment.get("value"))
            except (TypeError, ValueError):
                value = -1.0
            if not math.isfinite(value) or value < 0 or value > 100:
                _fail(f"{plabel}.adjustment.value 必须在 0–100 之间", strict=strict)
                continue
            normalized_adjustment = {"type": "multiplier", "value": value}
        elif adjustment_type == "override":
            raw_rule = adjustment.get("rule")
            rule = normalize_rule(raw_rule)
            if rule is None:
                _fail(f"{plabel}.adjustment.rule 不是合法定价规则", strict=strict)
                continue
            if isinstance(raw_rule, dict) and "currency" not in raw_rule:
                rule.pop("currency", None)
            normalized_adjustment = {"type": "override", "rule": rule}
        else:
            _fail(f"{plabel}.adjustment.type 必须是 multiplier/override", strict=strict)
            continue

        periods.append(
            {
                "id": period_id,
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "weekdays": weekdays,
                "all_day": all_day,
                "start": "00:00" if all_day else start_raw,
                "end": "00:00" if all_day else end_raw,
                "start_minute": 0 if all_day else int(start_minute or 0),
                "end_minute": 0 if all_day else int(end_minute or 0),
                "adjustment": normalized_adjustment,
            }
        )
    if not _validate_no_overlap(periods, strict=strict):
        return None
    return {
        "enabled": bool(raw.get("enabled", True)),
        "timezone": timezone,
        "periods": periods,
    }


def _as_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def match_pricing_period(
    schedule: Any,
    created_at: Any,
    *,
    default_timezone: str = "UTC",
) -> dict[str, Any] | None:
    """返回指定时刻命中的分时规则；无时间/无命中返回 ``None``。

    跨午夜规则的凌晨部分归属于前一个 ISO 星期日。例如周一 ``23:00–07:00``
    会命中周二 06:59，但不会命中周一 06:59。
    """
    if not isinstance(schedule, dict) or not schedule.get("enabled", True):
        return None
    dt = _as_aware_datetime(created_at)
    if dt is None:
        return None
    timezone = str(schedule.get("timezone") or default_timezone or "UTC")
    try:
        local = dt.astimezone(ZoneInfo(timezone))
    except Exception:
        local = dt.astimezone(UTC)
    weekday = local.isoweekday()
    previous_weekday = 7 if weekday == 1 else weekday - 1
    minute = local.hour * 60 + local.minute
    for period in schedule.get("periods") or []:
        if not isinstance(period, dict) or not period.get("enabled", True):
            continue
        weekdays = period.get("weekdays") or []
        if period.get("all_day"):
            if weekday in weekdays:
                return period
            continue
        start = int(period.get("start_minute", _minute_of_day(period.get("start")) or 0))
        end = int(period.get("end_minute", _minute_of_day(period.get("end")) or 0))
        if start < end:
            if weekday in weekdays and start <= minute < end:
                return period
        else:
            if (minute >= start and weekday in weekdays) or (
                minute < end and previous_weekday in weekdays
            ):
                return period
    return None


def scheduled_provider_ids(schedules: Any) -> set[str]:
    """返回需要保留分钟粒度的 Provider ID 集合。"""
    if not isinstance(schedules, dict):
        return set()
    out: set[str] = set()
    for target, schedule in schedules.items():
        if not isinstance(schedule, dict) or not schedule.get("enabled", True):
            continue
        if not any(
            isinstance(p, dict) and p.get("enabled", True) for p in schedule.get("periods") or []
        ):
            continue
        provider_id = str(target or "").split("|", 1)[0].strip()
        if provider_id:
            out.add(provider_id)
    return out
