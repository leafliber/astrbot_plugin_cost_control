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

降级原则：每个 handler 独立 try/except，失败返回 ``{"status": "error", ...}``，
绝不抛出未捕获异常。

阶段 4 实现。
"""

from __future__ import annotations

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
    query_usage: Any
    query_supplements: Any
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
                (f"{prefix}/records", self.api_records, ["GET"], "每请求明细记录"),
                (f"{prefix}/budgets", self.api_budgets, ["GET"], "预算配置与消耗"),
                (f"{prefix}/cache", self.api_cache, ["GET"], "缓存命中率与诊断事件"),
                (f"{prefix}/attribution", self.api_attribution, ["GET"], "归因报表"),
                (f"{prefix}/pricing", self.api_pricing, ["GET"], "模型单价表"),
                (f"{prefix}/config", self.api_config, ["GET"], "当前插件配置"),
                (f"{prefix}/actions/cleanup", self.api_action_cleanup, ["POST"], "手动清理"),
                (f"{prefix}/actions/report", self.api_action_report, ["POST"], "手动推送日报"),
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
        out: dict[str, Any] = {"status": "ok"}
        if data is not None:
            out["data"] = data
        out.update(extra)
        return out

    @staticmethod
    def _err(message: str) -> dict[str, Any]:
        return {"status": "error", "message": message}

    @staticmethod
    def _param(name: str, default: str = "") -> str:
        """从 Quart 全局 ``request.args`` 读 query 参数（延迟导入）。"""
        try:
            from quart import request

            return request.args.get(name, default)
        except Exception:
            return default

    @staticmethod
    def _supplement_to_dict(s: Any) -> dict[str, Any]:
        """把 ``CostSupplement`` 行序列化为 JSON 友好 dict。"""
        created = getattr(s, "created_at", None)
        return {
            "umo": getattr(s, "umo", "") or "",
            "provider_id": getattr(s, "provider_id", "") or "",
            "provider_model": getattr(s, "provider_model", None),
            "conversation_id": getattr(s, "conversation_id", None),
            "token_input_other": int(getattr(s, "token_input_other", 0) or 0),
            "token_input_cached": int(getattr(s, "token_input_cached", 0) or 0),
            "token_output": int(getattr(s, "token_output", 0) or 0),
            "cache_creation": getattr(s, "cache_creation", None),
            "cache_read": getattr(s, "cache_read", None),
            "injection_total": getattr(s, "injection_total", None),
            "attribution": getattr(s, "attribution", None),
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

    async def api_records(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /records``：每请求补充明细（umo / model / token 三类 / cache / 归因）。

        Query：``umo``（可选筛选）、``limit``（默认 100，上限 1000）。
        """
        try:
            umo = self._param("umo") or None
            try:
                limit = max(1, min(1000, int(self._param("limit", "100"))))
            except (TypeError, ValueError):
                limit = 100
            rows = await self.query_supplements(umo=umo, limit=limit)
            return self._ok([self._supplement_to_dict(r) for r in rows])
        except Exception as e:
            return self._err(str(e))

    async def api_budgets(self, **kwargs: Any) -> dict[str, Any]:
        """``GET /budgets``：预算配置 + 各维度当前周期消耗与超限状态（全局视角）。"""
        try:
            from datetime import UTC, datetime

            from .budget import day_window_start, month_window_start

            limits = self.get_budgets()
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)
            day_usage = await self.query_usage(start=d_start)
            month_usage = await self.query_usage(start=m_start)
            day_total = (
                int(day_usage.get("token_input_other", 0) or 0)
                + int(day_usage.get("token_input_cached", 0) or 0)
                + int(day_usage.get("token_output", 0) or 0)
            )
            month_total = (
                int(month_usage.get("token_input_other", 0) or 0)
                + int(month_usage.get("token_input_cached", 0) or 0)
                + int(month_usage.get("token_output", 0) or 0)
            )

            def _dim(key: str, used: int) -> dict[str, Any]:
                limit = int(limits.get(key, 0) or 0)
                return {
                    "limit": limit,
                    "used": used,
                    "ratio": round(used * 100.0 / limit, 1) if limit > 0 else 0.0,
                    "exceeded": limit > 0 and used >= limit,
                }

            return self._ok(
                {
                    "limits": limits,
                    "dimensions": {
                        "global_daily": _dim("global_daily", day_total),
                        "global_monthly": _dim("global_monthly", month_total),
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
            for s in sups:
                rate = hit_rate(
                    getattr(s, "cache_read", None) or getattr(s, "token_input_cached", None),
                    getattr(s, "token_input_other", None),
                    getattr(s, "cache_creation", None),
                )
                if rate >= 0:
                    rates.append(rate)
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
            comps: dict[str, list[int]] = {"system": [], "tools": [], "history": []}
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
        """``GET /pricing``：当前生效的模型单价表（USD / 百万 token）。"""
        try:
            return self._ok(self.get_pricing())
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
