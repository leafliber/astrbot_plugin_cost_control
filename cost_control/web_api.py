"""Web API Mixin。

通过 AstrBot 的 ``register_web_api`` 机制对外暴露 REST 接口，供 Plugin Page
前端（``pages/dashboard/``）通过 bridge SDK（``AstrBotPluginPage.apiGet`` /
``apiPost``）拉取数据。

源码约束（已核对 ``astrbot/core/star/context.py`` + ``dashboard/server.py``，
版本 4.25.5）：
- ``Context.register_web_api(route, view_handler, methods, desc)`` —— **全部必填**，
  handler 为 Quart view function（签名 ``async def handler(**kwargs)``），URL 路径
  变量经 Werkzeug 匹配进 ``kwargs``；query / body 通过**全局** ``quart.request``
  读取（``await request.json`` / ``request.args``）。
- 最终 URL：``http://<host>/api/plug/<route>``（dashboard 统一加 ``/api/plug/``
  前缀，route **不自动**含插件名 → 必须自带命名空间防全局路由冲突）。
- 返回 ``dict`` / ``list`` 由 Quart 自动 JSON 序列化；``datetime`` 经
  ``AstrBotJSONProvider`` 转 ISO。
- 认证：需 dashboard JWT；Plugin Page 通过 bridge 由父级 SPA 代发（自动带 JWT）。

降级原则：每个 handler 独立 try/except，失败返回 ``{"success": False, ...}``，
绝不抛出未捕获异常。

⚠️ 响应信封选用 ``{"success": True, "data": ...}``（而非 AstrBot 标准的
``{"status": "ok", "data": ...}``）。原因（已核对参考插件 message_recorder
+ bridge 行为）：Plugin Page bridge 的父级 SPA 复用 AstrBot 的 dashboard API
客户端，该客户端的响应拦截器会**自动解包标准 ``{status, data}`` 信封**——对
``{status:"ok", data}`` 直接 resolve 解包后的 ``data``，故前端拿到的 ``r``
是裸业务数据、不再含 ``status`` 字段，前端按 ``r.status`` 判定会落空。改用非
标准的 ``{success, data}`` 信封后，父级不识别 ``status`` 字段即**原样透传**，
前端再自行 ``extractData``（见 ``pages/dashboard/app.js``），与 message_recorder
完全一致、跨版本稳定。

阶段 4 实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .attributor import ESTIMATION_NOTE
from .budget import resolve_tz
from .config import (
    CONFIG_DEFAULTS,
    coerce_to_default_type,
    deep_merge,
    get_config,
    normalize_budget_override,
    normalize_fallback_provider,
    save_plugin_config,
    switches_from_config,
)
from .default_pricing import DEFAULT_PRICING

PLUGIN_NAME = "astrbot_plugin_cost_control"

# 缓存诊断说明：展示端统一引用，确保口径一致。
CACHE_NOTE = (
    "计算说明：缓存命中率 = cache_read / (cache_read + 非缓存输入 + cache_creation) × 100，"
    "取窗口内各请求的算术平均；token 三类（缓存命中 / 缓存未命中 / 输出）来自 ProviderStat 原生记录。"
    "优化潜力按平均命中率分档：≥80% 优秀（无需优化）/ 60–80% 低 / 40–60% 中 / <40% 高。"
    "基于已记录的样本数据统计，仅供趋势参考。"
)


class WebApiMixin:
    """注册 REST Web API 路由的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any
    # 兄弟 Mixin 提供。
    build_report: Any
    get_budgets: Any
    get_budgets_cost: Any
    get_budget_overrides: Any
    get_fallback_providers: Any
    query_usage: Any
    query_usage_grouped: Any
    query_supplements: Any
    query_user_token_total: Any
    query_user_cost_total: Any
    query_usage_timeseries: Any
    query_cache_events: Any
    cleanup_old_supplements: Any
    get_pricing: Any
    get_data_dir: Any
    register_cron: Any
    daily_report: Any

    def register_routes(self) -> None:
        """注册所有 Web API 路由（在 ``Main.initialize`` 中调用，幂等）。

        route 自带 ``/astrbot_plugin_cost_control/`` 命名空间。任何注册异常仅记日志，
        不阻断插件加载（对应端点不可用，其余能力不受影响）。
        """
        try:
            reg = self.context.register_web_api
            prefix = f"/{PLUGIN_NAME}"
            routes: list[tuple[str, Any, list[str], str]] = [
                (f"{prefix}/overview", self.api_overview, ["GET"], "总览聚合报表"),
                (
                    f"{prefix}/alerts",
                    self.api_alerts,
                    ["GET"],
                    "总览告警列表（缓存率/未定价/预算等）",
                ),
                (f"{prefix}/report", self.api_report, ["GET"], "综合报表（同 overview）"),
                (f"{prefix}/compare", self.api_compare, ["GET"], "环比对比（当前 vs 上一窗口）"),
                (f"{prefix}/timeline", self.api_timeline, ["GET"], "时序趋势（按天/小时分桶）"),
                (f"{prefix}/records", self.api_records, ["GET"], "每请求明细记录"),
                (
                    f"{prefix}/records/aggregate",
                    self.api_records_aggregate,
                    ["GET"],
                    "明细二级聚合（按模型/会话）",
                ),
                (f"{prefix}/budgets", self.api_budgets, ["GET"], "预算配置与消耗"),
                (f"{prefix}/providers", self.api_providers, ["GET"], "可用 Provider 列表"),
                (f"{prefix}/cache", self.api_cache, ["GET"], "缓存命中率与诊断事件"),
                (f"{prefix}/attribution", self.api_attribution, ["GET"], "归因报表"),
                (f"{prefix}/pricing", self.api_pricing, ["GET"], "模型单价表"),
                (f"{prefix}/config", self.api_config, ["GET"], "当前插件配置"),
                (f"{prefix}/actions/cleanup", self.api_action_cleanup, ["POST"], "手动清理"),
                (f"{prefix}/actions/report", self.api_action_report, ["POST"], "手动推送日报"),
                (
                    f"{prefix}/actions/save_config",
                    self.api_action_save_config,
                    ["POST"],
                    "保存预算/策略配置（热生效）",
                ),
                (
                    f"{prefix}/actions/sync_rates",
                    self.api_action_sync_rates,
                    ["POST"],
                    "同步最新汇率",
                ),
            ]
            for route, handler, methods, desc in routes:
                try:
                    reg(route, handler, methods, desc)
                except Exception:
                    pass  # 单条失败不影响其它端点
        except Exception:
            pass

    # ===== 通用辅助 =====

    @staticmethod
    def _ok(data: Any = None, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"success": True}
        if data is not None:
            out["data"] = data
        out.update(extra)
        return out

    @staticmethod
    def _err(message: str) -> dict[str, Any]:
        return {"success": False, "error": message}

    @staticmethod
    def _param(name: str, default: str = "") -> str:
        """从 Quart 全局 ``request.args`` 读 query 参数（延迟导入）。"""
        try:
            from quart import request

            return request.args.get(name, default)
        except Exception:
            return default

    @staticmethod
    def _parse_iso(s: str | None) -> datetime | None:
        """把 ISO 字符串解析为 aware UTC datetime（失败返回 None）。

        接受 ``2026-06-15``（按 UTC 00:00）、``2026-06-15T01:02:03``、
        带偏移的 ISO。无时区信息者按 UTC 处理。
        """
        if not s:
            return None
        try:
            from datetime import UTC, timedelta

            raw = str(s).strip()
            # 纯日期补全为 00:00:00
            if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
                raw = raw + "T00:00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            # end 区间包容到当天结束：纯日期输入顺延至次日 00:00
            if len(str(s).strip()) == 10:
                dt = dt + timedelta(days=1)
            return dt
        except Exception:
            return None

    @staticmethod
    def _supplement_to_dict(
        s: Any,
        pricing: dict[str, Any] | None = None,
        main_cur: str = "$",
        rates: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """把 ``CostSupplement`` 行序列化为 JSON 友好 dict。

        ``pricing`` 非空时按生效定价算出本条成本 ``cost``（未定价为 0.0）。per_request
        模式单条无法独立计费（需 distinct request_id 聚合），此处按 0 近似。

        若记录有固化的 ``cost_amount`` + ``currency_symbol``，则 ``cost`` 按当前汇率
        从固化的原始货币换算到主货币 ``main_cur``，``cost_original`` 保留原始金额；
        否则 ``cost`` 由 pricing 即时算出（回退，用于历史未回填行）。
        """
        from .cost import compute_cost_value
        from .exchange_rates import convert

        created = getattr(s, "created_at", None)
        token_input_other = int(getattr(s, "token_input_other", 0) or 0)
        token_input_cached = int(getattr(s, "token_input_cached", 0) or 0)
        token_output = int(getattr(s, "token_output", 0) or 0)
        cache_creation = getattr(s, "cache_creation", None)

        cost_amount = getattr(s, "cost_amount", None)
        currency_symbol = getattr(s, "currency_symbol", None)

        cost = 0.0
        cost_original = None
        if cost_amount is not None:
            # 有固化金额 → 按当前汇率换算到主货币
            cost_original = round(float(cost_amount), 6)
            cur = str(currency_symbol or "USD")
            cost = round(convert(cost_original, cur, main_cur, rates or {}), 6)
        elif pricing is not None:
            # 回退：无固化金额，按定价即时算（USD 口径）
            cost = round(
                compute_cost_value(
                    {
                        "token_input_other": token_input_other,
                        "token_input_cached": token_input_cached,
                        "token_output": token_output,
                        "cache_creation": cache_creation,
                    },
                    getattr(s, "provider_id", None) or None,
                    getattr(s, "provider_model", None),
                    pricing,
                ),
                6,
            )

        return {
            "umo": getattr(s, "umo", "") or "",
            "provider_id": getattr(s, "provider_id", "") or "",
            "provider_model": getattr(s, "provider_model", None),
            "conversation_id": getattr(s, "conversation_id", None),
            "token_input_other": token_input_other,
            "token_input_cached": token_input_cached,
            "token_output": token_output,
            "cache_creation": cache_creation,
            "cache_read": getattr(s, "cache_read", None),
            "injection_total": getattr(s, "injection_total", None),
            "attribution": getattr(s, "attribution", None),
            "cost": cost,
            "cost_original": cost_original,
            "currency_symbol": currency_symbol,
            "created_at": created.isoformat() if created else None,
        }

    # ===== 端点 =====

    async def api_overview(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /overview``：总览聚合报表（用量 / 成本 / 缓存 / 归因）。"""
        try:
            window = self._param("window", "daily") or "daily"
            report = await self.build_report(window=window)
            return self._ok(report)
        except Exception as e:
            return self._err(str(e))

    async def api_alerts(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /alerts``：总览页顶部告警列表（黄色提醒，引导用户处理）。

        Query：``window``（daily|weekly|monthly，默认 daily）—— 仅影响缓存率
        统计的窗口；未定价 / 预算告警基于全量历史与当前配置，不受窗口影响。

        每条告警：``{level, code, title, detail, tab}``。``tab`` 为前端目标页
        （cache|pricing|budgets），用于点击跳转。任一子检查异常降级跳过，绝不
        阻断其它告警。
        """
        alerts: list[dict[str, Any]] = []
        window = self._param("window", "daily") or "daily"

        # 1. 缓存命中率偏低（与 /cache 同口径，但独立查询避免互相依赖）
        try:
            from datetime import UTC, datetime

            from .analytics import report_window_start
            from .cache_diag import hit_rate

            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            start = report_window_start(window, now, tz, refresh)
            sups = await self.query_supplements(start=start, limit=5000)
            rates: list[float] = []
            for s in sups:
                cache_read = getattr(s, "cache_read", None)
                if cache_read is None:
                    cache_read = getattr(s, "token_input_cached", None)
                rate = hit_rate(
                    cache_read,
                    getattr(s, "token_input_other", None),
                    getattr(s, "cache_creation", None),
                )
                if rate >= 0:
                    rates.append(rate)
            samples = len(rates)
            avg = round(sum(rates) / samples, 1) if samples else 0.0
            # 样本>=5 且命中率<30% 才告警，避免小样本噪音
            if samples >= 5 and avg < 30:
                alerts.append(
                    {
                        "level": "warn",
                        "code": "low_cache_rate",
                        "title": "缓存命中率偏低",
                        "detail": (
                            f"当前窗口平均缓存命中率 {avg}%（{samples} 样本）。"
                            "缓存命中单价仅为非缓存的 1/10，提升命中率可显著降低成本。"
                            "排查 system prompt 稳定性、上下文重置、工具定义频繁变化。"
                        ),
                        "tab": "cache",
                    }
                )
        except Exception:
            pass

        # 2. 存在未定价模型（与 /pricing 同口径）
        try:
            from .cost import resolve_pricing

            pricing = self.get_pricing()
            unpriced_count = 0
            unpriced_tokens = 0
            rows = await self.query_usage_grouped(by="provider_model")
            for r in rows:
                provider_id = r.get("provider_id") or ""
                model = r.get("provider_model") or ""
                if model and resolve_pricing(provider_id or None, model, pricing) is None:
                    unpriced_count += 1
                    unpriced_tokens += (
                        int(r.get("token_input_other", 0) or 0)
                        + int(r.get("token_input_cached", 0) or 0)
                        + int(r.get("token_output", 0) or 0)
                    )
            if unpriced_count > 0:
                alerts.append(
                    {
                        "level": "warn",
                        "code": "unpriced_models",
                        "title": "存在未定价模型",
                        "detail": (
                            f"检测到 {unpriced_count} 个模型未配置定价"
                            f"（涉及 {unpriced_tokens} token 用量），其成本被计为 $0，"
                            "导致成本统计偏低。请前往定价页为对应 provider 设置单价。"
                        ),
                        "tab": "pricing",
                    }
                )
        except Exception:
            pass

        # 3. 预算超限 / 接近超限（复用 /budgets 的全局维度判定）
        try:
            from .budget import _DIM_ORDER, day_window_start, month_window_start, total_tokens

            cfg = getattr(self, "cfg", None)
            limits = self.get_budgets()
            limits_cost = self.get_budgets_cost()
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(cfg, "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)
            day_total = total_tokens(await self.query_usage(start=d_start))
            month_total = total_tokens(await self.query_usage(start=m_start))

            has_cost = any(float(limits_cost.get(d, 0) or 0) > 0 for d in _DIM_ORDER)
            if has_cost:
                from .cost import compute_cost_grouped_in_main
                from .exchange_rates import get_main_currency, get_rates

                _main_cur = get_main_currency(cfg)
                _rates = get_rates(cfg)
                pricing = self.get_pricing()
                day_cost = compute_cost_grouped_in_main(
                    await self.query_usage_grouped(by="provider_model", start=d_start),
                    pricing,
                    _main_cur,
                    _rates,
                )
                month_cost = compute_cost_grouped_in_main(
                    await self.query_usage_grouped(by="provider_model", start=m_start),
                    pricing,
                    _main_cur,
                    _rates,
                )
            else:
                day_cost = month_cost = 0.0

            dim_used_t = {"global_daily": day_total, "global_monthly": month_total}
            dim_used_c = {"global_daily": day_cost, "global_monthly": month_cost}
            dim_label = {"global_daily": "每日全局", "global_monthly": "每月全局"}

            # 将各维度 cost 限额换算到主货币（budgets_cost_currency 可能设了独立货币）
            from .config import get_budgets_cost_currency
            from .exchange_rates import convert as _conv_alert, get_main_currency, get_rates

            _alert_bcc = get_budgets_cost_currency(cfg)
            _alert_main = get_main_currency(cfg)
            _alert_rates = get_rates(cfg)

            exceeded_dims: list[str] = []
            near_dims: list[str] = []
            for d in _DIM_ORDER:
                lt = float(limits.get(d, 0) or 0)
                lc_raw = float(limits_cost.get(d, 0) or 0)
                d_cur = str(_alert_bcc.get(d, "") or "") or _alert_main
                lc = (
                    round(_conv_alert(lc_raw, d_cur, _alert_main, _alert_rates), 6)
                    if lc_raw > 0 and d_cur != _alert_main
                    else lc_raw
                )
                for metric, limit, used in (
                    ("token", lt, dim_used_t.get(d, 0)),
                    ("cost", lc, dim_used_c.get(d, 0)),
                ):
                    if limit <= 0:
                        continue
                    ratio = used * 100.0 / limit
                    label = f"{dim_label.get(d, d)}·{metric}"
                    if used >= limit:
                        exceeded_dims.append(f"{label}（{ratio:.0f}%）")
                    elif ratio >= 80:
                        near_dims.append(f"{label}（{ratio:.0f}%）")

            if exceeded_dims:
                alerts.append(
                    {
                        "level": "warn",
                        "code": "budget_exceeded",
                        "title": "预算已超限",
                        "detail": (
                            f"以下 {len(exceeded_dims)} 项预算已超限："
                            + "、".join(exceeded_dims[:4])
                            + ("…" if len(exceeded_dims) > 4 else "")
                            + "。请前往预算页调整限额或超限策略。"
                        ),
                        "tab": "budgets",
                    }
                )
            if near_dims:
                alerts.append(
                    {
                        "level": "warn",
                        "code": "budget_near_limit",
                        "title": "预算接近超限",
                        "detail": (
                            f"以下 {len(near_dims)} 项预算使用率超过 80%："
                            + "、".join(near_dims[:4])
                            + ("…" if len(near_dims) > 4 else "")
                            + "。建议提前关注用量趋势。"
                        ),
                        "tab": "budgets",
                    }
                )
        except Exception:
            pass

        return self._ok(alerts)

    async def api_report(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /report``：综合报表（与 overview 等价，保留独立语义）。"""
        return await self.api_overview(**kwargs)

    async def api_compare(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /compare``：当前窗口 vs 上一窗口的 cost/count/tokens 环比。

        Query：``window``（daily|weekly|monthly，默认 daily）。当前窗口与报表口径
        一致，上一窗口为紧邻的等长（月度按自然月）上一段（见
        :func:`analytics.compare_windows`）。``previous`` 为 0 时对应 ``delta`` 百分比
        为 ``null``（前端显示「新增」）。
        """
        try:
            from datetime import UTC, datetime

            from .analytics import compare_windows
            from .budget import total_tokens
            from .cost import compute_cost_grouped

            window = self._param("window", "daily") or "daily"
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            cur_start, cur_end, prev_start, prev_end = compare_windows(window, now, tz, refresh)
            pricing = self.get_pricing()
            from .config import get_currency_symbol, get_rates

            main_cur = get_currency_symbol(getattr(self, "cfg", None))
            rates = get_rates(getattr(self, "cfg", None))
            from .cost import compute_cost_grouped_in_main

            async def _stats(start: datetime, end: datetime) -> dict[str, Any]:
                usage = await self.query_usage(start=start, end=end)
                rows = await self.query_usage_grouped(by="provider_model", start=start, end=end)
                cost = compute_cost_grouped_in_main(rows, pricing, main_cur, rates)
                return {
                    "cost": cost,
                    "count": int(usage.get("count", 0) or 0),
                    "tokens": total_tokens(usage),
                }

            cur = await _stats(cur_start, cur_end)
            prev = await _stats(prev_start, prev_end)

            def _pct(c: float, p: float) -> float | None:
                return round((c - p) * 100.0 / p, 1) if p > 0 else None

            label = "昨日" if window == "daily" else "近 7 天" if window == "weekly" else "近 30 天"
            return self._ok(
                {
                    "window": window,
                    "current": cur,
                    "previous": prev,
                    "delta": {
                        "cost_pct": _pct(cur["cost"], prev["cost"]),
                        "count_pct": _pct(cur["count"], prev["count"]),
                        "tokens_pct": _pct(cur["tokens"], prev["tokens"]),
                    },
                    "label": label,
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_timeline(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /timeline``：用量 / 调用次数时序（基于 ProviderStat 全量历史）。

        Query：``days``（默认 14，上限 90）、``bucket``（day|hour，默认 day）、
        ``umo`` / ``provider`` / ``model``（可选筛选）。
        """
        try:
            from datetime import UTC, timedelta

            try:
                days = max(1, min(90, int(self._param("days", "14"))))
            except (TypeError, ValueError):
                days = 14
            bucket = self._param("bucket", "day") or "day"
            end = datetime.now(UTC)
            start = end - timedelta(days=days)
            umo = self._param("umo") or None
            provider = self._param("provider") or None
            model = self._param("model") or None
            series = await self.query_usage_timeseries(
                start=start,
                end=end,
                bucket=bucket,
                umo=umo,
                provider=provider,
                model=model,
            )

            # 按桶计算成本趋势：拉取 (bucket, model) 粒度用量 → 逐桶按模型定价核算
            cost_series: list[dict[str, Any]] = []
            try:
                from .cost import compute_row_cost_in_main
                from .exchange_rates import get_main_currency, get_rates

                pricing = self.get_pricing()
                _mc = get_main_currency(getattr(self, "cfg", None))
                _rt = get_rates(getattr(self, "cfg", None))
                model_rows = await self.query_usage_timeseries_by_model(
                    start=start,
                    end=end,
                    bucket=bucket,
                    umo=umo,
                    provider=provider,
                    model=model,
                )
                # 按 bucket 聚合成本
                bucket_cost: dict[str, float] = {}
                for mr in model_rows:
                    bk = str(mr.get("bucket") or "")
                    if not bk:
                        continue
                    c = compute_row_cost_in_main(mr, pricing, _mc, _rt)
                    bucket_cost[bk] = bucket_cost.get(bk, 0.0) + c
                cost_series = [
                    {"bucket": bk, "cost": round(v, 6)}
                    for bk, v in sorted(bucket_cost.items())
                ]
            except Exception:
                pass

            return self._ok(
                {
                    "series": series,
                    "cost_series": cost_series,
                    "bucket": bucket,
                    "days": days,
                    "coverage_note": "基于 ProviderStat 全量历史（按 UTC 分桶，展示为本地日）",
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_records(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /records``：每请求补充明细（umo / model / token 三类 / cache / 归因）。

        Query：``umo`` / ``provider`` / ``model``（可选筛选）、``start`` / ``end``
        （ISO）、``order_by``（created_at|token_input_other|token_output|umo）、
        ``order_dir``（desc|asc）、``limit``（默认 100，上限 1000）。
        """
        try:
            umo = self._param("umo") or None
            provider = self._param("provider") or None
            model = self._param("model") or None
            start = self._parse_iso(self._param("start"))
            end = self._parse_iso(self._param("end"))
            try:
                limit = max(1, min(1000, int(self._param("limit", "100"))))
            except (TypeError, ValueError):
                limit = 100
            order_by = self._param("order_by", "created_at") or "created_at"
            order_dir = self._param("order_dir", "desc") or "desc"
            rows = await self.query_supplements(
                umo=umo,
                provider_id=provider,
                provider_model=model,
                start=start,
                end=end,
                limit=limit,
                order_by=order_by,
                order_dir=order_dir,
            )
            pricing = self.get_pricing()
            from .config import get_currency_symbol, get_rates

            main_cur = get_currency_symbol(getattr(self, "cfg", None))
            rates = get_rates(getattr(self, "cfg", None))
            return self._ok([self._supplement_to_dict(r, pricing, main_cur, rates) for r in rows])
        except Exception as e:
            return self._err(str(e))

    async def api_records_aggregate(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /records/aggregate``：按模型 / provider / 会话聚合 ProviderStat 用量。

        Query：``by``（model|provider|umo，默认 model）、``umo`` / ``provider`` / ``model``
        （可选）、``start`` / ``end``（ISO）。返回每组的 token 三类、条数、成本、占比。
        """
        try:
            from .cost import compute_row_cost

            by = self._param("by", "model") or "model"
            if by not in ("model", "provider", "umo"):
                by = "model"
            start = self._parse_iso(self._param("start"))
            end = self._parse_iso(self._param("end"))
            umo = self._param("umo") or None
            provider = self._param("provider") or None
            model = self._param("model") or None
            pricing = self.get_pricing()
            from .config import get_currency_symbol, get_rates

            main_cur = get_currency_symbol(getattr(self, "cfg", None))
            rates = get_rates(getattr(self, "cfg", None))
            from .cost import compute_row_cost_in_main

            # model / provider 维度：用 (provider_id, provider_model) 底层行精确算成本
            # （按 provider_id 匹配用户定价），再二次聚合到请求维度。umo 维度底层无
            # provider/model，成本无法精确（维持 0，已知局限）。
            groups: dict[str, dict[str, Any]] = {}
            if by == "umo":
                rows = await self.query_usage_grouped(
                    by="umo",
                    umo=umo,
                    provider=provider,
                    model=model,
                    start=start,
                    end=end,
                )
                for r in rows:
                    key = r.get("key") or ""
                    g = groups.setdefault(
                        key,
                        {
                            "key": key,
                            "count": 0,
                            "tokens": 0,
                            "token_input_other": 0,
                            "token_input_cached": 0,
                            "token_output": 0,
                            "cost": 0.0,
                        },
                    )
                    tio = int(r.get("token_input_other", 0) or 0)
                    tic = int(r.get("token_input_cached", 0) or 0)
                    tio_out = int(r.get("token_output", 0) or 0)
                    g["count"] += int(r.get("count", 0) or 0)
                    g["tokens"] += tio + tic + tio_out
                    g["token_input_other"] += tio
                    g["token_input_cached"] += tic
                    g["token_output"] += tio_out
            else:
                pm_rows = await self.query_usage_grouped(
                    by="provider_model",
                    umo=umo,
                    provider=provider,
                    model=model,
                    start=start,
                    end=end,
                )
                for r in pm_rows:
                    key = (r.get("provider_model") if by == "model" else r.get("provider_id")) or ""
                    g = groups.setdefault(
                        key,
                        {
                            "key": key,
                            "count": 0,
                            "tokens": 0,
                            "token_input_other": 0,
                            "token_input_cached": 0,
                            "token_output": 0,
                            "cost": 0.0,
                        },
                    )
                    tio = int(r.get("token_input_other", 0) or 0)
                    tic = int(r.get("token_input_cached", 0) or 0)
                    tio_out = int(r.get("token_output", 0) or 0)
                    g["count"] += int(r.get("count", 0) or 0)
                    g["tokens"] += tio + tic + tio_out
                    g["token_input_other"] += tio
                    g["token_input_cached"] += tic
                    g["token_output"] += tio_out
                    g["cost"] += compute_row_cost_in_main(r, pricing, main_cur, rates)

            total_tokens = sum(int(g["tokens"]) for g in groups.values())
            out: list[dict[str, Any]] = []
            for g in groups.values():
                g["cost"] = round(float(g["cost"]), 6)
                g["pct"] = round(g["tokens"] * 100.0 / total_tokens, 1) if total_tokens else 0.0
                out.append(g)
            out.sort(key=lambda x: x["cost"], reverse=True)
            return self._ok(
                {
                    "by": by,
                    "total_tokens": total_tokens,
                    "groups": out,
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_budgets(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /budgets``：预算配置 + 5 维全局消耗 + 局部阈值 + 备用 Provider 库。

        返回结构：
        ```
        {
          "limits": {dim: int},
          "limits_cost": {dim: float},
          "dimensions": {dim: {token: {limit, used, ratio, exceeded, top_key, note}, cost: ...}},
          "overrides": [
            {id, enabled, target_type, target_value, token_limit, cost_limit,
             on_exceeded, stop_message, fallback_provider_ids, fallback_token_limit,
             current: {token: {used, ratio, exceeded}, cost: {used, ratio, exceeded}}},
            ...
          ],
          "fallback_providers": [{id, enabled, note}, ...],
          "global_default_on_exceeded": "stop"|"fallback"|"warn",
        }
        ```
        全局维度给精确消耗；局部维度（per_session/per_user/per_model）给「本周期消耗
        最多的会话/模型」代表值（运行时按请求 umo/model 实时拦截，无单一全局值）。
        """
        try:
            from datetime import UTC, datetime

            from .budget import (
                _DIM_ORDER,
                day_window_start,
                default_on_exceeded,
                get_budget_overrides,
                get_fallback_providers,
                month_window_start,
                total_tokens,
            )

            cfg = getattr(self, "cfg", None)
            limits = self.get_budgets()
            limits_cost = self.get_budgets_cost()
            has_cost = any(float(limits_cost.get(d, 0) or 0) > 0 for d in _DIM_ORDER)
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(cfg, "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)
            day_usage = await self.query_usage(start=d_start)
            month_usage = await self.query_usage(start=m_start)
            day_total = total_tokens(day_usage)
            month_total = total_tokens(month_usage)

            # 局部维度的代表值：今日消耗最多的会话 / 模型
            top_session = await self.query_usage_grouped(by="umo", start=d_start)
            top_session.sort(key=lambda r: total_tokens(r), reverse=True)
            ses_used = total_tokens(top_session[0]) if top_session else 0
            ses_key = str((top_session[0] or {}).get("key", "")) if top_session else ""
            top_model = await self.query_usage_grouped(by="provider_model", start=d_start)
            top_model.sort(key=lambda r: total_tokens(r), reverse=True)
            mod_used = total_tokens(top_model[0]) if top_model else 0
            mod_key = str((top_model[0] or {}).get("provider_model", "")) if top_model else ""

            # 主货币 + 汇率（成本维度统一换算到主货币口径）
            from .exchange_rates import get_main_currency, get_rates

            main_cur = get_main_currency(cfg)
            rates = get_rates(cfg)

            if has_cost:
                from .cost import compute_cost_grouped_in_main, compute_row_cost_in_main

                pricing = self.get_pricing()
                day_cost = compute_cost_grouped_in_main(
                    await self.query_usage_grouped(by="provider_model", start=d_start),
                    pricing,
                    main_cur,
                    rates,
                )
                month_cost = compute_cost_grouped_in_main(
                    await self.query_usage_grouped(by="provider_model", start=m_start),
                    pricing,
                    main_cur,
                    rates,
                )
                ses_cost = (
                    compute_cost_grouped_in_main(
                        await self.query_usage_grouped(
                            by="provider_model", umo=ses_key, start=d_start
                        ),
                        pricing,
                        main_cur,
                        rates,
                    )
                    if ses_key
                    else 0.0
                )
                mod_cost = (
                    round(
                        compute_row_cost_in_main(top_model[0], pricing, main_cur, rates), 6
                    )
                    if top_model
                    else 0.0
                )
            else:
                day_cost = month_cost = ses_cost = mod_cost = 0.0

            def _part(limit: Any, used: Any) -> dict[str, Any]:
                limit = float(limit or 0)
                used = float(used or 0)
                return {
                    "limit": limit,
                    "used": used,
                    "ratio": round(used * 100.0 / limit, 1) if limit > 0 else 0.0,
                    "exceeded": limit > 0 and used >= limit,
                }

            def _dim_entry(
                key: str, used_t: Any, used_c: Any, top_key: str = "", note: str = ""
            ) -> dict[str, Any]:
                return {
                    "token": {
                        **_part(limits.get(key, 0), used_t),
                        "top_key": top_key,
                        "note": note,
                    },
                    "cost": {
                        **_part(limits_cost.get(key, 0), used_c),
                        "top_key": top_key,
                        "note": note,
                    },
                }

            # ===== 局部阈值：聚合每条规则的实时 used =====
            overrides_raw = get_budget_overrides(cfg)
            pricing = self.get_pricing() if has_cost or overrides_raw else {}
            if has_cost or overrides_raw:
                from .cost import compute_cost_grouped_in_main
                from .exchange_rates import convert as _conv

            overrides_out: list[dict[str, Any]] = []
            for idx, ov in enumerate(overrides_raw):
                used_t_v = 0.0
                used_c_v = 0.0
                tt = str(ov.get("target_type") or "")
                tv = str(ov.get("target_value") or "")
                try:
                    if tt == "umo":
                        if ov.get("token_limit", 0) > 0:
                            used_t_v = float(
                                total_tokens(await self.query_usage(umo=tv, start=d_start))
                            )
                        if ov.get("cost_limit", 0) > 0:
                            used_c_v = compute_cost_grouped_in_main(
                                await self.query_usage_grouped(
                                    by="provider_model", umo=tv, start=d_start
                                ),
                                pricing,
                                main_cur,
                                rates,
                            )
                    elif tt == "provider":
                        if ov.get("token_limit", 0) > 0:
                            used_t_v = float(
                                total_tokens(await self.query_usage(provider=tv, start=d_start))
                            )
                        if ov.get("cost_limit", 0) > 0:
                            used_c_v = compute_cost_grouped_in_main(
                                await self.query_usage_grouped(
                                    by="provider_model", provider=tv, start=d_start
                                ),
                                pricing,
                                main_cur,
                                rates,
                            )
                    elif tt == "user":
                        if ov.get("token_limit", 0) > 0 and hasattr(self, "query_user_token_total"):
                            used_t_v = float(await self.query_user_token_total(tv, d_start))
                        if ov.get("cost_limit", 0) > 0 and hasattr(self, "query_user_cost_total"):
                            # query_user_cost_total 返回 USD 口径，换算到主货币
                            _uc = float(await self.query_user_cost_total(tv, d_start, pricing))
                            used_c_v = round(_conv(_uc, "USD", main_cur, rates), 6)
                except Exception:
                    # 单条 override 聚合失败不影响其它条
                    pass

                def _ratio(used: float, limit: Any) -> dict[str, Any]:
                    limit_f = float(limit or 0)
                    return {
                        "used": round(used, 6),
                        "ratio": round(used * 100.0 / limit_f, 1) if limit_f > 0 else 0.0,
                        "exceeded": limit_f > 0 and used >= limit_f,
                    }

                overrides_out.append(
                    {
                        "id": f"ovo_{idx}",
                        "enabled": bool(ov.get("enabled", True)),
                        "target_type": tt,
                        "target_value": tv,
                        "token_limit": int(ov.get("token_limit") or 0),
                        "cost_limit": float(ov.get("cost_limit") or 0.0),
                        "cost_currency": str(ov.get("cost_currency") or ""),
                        "on_exceeded": str(ov.get("on_exceeded") or "stop"),
                        "stop_message": str(ov.get("stop_message") or ""),
                        "fallback_provider_ids": list(ov.get("fallback_provider_ids") or []),
                        "fallback_token_limit": int(ov.get("fallback_token_limit") or 0),
                        "current": {
                            "token": _ratio(used_t_v, ov.get("token_limit", 0)),
                            "cost": _ratio(used_c_v, ov.get("cost_limit", 0)),
                        },
                    }
                )

            fallback_providers = get_fallback_providers(cfg)

            # 各维度 cost 限额的独立货币（budgets_cost_currency）
            from .config import get_budgets_cost_currency, get_currency_symbol
            from .exchange_rates import get_rate_updated_at

            return self._ok(
                {
                    "limits": limits,
                    "limits_cost": limits_cost,
                    "limits_cost_currency": get_budgets_cost_currency(cfg),
                    "currency_symbol": get_currency_symbol(cfg),
                    "exchange_rates": rates,
                    "exchange_rates_updated_at": get_rate_updated_at(cfg),
                    "dimensions": {
                        "global_daily": _dim_entry("global_daily", day_total, day_cost),
                        "global_monthly": _dim_entry("global_monthly", month_total, month_cost),
                        "per_session_daily": _dim_entry(
                            "per_session_daily",
                            ses_used,
                            ses_cost,
                            ses_key,
                            "今日消耗最多的会话",
                        ),
                        "per_user_daily": _dim_entry(
                            "per_user_daily",
                            ses_used,
                            ses_cost,
                            ses_key,
                            "展示为今日消耗最多的会话；运行时按请求 user_id 跨会话聚合拦截",
                        ),
                        "per_model_daily": _dim_entry(
                            "per_model_daily",
                            mod_used,
                            mod_cost,
                            mod_key,
                            "今日消耗最多的模型",
                        ),
                    },
                    "overrides": overrides_out,
                    "fallback_providers": fallback_providers,
                    "global_default_on_exceeded": default_on_exceeded(cfg),
                }
            )
        except Exception as e:
            return self._err(str(e))

    def _collect_provider_models(self) -> list[dict[str, Any]]:
        """从 ``context.get_config()`` 遍历 provider 配置，返回 provider + 候选模型列表。

        每个 provider 读 ``id`` / ``type`` / 顶层 ``model``（主模型，回退
        ``model_config.model``）/ 顶层 ``model_list``（候选模型数组，回退
        ``model_config.model_list``，每项 ``model_name`` / ``enable``），合并主模型
        + 启用候选，按 id 去重，返回 ``[{id, model, type, candidates: [str, ...]}]``。

        字段位置：4.25.5 起 provider 的 ``model`` / ``model_list`` 在顶层（已核对
        ``astrbot/core/config/default.py``），旧版在 ``model_config`` 内，故两层兼容。
        三级降级：顶层/model_config 读 model → 回退 ``get_all_providers().meta()``
        单 model → 空列表。全步骤 try 包裹，绝不抛异常。
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        # 1. 优先从主配置读 provider 全量模型（含 model_list 候选）
        try:
            cfg = self.context.get_config() or {}
            prov_list = cfg.get("provider") if isinstance(cfg, dict) else None
            if isinstance(prov_list, list):
                for p in prov_list:
                    try:
                        if not isinstance(p, dict):
                            continue
                        pid = str(p.get("id") or "").strip()
                        if not pid or pid in seen:
                            continue
                        seen.add(pid)
                        ptype = str(p.get("type") or "")
                        mc = p.get("model_config") or {}
                        if not isinstance(mc, dict):
                            mc = {}
                        # 主模型:顶层 model(4.25.5+) → model_config.model(旧版)
                        main_model = str(p.get("model") or mc.get("model") or "").strip() or None
                        candidates: list[str] = []
                        # 候选列表:顶层 model_list → model_config.model_list
                        ml = p.get("model_list")
                        if ml is None:
                            ml = mc.get("model_list")
                        if isinstance(ml, list):
                            for it in ml:
                                if not isinstance(it, dict):
                                    continue
                                if not it.get("enable", True):
                                    continue
                                m = str(it.get("model_name") or it.get("model") or "").strip()
                                if m and m not in candidates:
                                    candidates.append(m)
                        if main_model and main_model not in candidates:
                            candidates.insert(0, main_model)
                        out.append(
                            {
                                "id": pid,
                                "model": main_model or "",
                                "type": ptype,
                                "candidates": candidates,
                            }
                        )
                    except Exception:
                        continue
        except Exception:
            pass
        # 2. 运行时 get_all_providers().meta() 兜底:补 model 为空的 provider,并追加
        #    配置里没有的运行时 provider(meta() 字段最可靠,解决个别 provider type
        #    配置结构特殊导致顶层 model 读不到的情况)。
        try:
            all_provs = self.context.get_all_providers()
        except Exception:
            all_provs = []
        by_id: dict[str, dict[str, Any]] = {e["id"]: e for e in out}
        for p in all_provs or []:
            try:
                meta = p.meta()
                pid = str(getattr(meta, "id", "") or "").strip()
                if not pid:
                    continue
                model = str(getattr(meta, "model", "") or "").strip()
                mtype = str(getattr(meta, "type", "") or "")
                if pid in by_id:
                    entry = by_id[pid]
                    if not entry["model"] and model:
                        entry["model"] = model
                    if model and model not in entry["candidates"]:
                        entry["candidates"].insert(0, model)
                    if not entry["type"] and mtype:
                        entry["type"] = mtype
                elif pid not in seen:
                    seen.add(pid)
                    entry = {
                        "id": pid,
                        "model": model,
                        "type": mtype,
                        "candidates": [model] if model else [],
                    }
                    by_id[pid] = entry
                    out.append(entry)
            except Exception:
                continue
        return out

    async def api_providers(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /providers``：astrbot 当前配置的 Provider 列表（含候选模型）。

        经 :meth:`_collect_provider_models` 取全部 provider 及其候选模型。任一步异常
        兜空，绝不抛出。
        """
        try:
            return self._ok({"providers": self._collect_provider_models()})
        except Exception as e:
            return self._err(str(e))

    async def api_cache(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /cache``：缓存命中率 + 所有会话最近的破坏诊断事件。

        Query：``window``（daily|weekly|monthly，默认 daily）按报表窗口
        （见 :func:`analytics.report_window_start`）过滤命中率与 token 聚合统计；
        ``limit``（默认 100，上限 500）。缓存破坏事件始终取最近 ``limit`` 条，
        **不受 window 影响**。
        """
        try:
            from datetime import UTC, datetime

            from .analytics import report_window_start

            try:
                limit = max(1, min(500, int(self._param("limit", "100"))))
            except (TypeError, ValueError):
                limit = 100
            window = self._param("window", "daily") or "daily"
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            start = report_window_start(window, now, tz, refresh)
            sups = await self.query_supplements(start=start, limit=limit)
            from .cache_diag import hit_rate

            rates: list[float] = []
            total_input_other = 0
            total_input_cached = 0
            total_output = 0
            for s in sups:
                cache_read = getattr(s, "cache_read", None)
                if cache_read is None:
                    cache_read = getattr(s, "token_input_cached", None)
                rate = hit_rate(
                    cache_read,
                    getattr(s, "token_input_other", None),
                    getattr(s, "cache_creation", None),
                )
                if rate >= 0:
                    rates.append(rate)
                total_input_other += int(getattr(s, "token_input_other", 0) or 0)
                total_input_cached += int(getattr(s, "token_input_cached", 0) or 0)
                total_output += int(getattr(s, "token_output", 0) or 0)
            avg = round(sum(rates) / len(rates), 1) if rates else 0.0

            events: list[dict[str, Any]] = []
            try:
                ev_rows = await self.query_cache_events(limit=50)
                for r in ev_rows:
                    created = getattr(r, "created_at", None)
                    events.append(
                        {
                            "umo": getattr(r, "umo", "") or "",
                            "type": getattr(r, "type", "") or "",
                            "severity": getattr(r, "severity", "medium") or "medium",
                            "detail": getattr(r, "detail", "") or "",
                            "before": getattr(r, "before", None),
                            "after": getattr(r, "after", None),
                            "created_at": created.isoformat() if created else None,
                        }
                    )
            except Exception:
                events = []
            return self._ok(
                {
                    "cache_hit_rate": avg,
                    "samples": len(rates),
                    "total_input_other": total_input_other,
                    "total_input_cached": total_input_cached,
                    "total_output": total_output,
                    "events": events,
                    "cache_note": CACHE_NOTE,
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_attribution(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /attribution``：最近请求的归因（各组件占比 + 注入量）。

        Query：``window``（daily|weekly|monthly，默认 daily）按与总览一致的报表窗口
        （见 :func:`analytics.report_window_start`）过滤；``limit``（默认 50，上限 500）。
        """
        try:
            from datetime import UTC, datetime

            from .analytics import report_window_start

            try:
                limit = max(1, min(500, int(self._param("limit", "50"))))
            except (TypeError, ValueError):
                limit = 50
            window = self._param("window", "daily") or "daily"
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            start = report_window_start(window, now, tz, refresh)
            sups = await self.query_supplements(start=start, limit=limit)
            items: list[dict[str, Any]] = []
            comps: dict[str, list[int]] = {
                "system": [],
                "tools": [],
                "history": [],
                "user": [],
                "extra": [],
            }
            for s in sups:
                attr = getattr(s, "attribution", None)
                inj = getattr(s, "injection_total", None)
                created = getattr(s, "created_at", None)
                item: dict[str, Any] = {
                    "umo": getattr(s, "umo", "") or "",
                    "injection_total": inj,
                    "attribution": attr,
                    "created_at": created.isoformat() if created else None,
                }
                items.append(item)
                if isinstance(attr, dict):
                    for k in comps:
                        v = attr.get(k)
                        if isinstance(v, int):
                            comps[k].append(v)
            avg = {k: round(sum(v) / len(v)) if v else 0 for k, v in comps.items()}
            return self._ok(
                {"recent": items, "avg_components": avg, "estimation_note": ESTIMATION_NOTE}
            )
        except Exception as e:
            return self._err(str(e))

    async def api_pricing(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /pricing``：定价页所需全部数据。

        返回 ``{provider_models, user_pricing, defaults, unpriced}``：

        - ``provider_models``：当前 AstrBot 配置的全部 provider 及其候选模型（见
          :meth:`_collect_provider_models`），供前端按 provider 展示与编辑。
        - ``user_pricing``：用户自定义定价（``cfg["pricing"]``，key=provider_id）。
        - ``defaults``：内置出厂默认单价（``DEFAULT_PRICING``，key=模型名，per_token），
          供前端折叠区只读展示与 per_token 预填基准。
        - ``unpriced``：全量历史（``query_usage_grouped(by="provider_model")``）中
          :func:`cost.resolve_pricing` 解析不到定价的 (provider,model)——其成本被计
          为 0，会使成本统计偏低，需提示用户补定价。附 token 量表明失真影响范围。
        """
        try:
            from .cost import resolve_pricing

            pricing = self.get_pricing()
            unpriced: list[dict[str, Any]] = []
            # provider_id → 实际运行时模型名列表（来自用量记录，与成本计算同源）。
            # 配置里的 model / candidates 可能是路由名 / auto / 空，但运行时实际
            # 调用的模型名能匹配内置默认——用这些名字作为 matched_default 的回退。
            runtime_models: dict[str, list[str]] = {}
            try:
                rows = await self.query_usage_grouped(by="provider_model")
                for r in rows:
                    provider_id = r.get("provider_id") or ""
                    model = r.get("provider_model") or ""
                    if model and resolve_pricing(provider_id or None, model, pricing) is None:
                        tokens = (
                            int(r.get("token_input_other", 0) or 0)
                            + int(r.get("token_input_cached", 0) or 0)
                            + int(r.get("token_output", 0) or 0)
                        )
                        unpriced.append(
                            {
                                "provider_id": provider_id,
                                "model": model,
                                "tokens": tokens,
                                "count": int(r.get("count", 0) or 0),
                            }
                        )
                    if (
                        provider_id
                        and model
                        and model not in runtime_models.setdefault(provider_id, [])
                    ):
                        runtime_models[provider_id].append(model)
                unpriced.sort(key=lambda x: x["tokens"], reverse=True)
            except Exception:
                pass  # 用量查询失败不阻断定价表展示
            from .cost import _best_match_key

            provider_models = self._collect_provider_models()
            # 为每个 provider 附「实际匹配到的内置默认」(经 _best_match_key 模糊匹配,
            # 与 resolve_pricing / match_pricing 同口径),供前端定价卡提示当前生效基准，
            # 并把命中单价作为编辑框 placeholder（用户输入即覆盖）。
            # 依次尝试：主模型 → 候选模型 → 运行时实际模型（用量记录，与计费同源）
            # → provider id 自身（部分 id 直接含模型名，如 minimax/MiniMax-M2.7）。
            # 后两级解决配置 model 为路由名 / auto / 空但实际命中的模型能匹配内置默认、
            # 卡片却误显示"无内置匹配"的问题。
            for p in provider_models:
                try:
                    candidates = p.get("candidates") or []
                    model = p.get("model") or ""
                    pid = p.get("id") or ""
                    tried = [model] + [c for c in candidates if c and c != model]
                    for m in runtime_models.get(pid, []):
                        if m and m not in tried:
                            tried.append(m)
                    if pid and pid not in tried:
                        tried.append(pid)
                    md: Any = None
                    for m in tried:
                        key = _best_match_key(m, DEFAULT_PRICING)
                        if key:
                            md = {"model": key, "entry": DEFAULT_PRICING[key]}
                            break
                    p["matched_default"] = md
                except Exception:
                    p["matched_default"] = None
            from .config import get_currency_symbol
            from .exchange_rates import get_rates, get_rate_updated_at

            _pcfg = getattr(self, "cfg", None)
            return self._ok(
                {
                    "provider_models": provider_models,
                    "user_pricing": pricing.get("user", {}),
                    "defaults": DEFAULT_PRICING,
                    "unpriced": unpriced,
                    "currency_symbol": get_currency_symbol(_pcfg),
                    "exchange_rates": get_rates(_pcfg),
                    "exchange_rates_updated_at": get_rate_updated_at(_pcfg),
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_config(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /config``：当前插件配置（不含密钥，仅预算 / 单价 / 诊断等设置）。"""
        try:
            cfg = getattr(self, "cfg", None) or {}
            return self._ok(dict(cfg) if isinstance(cfg, dict) else {})
        except Exception as e:
            return self._err(str(e))

    async def api_action_cleanup(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/cleanup``：手动清理过期补充记录，返回删除条数。

        清理窗口取 ``schedule.retain_days``（<=0 表示不清理，返回 0）。
        """
        try:
            from datetime import UTC, datetime, timedelta

            sched = get_config(getattr(self, "cfg", None), "schedule", {}) or {}
            days = int(sched.get("retain_days", 0) or 0) if isinstance(sched, dict) else 0
            if days <= 0:
                return self._ok({"deleted": 0, "message": "retain_days<=0，未清理"})
            before = datetime.now(UTC) - timedelta(days=days)
            n = await self.cleanup_old_supplements(before)
            return self._ok({"deleted": n})
        except Exception as e:
            return self._err(str(e))

    async def api_action_report(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/report``：手动触发日报推送（推送给 ``alerts.daily_report_to``）。"""
        try:
            await self.daily_report()
            return self._ok({"message": "日报已触发"})
        except Exception as e:
            return self._err(str(e))

    def _validate_save_payload(self, body: Any) -> tuple[dict[str, Any] | None, str]:
        """校验 save_config 请求体，返回合并后的**全量**配置或错误信息。

        以当前 ``self.cfg`` 为底座，按 body 提供的 key 逐项强转校验
        (:func:`coerce_to_default_type`)；``budget_overrides`` 逐条过
        :func:`normalize_budget_override`（非法整条丢弃）；``fallback_providers``
        逐条过 :func:`normalize_fallback_provider`；``pricing`` 接受任意 dict；
        ``default_on_exceeded`` 限定 ``stop|fallback|warn``。未知 key 忽略。
        """
        if not isinstance(body, dict):
            return None, "请求体必须是 JSON 对象"
        cur = getattr(self, "cfg", None)
        out: dict[str, Any] = dict(cur) if isinstance(cur, dict) else {}
        for k, v in body.items():
            if k == "budget_overrides":
                if not isinstance(v, list):
                    return None, "budget_overrides 必须是数组"
                normalized: list[dict[str, Any]] = []
                for it in v:
                    n = normalize_budget_override(it)
                    if n is not None:
                        normalized.append(n)
                out[k] = normalized
            elif k == "fallback_providers":
                if not isinstance(v, list):
                    return None, "fallback_providers 必须是数组"
                normalized_fb: list[dict[str, Any]] = []
                for it in v:
                    n = normalize_fallback_provider(it)
                    if n is not None:
                        normalized_fb.append(n)
                out[k] = normalized_fb
            elif k == "default_on_exceeded":
                sv = str(v or "").strip().lower()
                if sv not in ("stop", "fallback", "warn"):
                    return None, "default_on_exceeded 必须是 stop/fallback/warn"
                out[k] = sv
            elif k == "pricing":
                if not isinstance(v, dict):
                    return None, "pricing 必须是对象（key=provider_id）"
                # 复用 config._normalize_user_entry 按 mode 规范化（key=provider_id）
                from .config import _normalize_user_entry

                normalized_p: dict[str, dict[str, Any]] = {}
                for pid, entry in v.items():
                    pid_s = str(pid).strip()
                    if not pid_s:
                        continue
                    n = _normalize_user_entry(entry)
                    if n is not None:
                        normalized_p[pid_s] = n
                out[k] = normalized_p
            elif k == "exchange_rates":
                # 接受任意 {货币代码: 汇率} dict，逐值转 float（可能含 API 同步的
                # 160+ 货币，不能用非空默认 dict 的 coerce 逻辑裁剪）
                if not isinstance(v, dict):
                    return None, "exchange_rates 必须是对象"
                rates_out: dict[str, float] = {}
                for rk, rv in v.items():
                    try:
                        rates_out[str(rk).strip().upper()] = float(rv)
                    except (TypeError, ValueError):
                        continue
                if rates_out:
                    rates_out["USD"] = 1.0  # 基准
                out[k] = rates_out
            elif k == "budgets_cost_currency":
                # {维度: 货币代码}，逐值转 str
                if not isinstance(v, dict):
                    return None, "budgets_cost_currency 必须是对象"
                bcc_out: dict[str, str] = {}
                for bk, bv in v.items():
                    bs = str(bv or "").strip().upper()
                    if bs:
                        bcc_out[str(bk)] = bs
                out[k] = bcc_out
            elif k in CONFIG_DEFAULTS:
                out[k] = coerce_to_default_type(v, CONFIG_DEFAULTS[k])
            # else: 未知 key 忽略
        if not any(k in body for k in CONFIG_DEFAULTS):
            return None, "未提供可识别的配置项"
        return out, ""

    async def api_action_save_config(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/save_config``：保存配置（全量，热生效，无需重载）。

        校验 → 写插件自有 ``config.json``（持久，不被 AstrBot 裁剪）→ 重建
        ``self.cfg``（立即对后续读取生效）→ 开关同步 ``self.config`` + ``save_config``
        （持久到 ``<plugin>_config.json``，self.config 仅含开关故无裁剪噪音）→
        schedule/alerts 变更则重注册 CronJob。
        """
        try:
            from astrbot import logger
            from quart import request

            try:
                body = await request.json
            except Exception:
                body = None
            merged, err = self._validate_save_payload(body)
            if err:
                return self._err(err)
            assert merged is not None
            changed = [k for k in (body or {}) if k in CONFIG_DEFAULTS]
            # 1. 写插件自有配置文件（持久）
            data_dir = getattr(self, "_data_dir", None) or str(self.get_data_dir())
            try:
                save_plugin_config(data_dir, merged)
            except Exception as e:
                return self._err(f"写入配置文件失败：{e}")
            # 2. 重建 self.cfg（热；merged 已含页面编辑值）
            self.cfg = deep_merge(CONFIG_DEFAULTS, merged)
            # 3. 开关持久化到 <plugin>_config.json（self.config 仅含开关 → 无噪音）
            try:
                self.config.save_config(switches_from_config(merged))
            except Exception as e:
                logger.warning("[cost_control] 开关持久化失败（不影响热生效）: %s", e)
            # 4. schedule/alerts 变更 → 重注册 cron（幂等）
            if any(k in changed for k in ("schedule", "alerts")):
                try:
                    await self.register_cron()
                except Exception as e:
                    logger.warning("[cost_control] CronJob 重注册失败: %s", e)
            return self._ok({"saved": changed, "config": self.cfg})
        except Exception as e:
            return self._err(str(e))

    async def api_action_sync_rates(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/sync_rates``：从免费 API 同步最新汇率并持久化到 config。

        调用 :func:`exchange_rates.sync_rates`（``open.er-api.com``），成功则把
        汇率表 + 同步时间写入 ``self.cfg`` 的 ``exchange_rates`` /
        ``exchange_rates_updated_at``，并持久化到插件 config.json；失败返回错误。
        """
        try:
            from astrbot import logger

            from .exchange_rates import sync_rates

            rates, updated_at, err = await sync_rates()
            if err:
                return self._err(f"汇率同步失败：{err}")
            # 写入 self.cfg 并持久化
            cfg = getattr(self, "cfg", None)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg = dict(cfg)
            cfg["exchange_rates"] = rates
            cfg["exchange_rates_updated_at"] = updated_at
            self.cfg = deep_merge(CONFIG_DEFAULTS, cfg)
            # 持久化到 config.json
            data_dir = getattr(self, "_data_dir", None) or str(self.get_data_dir())
            try:
                save_plugin_config(data_dir, self.cfg)
            except Exception as e:
                logger.warning("[cost_control] 汇率持久化失败（热生效）: %s", e)
            return self._ok(
                {
                    "exchange_rates": rates,
                    "exchange_rates_updated_at": updated_at,
                    "count": len(rates),
                }
            )
        except Exception as e:
            return self._err(str(e))
