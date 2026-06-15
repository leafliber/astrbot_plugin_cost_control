"""报表 Mixin。

交叉聚合用量 / 成本 / 缓存命中 / 归因，生成日报 / 周报 / 月报，供 ``/report``
命令、Web API（``/report`` ``/overview``）、CronJob 推送复用。

可测性设计：窗口边界计算抽成模块级纯函数 ``report_window_start``（不依赖
astrbot / DB，可单测）；DB 查询在 ``AnalyticsMixin.build_report`` 内复用
``UsageQueryMixin`` / ``StoreMixin`` 的方法。

阶段 4 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .budget import day_window_start, month_window_start, resolve_tz
from .cache_diag import hit_rate
from .config import get_config, get_pricing
from .cost import compute_cost_value


def report_window_start(
    window: str,
    now_utc: datetime,
    tz: ZoneInfo,
    refresh_time: str,
) -> datetime:
    """计算报表窗口的起始 UTC 时刻（纯函数）。

    Args:
        window: ``"daily"`` / ``"weekly"`` / ``"monthly"``，未知值按 daily。
        now_utc: 当前 UTC 时刻（aware）。
        tz: 本地时区。
        refresh_time: ``"HH:MM"`` 日刷新点（按本地时区解释）。

    Returns:
        窗口起始 UTC datetime（aware）。
        - ``daily``：当日 ``refresh_time`` 起点。
        - ``weekly``：当日 ``refresh_time`` 起点回退 6 天（最近 7 天含今天）。
        - ``monthly``：本月 1 日 0 点。
    """
    window = (window or "daily").strip().lower()
    if window == "monthly":
        return month_window_start(now_utc, tz)
    daily_start = day_window_start(refresh_time, now_utc, tz)
    if window == "weekly":
        return daily_start - timedelta(days=6)
    return daily_start


def _row_cost(row: dict[str, Any], pricing: dict[str, dict[str, float]]) -> float:
    """按模型单价算单行成本（纯函数辅助）。模型无匹配单价返回 0.0。"""
    return compute_cost_value(row, row.get("key") or None, pricing)


def _aggregate_supplements(
    sups: list[Any],
) -> dict[str, Any]:
    """从补充记录列表聚合缓存命中率与归因注入（纯函数）。

    Args:
        sups: ``CostSupplement`` 对象列表（duck-typed，含 ``cache_read`` /
            ``cache_creation`` / ``token_input_cached`` / ``token_input_other`` /
            ``injection_total`` / ``umo`` / token 三类属性）。

    Returns:
        ``{"cache_hit_rate": float, "cache_samples": int, "avg_injection": float,
        "injection_samples": int, "by_session": [...]}``。无样本时各率 / 均值为 0。
    """
    rates: list[float] = []
    injections: list[int] = []
    sessions: dict[str, dict[str, int]] = {}
    for s in sups or []:
        rate = hit_rate(
            getattr(s, "cache_read", None) or getattr(s, "token_input_cached", None),
            getattr(s, "token_input_other", None),
            getattr(s, "cache_creation", None),
        )
        if rate >= 0:
            rates.append(rate)
        inj = getattr(s, "injection_total", None)
        if inj is not None:
            try:
                injections.append(int(inj))
            except (TypeError, ValueError):
                pass
        umo = str(getattr(s, "umo", "") or "(unknown)")
        bucket = sessions.setdefault(umo, {"count": 0, "tokens": 0})
        bucket["count"] += 1
        bucket["tokens"] += (
            int(getattr(s, "token_input_other", 0) or 0)
            + int(getattr(s, "token_input_cached", 0) or 0)
            + int(getattr(s, "token_output", 0) or 0)
        )
    by_session: list[dict[str, Any]] = [
        {"umo": umo, "count": v["count"], "tokens": v["tokens"]}
        for umo, v in sorted(sessions.items(), key=lambda kv: kv[1]["tokens"], reverse=True)
    ]
    return {
        "cache_hit_rate": round(sum(rates) / len(rates), 1) if rates else 0.0,
        "cache_samples": len(rates),
        "avg_injection": round(sum(injections) / len(injections)) if injections else 0,
        "injection_samples": len(injections),
        "by_session": by_session,
    }


class AnalyticsMixin:
    """生成交叉报表的 Mixin。

    依赖兄弟 ``UsageQueryMixin`` / ``StoreMixin``（由 ``Main`` 多继承提供）。
    """

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any
    # 兄弟 Mixin 提供。
    query_usage: Any
    query_usage_grouped: Any
    query_supplements: Any

    async def build_report(self, *, window: str = "daily") -> dict[str, Any]:
        """构建指定时间窗的用量 / 成本 / 缓存 / 归因综合报表。

        Args:
            window: ``"daily"`` / ``"weekly"`` / ``"monthly"``。

        Returns:
            报表 dict，含 ``window`` / ``start`` / ``end`` / ``usage`` /
            ``cost`` / ``cost_by_model`` / ``cache_hit_rate`` / ``avg_injection`` /
            ``top_sessions``。任何异常降级为空字段，绝不抛出。
        """
        now = datetime.now(UTC)
        tz = resolve_tz(self.context)
        refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
        start = report_window_start(window, now, tz, refresh)
        pricing = get_pricing(getattr(self, "config", None))

        empty: dict[str, Any] = {
            "window": window,
            "start": start.isoformat(),
            "end": now.isoformat(),
            "usage": {},
            "cost": 0.0,
            "cost_by_model": [],
            "cache_hit_rate": 0.0,
            "cache_samples": 0,
            "avg_injection": 0,
            "injection_samples": 0,
            "top_sessions": [],
        }
        try:
            usage = await self.query_usage(start=start)
            rows = await self.query_usage_grouped(by="model", start=start)
            cost_by_model = [
                {
                    "model": r.get("key") or "",
                    "count": r.get("count", 0),
                    "tokens": (
                        int(r.get("token_input_other", 0) or 0)
                        + int(r.get("token_input_cached", 0) or 0)
                        + int(r.get("token_output", 0) or 0)
                    ),
                    "cost": round(_row_cost(r, pricing), 6),
                }
                for r in rows
            ]
            cost_by_model.sort(key=lambda m: m["cost"], reverse=True)
            total_cost = round(sum(m["cost"] for m in cost_by_model), 6)

            sups = await self.query_supplements(start=start, limit=5000)
            agg = _aggregate_supplements(sups)

            return {
                "window": window,
                "start": start.isoformat(),
                "end": now.isoformat(),
                "usage": usage,
                "cost": total_cost,
                "cost_by_model": cost_by_model,
                "cache_hit_rate": agg["cache_hit_rate"],
                "cache_samples": agg["cache_samples"],
                "avg_injection": agg["avg_injection"],
                "injection_samples": agg["injection_samples"],
                "top_sessions": agg["by_session"][:10],
            }
        except Exception:
            return empty
