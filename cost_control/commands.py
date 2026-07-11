"""命令 Mixin。

注册本插件提供的交互命令：

- ``/cost`` —— 本会话今日 token 用量 + 按模型成本。
- ``/budget`` —— 预算配置与当前超限状态。
- ``/optimize`` —— system prompt 静态分析；带 ``rewrite`` 参数触发 LLM 改写。
- ``/cache`` —— 本会话最近缓存命中率与四类破坏诊断事件。
- ``/report [daily|weekly|monthly]`` —— 用量 / 成本 / 缓存 / 归因综合报表。
- ``/attribution`` —— 最近一次请求的上下文注入归因。

命令 handler 为 ``async`` generator，通过 ``yield event.plain_result(...)``
返回文本（走 ``call_handler`` 洋葱模型，已核对 ``astrbot/core/star/`` 内置插件写法）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter

from .attributor import ESTIMATION_NOTE
from .budget import _DIM_ORDER, day_window_start, resolve_tz
from .config import get_config, get_pricing
from .cost import compute_row_cost
from .exchange_rates import currency_to_symbol, get_main_currency


class CommandsMixin:
    """注册 ``/cost`` ``/budget`` ``/optimize`` ``/cache`` ``/report``
    ``/attribution`` 命令的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    # 兄弟 Mixin 提供。
    query_usage: Any
    query_usage_grouped: Any
    query_supplements: Any
    get_budgets: Any
    get_budgets_cost: Any
    get_budget_overrides: Any
    get_fallback_providers: Any
    default_on_exceeded: Any
    check_budget: Any
    consume_last_injection: Any
    last_system_prompt: Any
    check_hit_rate: Any
    recent_events: Any
    build_report: Any

    def _umo(self, event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", None) or "")

    def _day_start(self) -> datetime:
        refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
        return day_window_start(refresh, datetime.now(UTC), resolve_tz(self.context))

    @filter.command("cost")
    async def cmd_cost(self, event: AstrMessageEvent):
        """``/cost``：查询当前会话今日 token 用量与成本。"""
        try:
            umo = self._umo(event)
            d_start = self._day_start()
            usage = await self.query_usage(umo=umo, start=d_start)
            rows = await self.query_usage_grouped(by="provider_model", umo=umo, start=d_start)
            pricing = get_pricing(getattr(self, "cfg", None))
            sym = currency_to_symbol(get_main_currency(getattr(self, "cfg", None)))
            cost = round(sum(compute_row_cost(r, pricing) for r in rows), 6)
            lines = [
                "💰 今日用量（本会话）",
                f"调用 {usage.get('count', 0)} 次，成本 ≈ {sym}{cost:.4f}",
                f"输入(非缓存) {usage.get('token_input_other', 0)} / "
                f"缓存命中 {usage.get('token_input_cached', 0)} / "
                f"输出 {usage.get('token_output', 0)}",
            ]
            for r in rows[:5]:
                c = round(compute_row_cost(r, pricing), 6)
                name = r.get("provider_model") or r.get("key") or "?"
                lines.append(f"  · {name}：{r.get('count', 0)}次 / {sym}{c:.4f}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("budget")
    async def cmd_budget(self, event: AstrMessageEvent):
        """``/budget``：查询预算配置与当前超限状态。"""
        try:
            umo = self._umo(event)
            sym = currency_to_symbol(get_main_currency(getattr(self, "cfg", None)))
            budgets = self.get_budgets()
            budgets_cost = self.get_budgets_cost()
            overrides = self.get_budget_overrides(getattr(self, "cfg", None))
            result = await self.check_budget(umo, None, event=event)
            lines = ["📋 预算配置"]
            any_cfg = False
            for dim in _DIM_ORDER:
                t = int(budgets.get(dim, 0) or 0)
                c = float(budgets_cost.get(dim, 0) or 0)
                if t > 0 or c > 0:
                    parts = []
                    if t > 0:
                        parts.append(f"token {t}")
                    if c > 0:
                        parts.append(f"花费 {sym}{c:.2f}")
                    lines.append(f"  {dim}: " + " / ".join(parts))
                    any_cfg = True
            if not any_cfg:
                lines.append("  （未配置全局预算）")
            if overrides:
                lines.append(f"🎯 局部阈值：{len(overrides)} 条")
                for ov in overrides:
                    parts = []
                    if ov.get("token_limit", 0) > 0:
                        parts.append(f"token {ov['token_limit']}")
                    if ov.get("cost_limit", 0) > 0:
                        parts.append(f"花费 {sym}{ov['cost_limit']:.2f}")
                    lines.append(
                        f"  · {ov.get('target_type')}:{ov.get('target_value')} "
                        f"({'/'.join(parts) or '不限'}) "
                        f"→ {ov.get('on_exceeded', 'stop')}"
                    )
            if result.get("exceeded"):
                dim = result.get("dim")
                used = result.get("used")
                limit = result.get("limit")
                if result.get("metric") == "cost":
                    used_s = f"{sym}{float(used or 0):.4f}"
                    limit_s = f"{sym}{float(limit or 0):.2f}"
                    lines.append(f"⚠️ 已超出花费预算（{dim}）：{used_s} / {limit_s}")
                else:
                    lines.append(f"⚠️ 已超限（{dim}）：用 {used} / 限 {limit} token")
                lines.append(f"   处理动作：{result.get('on_exceeded', 'stop')}")
            else:
                lines.append("✅ 当前未超限")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("cache")
    async def cmd_cache(self, event: AstrMessageEvent):
        """``/cache``：查看本会话最近缓存命中率与破坏诊断事件。"""
        try:
            umo = self._umo(event)
            sups = await self.query_supplements(umo=umo, limit=10)
            lines = ["🗄 缓存诊断（本会话最近）"]
            rates: list[float] = []
            for s in sups or []:
                rate, _ = self.check_hit_rate(
                    {
                        "cache_read": getattr(s, "cache_read", None),
                        "token_input_cached": getattr(s, "token_input_cached", 0),
                        "token_input_other": getattr(s, "token_input_other", 0),
                        "cache_creation": getattr(s, "cache_creation", None),
                    }
                )
                if rate >= 0:
                    rates.append(rate)
            if rates:
                avg = sum(rates) / len(rates)
                lines.append(f"平均命中率 ≈ {avg:.0f}%（{len(rates)} 条样本）")
            elif sups:
                lines.append("暂无可用命中率样本（缓存数据缺失）")
            else:
                lines.append("暂无缓存数据（需先有 LLM 请求）")
            events = await self.recent_events(umo)
            if events:
                lines.append(f"最近破坏事件（共 {len(events)} 条，显示最新 5）：")
                for ev in events[-5:]:
                    lines.append(f"  · [{ev.get('type')}] {ev.get('detail')}")
            else:
                lines.append("未检测到缓存破坏事件")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("report")
    async def cmd_report(self, event: AstrMessageEvent):
        """``/report``：生成用量 / 成本 / 缓存 / 归因综合报表。

        可选参数 ``daily``（默认）/ ``weekly`` / ``monthly`` 指定时间窗。
        """
        try:
            arg = str(getattr(event, "message_str", "") or "").strip().lower()
            window = arg if arg in ("daily", "weekly", "monthly") else "daily"
            sym = currency_to_symbol(get_main_currency(getattr(self, "cfg", None)))
            report = await self.build_report(window=window)
            usage = report.get("usage", {}) or {}
            lines = [
                f"📊 成本报表（{window}）",
                f"调用 {usage.get('count', 0)} 次，成本 ≈ {sym}{report.get('cost', 0):.4f}",
                f"输入(非缓存) {usage.get('token_input_other', 0)} / "
                f"缓存命中 {usage.get('token_input_cached', 0)} / "
                f"输出 {usage.get('token_output', 0)}",
                f"平均缓存命中率 ≈ {report.get('cache_hit_rate', 0)}%"
                f"（{report.get('cache_samples', 0)} 样本）",
                f"平均上下文注入 ≈ {report.get('avg_injection', 0)} token"
                f"（{report.get('injection_samples', 0)} 样本）",
            ]
            by_model = report.get("cost_by_model", []) or []
            if by_model:
                lines.append("按模型成本（Top 5）：")
                for m in by_model[:5]:
                    lines.append(
                        f"  · {m.get('model') or '?'}：{m.get('count', 0)}次 / "
                        f"{sym}{m.get('cost', 0):.4f}"
                    )
            top = report.get("top_sessions", []) or []
            if top:
                lines.append("Top 会话（按 token）：")
                for s in top[:3]:
                    lines.append(f"  · {s.get('umo', '?')}：{s.get('tokens', 0)} token")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("attribution")
    async def cmd_attribution(self, event: AstrMessageEvent):
        """``/attribution``：查看最近一次请求的上下文注入归因。"""
        try:
            umo = self._umo(event)
            inj = self.consume_last_injection(umo)
            if not inj:
                yield event.plain_result("暂无归因数据（需先发起一次对话，且归因功能开启）。")
                return
            final = inj.get("final", {}) or {}
            injected = inj.get("injected", {}) or {}
            lines = [
                "🔎 最近一次请求的上下文归因（token 估算）",
                f"system {final.get('system', 0)} / tools {final.get('tools', 0)} / "
                f"history {final.get('history', 0)} / user {final.get('user', 0)} / "
                f"extra {final.get('extra', 0)}",
                f"  → 总计 {final.get('total', 0)}",
                f"本轮插件累计注入 {inj.get('injected_total', 0)}"
                f"（system +{injected.get('system', 0)} / "
                f"tools +{injected.get('tools', 0)} / "
                f"history +{injected.get('history', 0)} / "
                f"extra +{injected.get('extra', 0)}）",
                "",
                ESTIMATION_NOTE,
            ]
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")
