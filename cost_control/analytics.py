"""报表 Mixin。

交叉聚合用量 / 成本 / 缓存命中 / 归因，生成日报 / 周报 / 月报，供 ``/report``
命令、Web API（``/report`` ``/overview``）、CronJob 推送复用。

可测性设计：窗口边界计算抽成模块级纯函数 ``report_window_start``（不依赖
astrbot / DB，可单测）；DB 查询在 ``AnalyticsMixin.build_report`` 内复用
``UsageQueryMixin`` / ``StoreMixin`` 的方法。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .budget import day_window_start, resolve_tz
from .cache_diag import hit_rate
from .config import get_config
from .cost import (
    compute_cost_in_main,
    compute_row_cost_in_main,
    resolve_effective_pricing,
)
from .exchange_rates import convert as _convert

# 生产环境经 root logger 汇入 astrbot/loguru；测试经 caplog 可捕获。
logger = logging.getLogger("cost_control.analytics")


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
        - ``monthly``：当日 ``refresh_time`` 起点回退 29 天（最近 30 天含今天）。
    """
    window = (window or "daily").strip().lower()
    daily_start = day_window_start(refresh_time, now_utc, tz)
    if window == "weekly":
        return daily_start - timedelta(days=6)
    if window == "monthly":
        return daily_start - timedelta(days=29)
    return daily_start


def compare_windows(
    window: str,
    now_utc: datetime,
    tz: ZoneInfo,
    refresh_time: str,
) -> tuple[datetime, datetime, datetime, datetime]:
    """计算同比对比的当前窗口与上一窗口 UTC 边界（纯函数）。

    返回 ``(cur_start, cur_end, prev_start, prev_end)``（均 aware UTC）。
    当前窗口与 :func:`report_window_start` 一致（``cur_end`` 为 ``now_utc``）；
    上一窗口为紧邻当前窗口起点的等长上一段：
    - ``daily``：``[cur_start-1d, cur_start)``
    - ``weekly``：``[cur_start-7d, cur_start)``
    - ``monthly``：``[cur_start-30d, cur_start)``（与当前「近 30 天」滚动口径对应）
    未知 window 按 daily。
    """
    cur_start = report_window_start(window, now_utc, tz, refresh_time)
    cur_end = now_utc
    w = (window or "daily").strip().lower()
    if w == "monthly":
        prev_start = cur_start - timedelta(days=30)
        prev_end = cur_start
    elif w == "weekly":
        prev_start = cur_start - timedelta(days=7)
        prev_end = cur_start
    else:
        prev_start = cur_start - timedelta(days=1)
        prev_end = cur_start
    return cur_start, cur_end, prev_start, prev_end


def _safe_row_cost_in_main(
    row: dict[str, Any],
    pricing: dict[str, Any],
    main_cur: str,
    rates: dict[str, float],
) -> float:
    """单条聚合行的主货币成本；该行计算失败（如 tiered_expr 运行时除零）按 0 计。

    报表是跨行聚合视图，一条坏记录只应损失自身那份成本（debug 日志可查），
    绝不能把异常抛回 ``build_report`` 的大 except——那会让整份日 / 周 / 月报
    静默变空。
    """
    try:
        return compute_row_cost_in_main(row, pricing, main_cur, rates)
    except Exception as e:
        logger.debug(
            "[cost_control] 报表 cost_by_model 单行失败 model=%s class=%s",
            str(row.get("provider_model") or "-"),
            type(e).__name__,
        )
        return 0.0


def _aggregate_supplements(
    sups: list[Any],
    pricing: dict[str, Any] | None = None,
    main_cur: str = "$",
    rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """从补充记录列表聚合缓存命中率与归因注入（纯函数）。

    Args:
        sups: ``CostSupplement`` 对象列表（duck-typed，含 ``cache_read`` /
            ``cache_creation`` / ``token_input_cached`` / ``token_input_other`` /
            ``injection_total`` / ``umo`` / token 三类属性）。
        pricing: 模型单价表，非空时按会话累加成本到 ``by_session[*].cost``。
        main_cur: 主货币代码。
        rates: 生效汇率表。

    会话成本口径与明细页（``_supplement_to_dict``）一致：

    - 优先用固化的 ``cost_amount`` + ``currency_symbol`` 按当前汇率换算到
      ``main_cur``（金额是记录时刻定价的快照，不受之后定价改动影响）；
    - ``per_request`` 无法逐行固化（单条恒 0），按 distinct
      ``(provider_id, request_id)`` × 当前定价只计一次（以最早行的定价为准），
      与 ``query_user_cost_total`` 同口径；``request_id`` 缺失的行无法归属，跳过；
    - 无固化值的历史未回填行，回退按当前定价重算。

    单行失败（如 tiered_expr 运行时除零）按 0 跳过并计一条 warning，绝不让
    一条坏记录把整份报表打挂成空。

    Returns:
        ``{"cache_hit_rate": float, "cache_samples": int, "avg_injection": float,
        "injection_samples": int, "by_session": [...]}``。无样本时各率 / 均值为 0。
    """
    cache_rates: list[float] = []
    injections: list[int] = []
    sessions: dict[str, dict[str, Any]] = {}
    _rates = rates or {}
    # per_request：同一 (provider, request_id) 只计一次，以最早 LLM 调用行的定价为准。
    req_charged: set[tuple[str, str]] = set()
    failed = 0
    ordered = sorted(sups or [], key=lambda s: str(getattr(s, "created_at", None) or ""))
    for s in ordered:
        cache_read = getattr(s, "cache_read", None)
        if cache_read is None:
            cache_read = getattr(s, "token_input_cached", None)
        rate = hit_rate(
            cache_read,
            getattr(s, "token_input_other", None),
            getattr(s, "cache_creation", None),
        )
        if rate >= 0:
            cache_rates.append(rate)
        inj = getattr(s, "injection_total", None)
        if inj is not None:
            try:
                injections.append(int(inj))
            except (TypeError, ValueError):
                pass
        token_input_other = int(getattr(s, "token_input_other", 0) or 0)
        token_input_cached = int(getattr(s, "token_input_cached", 0) or 0)
        token_output = int(getattr(s, "token_output", 0) or 0)
        umo = str(getattr(s, "umo", "") or "(unknown)")
        bucket = sessions.setdefault(umo, {"count": 0, "tokens": 0, "cost": 0.0})
        bucket["count"] += 1
        bucket["tokens"] += token_input_other + token_input_cached + token_output
        if pricing is None:
            continue
        try:
            provider_id = getattr(s, "provider_id", None) or None
            model = getattr(s, "provider_model", None)
            created_at = getattr(s, "created_at", None)
            rule = resolve_effective_pricing(provider_id, model, pricing, created_at)
            if rule is not None and rule.get("mode") == "per_request":
                rid = getattr(s, "request_id", None)
                pid = provider_id or ""
                if rid and (pid, str(rid)) not in req_charged:
                    req_charged.add((pid, str(rid)))
                    cur = str(rule.get("currency", "USD") or "USD").strip().upper() or "USD"
                    bucket["cost"] += _convert(
                        float(rule.get("price", 0.0) or 0.0), cur, main_cur, _rates
                    )
            elif getattr(s, "cost_amount", None) is not None:
                # 固化金额优先：与明细页同口径，按当前汇率换算到主货币。
                bucket["cost"] += _convert(
                    float(getattr(s, "cost_amount")),
                    str(getattr(s, "currency_symbol", None) or "USD"),
                    main_cur,
                    _rates,
                )
            else:
                # 回退：历史未回填行按当前定价重算并换算到主货币。
                bucket["cost"] += compute_cost_in_main(
                    {
                        "token_input_other": token_input_other,
                        "token_input_cached": token_input_cached,
                        "token_output": token_output,
                        "cache_creation": getattr(s, "cache_creation", None),
                        "billing_context": getattr(s, "billing_context", None),
                        "created_at": created_at,
                    },
                    provider_id,
                    model,
                    pricing,
                    main_cur,
                    _rates,
                )
        except Exception:
            failed += 1
    if failed:
        logger.warning("[cost_control] 报表会话成本有 %d 条记录计算失败，已按 0 跳过", failed)
    by_session: list[dict[str, Any]] = [
        {
            "umo": umo,
            "count": v["count"],
            "tokens": v["tokens"],
            "cost": round(float(v["cost"]), 6),
        }
        for umo, v in sorted(sessions.items(), key=lambda kv: kv[1]["tokens"], reverse=True)
    ]
    return {
        "cache_hit_rate": round(sum(cache_rates) / len(cache_rates), 1) if cache_rates else 0.0,
        "cache_samples": len(cache_rates),
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
    get_pricing: Any

    async def build_report(self, *, window: str = "daily") -> dict[str, Any]:
        """构建指定时间窗的用量 / 成本 / 缓存 / 归因综合报表。

        Args:
            window: ``"daily"`` / ``"weekly"`` / ``"monthly"``。

        Returns:
            报表 dict，含 ``window`` / ``start`` / ``end`` / ``usage`` /
            ``cost`` / ``cost_by_model`` / ``cache_hit_rate`` / ``avg_injection`` /
            ``top_sessions``（按 token 降序）/ ``top_sessions_by_cost``（按成本降序）。
            任何异常降级为空字段，绝不抛出。
        """
        now = datetime.now(UTC)
        tz = resolve_tz(self.context)
        refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
        start = report_window_start(window, now, tz, refresh)
        pricing = self.get_pricing()
        from .config import get_currency_symbol, get_rates

        main_cur = get_currency_symbol(getattr(self, "cfg", None))
        rates = get_rates(getattr(self, "cfg", None))

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
            "top_sessions_by_cost": [],
        }
        try:
            usage = await self.query_usage(start=start)
            rows = await self.query_usage_cost_rows(pricing, start=start)
            # 按 model 二次聚合（同模型可能由多个 provider 提供、不同价）
            model_agg: dict[str, dict[str, Any]] = {}
            for r in rows:
                model_name = r.get("provider_model") or ""
                m = model_agg.setdefault(
                    model_name,
                    {"model": model_name, "count": 0, "tokens": 0, "cost": 0.0},
                )
                m["count"] += int(r.get("count", 0) or 0)
                toks = (
                    int(r.get("token_input_other", 0) or 0)
                    + int(r.get("token_input_cached", 0) or 0)
                    + int(r.get("token_output", 0) or 0)
                )
                m["tokens"] += toks
                # 单行失败按 0 计（见 _safe_row_cost_in_main），不让坏记录打挂整份报表。
                m["cost"] += _safe_row_cost_in_main(r, pricing, main_cur, rates)
            cost_by_model = [{**m, "cost": round(float(m["cost"]), 6)} for m in model_agg.values()]
            cost_by_model.sort(key=lambda m: m["cost"], reverse=True)
            total_cost = round(sum(m["cost"] for m in cost_by_model), 6)

            sups = await self.query_supplements(start=start, limit=5000)
            if len(sups) >= 5000:
                logger.warning(
                    "[cost_control] 月报补充记录达到 limit=5000，缓存/注入统计可能被截断"
                )
            agg = _aggregate_supplements(sups, pricing, main_cur, rates)

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
                "top_sessions_by_cost": sorted(
                    agg["by_session"],
                    key=lambda s: float(s.get("cost", 0) or 0),
                    reverse=True,
                )[:10],
            }
        except Exception:
            return empty
