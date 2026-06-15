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

from .budget import resolve_tz
from .config import get_config

PLUGIN_NAME = "astrbot_plugin_cost_control"


class WebApiMixin:
    """注册 REST Web API 路由的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any
    # 兄弟 Mixin 提供。
    build_report: Any
    get_budgets: Any
    get_over_limit_policy: Any
    query_usage: Any
    query_usage_grouped: Any
    query_supplements: Any
    query_usage_timeseries: Any
    cleanup_old_supplements: Any
    get_pricing: Any
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
        s: Any, pricing: dict[str, dict[str, float]] | None = None
    ) -> dict[str, Any]:
        """把 ``CostSupplement`` 行序列化为 JSON 友好 dict。

        ``pricing`` 非空时按模型单价算出本条成本 ``cost``（未定价为 0.0，与
        全局口径一致）。
        """
        from .cost import compute_cost_value

        created = getattr(s, "created_at", None)
        token_input_other = int(getattr(s, "token_input_other", 0) or 0)
        token_input_cached = int(getattr(s, "token_input_cached", 0) or 0)
        token_output = int(getattr(s, "token_output", 0) or 0)
        cache_creation = getattr(s, "cache_creation", None)
        cost = 0.0
        if pricing is not None:
            cost = round(
                compute_cost_value(
                    {
                        "token_input_other": token_input_other,
                        "token_input_cached": token_input_cached,
                        "token_output": token_output,
                        "cache_creation": cache_creation,
                    },
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
            from .cost import compute_cost_value

            window = self._param("window", "daily") or "daily"
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
            cur_start, cur_end, prev_start, prev_end = compare_windows(window, now, tz, refresh)
            pricing = self.get_pricing()

            async def _stats(start: datetime, end: datetime) -> dict[str, Any]:
                usage = await self.query_usage(start=start, end=end)
                rows = await self.query_usage_grouped(by="model", start=start, end=end)
                cost = round(
                    sum(compute_cost_value(r, r.get("key") or None, pricing) for r in rows),
                    6,
                )
                return {
                    "cost": cost,
                    "count": int(usage.get("count", 0) or 0),
                    "tokens": total_tokens(usage),
                }

            cur = await _stats(cur_start, cur_end)
            prev = await _stats(prev_start, prev_end)

            def _pct(c: float, p: float) -> float | None:
                return round((c - p) * 100.0 / p, 1) if p > 0 else None

            label = "昨日" if window == "daily" else "上周" if window == "weekly" else "上月"
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
            return self._ok(
                {
                    "series": series,
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
            return self._ok([self._supplement_to_dict(r, pricing) for r in rows])
        except Exception as e:
            return self._err(str(e))

    async def api_records_aggregate(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /records/aggregate``：在筛选条件上按模型 / 会话二级聚合（基于 ProviderStat）。

        Query：``by``（model|provider，默认 model）、``umo`` / ``provider`` / ``model``
        （可选）、``start`` / ``end``（ISO）。返回每组的 token 三类、条数、成本、占比。
        """
        try:
            from .cost import compute_cost_value

            by = self._param("by", "model") or "model"
            if by not in ("model", "provider"):
                by = "model"
            start = self._parse_iso(self._param("start"))
            end = self._parse_iso(self._param("end"))
            umo = self._param("umo") or None
            provider = self._param("provider") or None
            model = self._param("model") or None
            rows = await self.query_usage_grouped(
                by=by,
                umo=umo,
                provider=provider,
                start=start,
                end=end,
            )
            # query_usage_grouped 无 model 筛选参数，应用层补
            if model and by == "model":
                rows = [r for r in rows if r.get("key") == model]
            pricing = self.get_pricing()
            total_tokens = 0
            out: list[dict[str, Any]] = []
            for r in rows:
                tokens = (
                    int(r.get("token_input_other", 0) or 0)
                    + int(r.get("token_input_cached", 0) or 0)
                    + int(r.get("token_output", 0) or 0)
                )
                cost = round(compute_cost_value(r, r.get("key") or None, pricing), 6)
                total_tokens += tokens
                out.append(
                    {
                        "key": r.get("key") or "",
                        "count": int(r.get("count", 0) or 0),
                        "tokens": tokens,
                        "token_input_other": int(r.get("token_input_other", 0) or 0),
                        "token_input_cached": int(r.get("token_input_cached", 0) or 0),
                        "token_output": int(r.get("token_output", 0) or 0),
                        "cost": cost,
                    }
                )
            out.sort(key=lambda x: x["cost"], reverse=True)
            for o in out:
                o["pct"] = round(o["tokens"] * 100.0 / total_tokens, 1) if total_tokens else 0.0
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
        """``GET /budgets``：预算配置 + 各维度当前周期消耗（全局视角）。

        全局维度（global_daily/global_monthly）给精确消耗；局部维度
        （per_session/per_user/per_model）给「本周期消耗最多的会话/模型」代表值
        （这些维度在运行时按当前请求的 umo/model 实时判定拦截，无单一全局消耗）。
        """
        try:
            from datetime import UTC, datetime

            from .budget import day_window_start, month_window_start, total_tokens

            limits = self.get_budgets()
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)
            day_usage = await self.query_usage(start=d_start)
            month_usage = await self.query_usage(start=m_start)
            day_total = total_tokens(day_usage)
            month_total = total_tokens(month_usage)

            def _dim(key: str, used: int, top_key: str = "", note: str = "") -> dict[str, Any]:
                limit = int(limits.get(key, 0) or 0)
                return {
                    "limit": limit,
                    "used": used,
                    "ratio": round(used * 100.0 / limit, 1) if limit > 0 else 0.0,
                    "exceeded": limit > 0 and used >= limit,
                    "top_key": top_key,
                    "note": note,
                }

            # per_session / per_user：本日消耗最多的会话（per_user 阶段2 退化为 umo 维度）
            top_session = await self.query_usage_grouped(by="umo", start=d_start)
            top_session.sort(key=lambda r: total_tokens(r), reverse=True)
            ses_used = total_tokens(top_session[0]) if top_session else 0
            ses_key = str((top_session[0] or {}).get("key", "")) if top_session else ""
            # per_model：本日消耗最多的模型
            top_model = await self.query_usage_grouped(by="model", start=d_start)
            top_model.sort(key=lambda r: total_tokens(r), reverse=True)
            mod_used = total_tokens(top_model[0]) if top_model else 0
            mod_key = str((top_model[0] or {}).get("key", "")) if top_model else ""

            return self._ok(
                {
                    "limits": limits,
                    "policy": self.get_over_limit_policy(),
                    "dimensions": {
                        "global_daily": _dim("global_daily", day_total),
                        "global_monthly": _dim("global_monthly", month_total),
                        "per_session_daily": _dim(
                            "per_session_daily", ses_used, ses_key, "今日消耗最多的会话"
                        ),
                        "per_user_daily": _dim(
                            "per_user_daily",
                            ses_used,
                            ses_key,
                            "退化为会话维度（ProviderStat 无独立 user_id）",
                        ),
                        "per_model_daily": _dim(
                            "per_model_daily", mod_used, mod_key, "今日消耗最多的模型"
                        ),
                    },
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_cache(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /cache``：缓存命中率 + 所有会话最近的破坏诊断事件。"""
        try:
            try:
                limit = max(1, min(500, int(self._param("limit", "100"))))
            except (TypeError, ValueError):
                limit = 100
            sups = await self.query_supplements(limit=limit)
            from .cache_diag import hit_rate

            rates: list[float] = []
            total_input_other = 0
            total_input_cached = 0
            for s in sups:
                rate = hit_rate(
                    getattr(s, "cache_read", None) or getattr(s, "token_input_cached", None),
                    getattr(s, "token_input_other", None),
                    getattr(s, "cache_creation", None),
                )
                if rate >= 0:
                    rates.append(rate)
                total_input_other += int(getattr(s, "token_input_other", 0) or 0)
                total_input_cached += int(getattr(s, "token_input_cached", 0) or 0)
            avg = round(sum(rates) / len(rates), 1) if rates else 0.0

            events: list[dict[str, Any]] = []
            bucket = getattr(self, "_cache_events", None)
            if isinstance(bucket, dict):
                for umo_val, evs in bucket.items():
                    for ev in evs or []:
                        e = dict(ev)
                        e.setdefault("umo", umo_val)
                        events.append(e)
            events = events[-50:]
            return self._ok(
                {
                    "cache_hit_rate": avg,
                    "samples": len(rates),
                    "total_input_other": total_input_other,
                    "total_input_cached": total_input_cached,
                    "events": events,
                }
            )
        except Exception as e:
            return self._err(str(e))

    async def api_attribution(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /attribution``：最近请求的归因（各组件占比 + 注入量）。"""
        try:
            try:
                limit = max(1, min(500, int(self._param("limit", "50"))))
            except (TypeError, ValueError):
                limit = 50
            sups = await self.query_supplements(limit=limit)
            items: list[dict[str, Any]] = []
            comps: dict[str, list[int]] = {
                "system": [],
                "tools": [],
                "history": [],
                "user": [],
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
            return self._ok({"recent": items, "avg_components": avg})
        except Exception as e:
            return self._err(str(e))

    async def api_pricing(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /pricing``：模型单价表 + 有用量但未定价的模型告警。

        返回 ``{pricing: {model: prices}, unpriced: [{model, tokens, count}]}``。
        ``unpriced`` 取全量历史（``query_usage_grouped(by="model")``）中
        :func:`cost.match_pricing` 匹配不到单价的模型——这些模型的成本被计为 0，
        会使整体成本统计偏低，需提示用户补单价。附 token 量表明失真影响范围。
        """
        try:
            from .cost import match_pricing

            pricing = self.get_pricing()
            unpriced: list[dict[str, Any]] = []
            try:
                rows = await self.query_usage_grouped(by="model")
                for r in rows:
                    model = r.get("key") or ""
                    if model and match_pricing(model, pricing) is None:
                        tokens = (
                            int(r.get("token_input_other", 0) or 0)
                            + int(r.get("token_input_cached", 0) or 0)
                            + int(r.get("token_output", 0) or 0)
                        )
                        unpriced.append(
                            {
                                "model": model,
                                "tokens": tokens,
                                "count": int(r.get("count", 0) or 0),
                            }
                        )
                unpriced.sort(key=lambda x: x["tokens"], reverse=True)
            except Exception:
                pass  # 用量查询失败不阻断定价表展示
            return self._ok({"pricing": pricing, "unpriced": unpriced})
        except Exception as e:
            return self._err(str(e))

    async def api_config(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /config``：当前插件配置（不含密钥，仅预算 / 单价 / 诊断等设置）。"""
        try:
            cfg = getattr(self, "config", None) or {}
            return self._ok(dict(cfg) if isinstance(cfg, dict) else {})
        except Exception as e:
            return self._err(str(e))

    async def api_action_cleanup(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/cleanup``：手动清理过期补充记录，返回删除条数。

        清理窗口取 ``schedule.retain_days``（<=0 表示不清理，返回 0）。
        """
        try:
            from datetime import UTC, datetime, timedelta

            sched = get_config(getattr(self, "config", None), "schedule", {}) or {}
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

    # 仅允许 WebUI 修改这两个顶层 key（白名单），防前端乱改 pricing/alerts 等。
    _SAVE_BUDGET_KEYS = (
        "per_session_daily",
        "per_user_daily",
        "per_model_daily",
        "global_daily",
        "global_monthly",
    )
    _SAVE_POLICY_KEYS = (
        "action",
        "fallback_provider_id",
        "fallback_token_limit",
        "block_wake_words_after_limit",
    )

    def _validate_save_payload(self, body: Any) -> tuple[dict[str, Any] | None, str]:
        """校验 save_config 请求体，返回合并后的配置或错误信息。

        ``self.config.save_config`` 是浅合并（整体替换顶层 key），故 budgets 必须含
        5 个 key、policy 必须含 4 个 key——缺失者从现有配置补齐，防覆盖丢字段。
        返回 ``(merged_or_None, error_msg)``；成功时 ``error_msg`` 为空串。
        """
        if not isinstance(body, dict):
            return None, "请求体必须是 JSON 对象"
        cfg = getattr(self, "config", None) or {}
        merged: dict[str, Any] = {}

        if "budgets" in body:
            sub = body.get("budgets")
            if not isinstance(sub, dict):
                return None, "budgets 必须是对象"
            cur_budgets = cfg.get("budgets", {}) if isinstance(cfg, dict) else {}
            if not isinstance(cur_budgets, dict):
                cur_budgets = {}
            out_b: dict[str, int] = {}
            for k in self._SAVE_BUDGET_KEYS:
                if k in sub:
                    try:
                        out_b[k] = max(0, int(sub[k]))
                    except (TypeError, ValueError):
                        return None, f"budgets.{k} 必须是整数"
                else:
                    try:
                        out_b[k] = max(0, int(cur_budgets.get(k, 0) or 0))
                    except (TypeError, ValueError):
                        out_b[k] = 0
            merged["budgets"] = out_b

        if "over_limit_policy" in body:
            sub = body.get("over_limit_policy")
            if not isinstance(sub, dict):
                return None, "over_limit_policy 必须是对象"
            cur_policy = cfg.get("over_limit_policy", {}) if isinstance(cfg, dict) else {}
            if not isinstance(cur_policy, dict):
                cur_policy = {}
            out_p: dict[str, Any] = {}
            for k in self._SAVE_POLICY_KEYS:
                if k in sub:
                    v = sub[k]
                    if k == "action":
                        v = str(v) if v is not None else "stop_llm"
                        if v not in ("stop_llm", "fallback_provider"):
                            return None, "action 必须是 stop_llm 或 fallback_provider"
                        out_p[k] = v
                    elif k == "fallback_provider_id":
                        out_p[k] = str(v or "")
                    elif k == "fallback_token_limit":
                        try:
                            out_p[k] = max(0, int(v))
                        except (TypeError, ValueError):
                            return None, "fallback_token_limit 必须是整数"
                    elif k == "block_wake_words_after_limit":
                        out_p[k] = bool(v)
                else:
                    out_p[k] = cur_policy.get(k)
            merged["over_limit_policy"] = out_p

        if not merged:
            return None, "未提供可更新的配置（仅支持 budgets / over_limit_policy）"
        return merged, ""

    async def api_action_save_config(self, **kwargs: Any) -> dict[str, Any]:
        """``POST /actions/save_config``：保存预算 / 策略配置（热生效，无需重载）。

        只接受白名单顶层 key（``budgets`` / ``over_limit_policy``），类型校验 + 补齐
        缺失 key 后调用 ``self.config.save_config(merged)``——同步更新内存 + 落盘
        ``data/config/<plugin>_config.json``，立即对后续 ``check_budget`` 生效。
        """
        try:
            from quart import request

            try:
                body = await request.json
            except Exception:
                body = None
            merged, err = self._validate_save_payload(body)
            if err:
                return self._err(err)
            assert merged is not None
            self.config.save_config(merged)
            return self._ok({"saved": list(merged.keys()), "config": merged})
        except Exception as e:
            return self._err(str(e))
