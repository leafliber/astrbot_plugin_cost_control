"""命令 Mixin。

注册本插件提供的交互命令。阶段 2 实现 ``/cost``（本会话用量 + 成本）与
``/budget``（预算配置 + 超限状态）；``/optimize`` ``/cache`` ``/report``
``/attribution`` 留待后续阶段，handler 返回占位提示。

命令 handler 为 ``async`` generator，通过 ``yield event.plain_result(...)``
返回文本（已核对 ``astrbot/core/star/`` 内置插件写法）。

阶段 2 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter

from .budget import _DIM_ORDER, day_window_start, resolve_tz
from .config import get_config, get_pricing
from .cost import compute_cost_value


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
    check_budget: Any
    consume_last_injection: Any
    last_system_prompt: Any
    check_hit_rate: Any
    recent_events: Any
    analyze_prompt: Any
    rewrite_prompt: Any

    def _umo(self, event: AstrMessageEvent) -> str:
        return str(getattr(event, "unified_msg_origin", None) or "")

    def _day_start(self) -> datetime:
        refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
        return day_window_start(refresh, datetime.now(UTC), resolve_tz(self.context))

    @filter.command("cost")
    async def cmd_cost(self, event: AstrMessageEvent):
        """``/cost``：查询当前会话今日 token 用量与成本。"""
        try:
            umo = self._umo(event)
            d_start = self._day_start()
            usage = await self.query_usage(umo=umo, start=d_start)
            rows = await self.query_usage_grouped(by="model", umo=umo, start=d_start)
            pricing = get_pricing(getattr(self, "config", None))
            cost = sum(compute_cost_value(r, r.get("key") or None, pricing) for r in rows)
            lines = [
                "💰 今日用量（本会话）",
                f"调用 {usage.get('count', 0)} 次，成本 ≈ ${cost:.4f}",
                f"输入(非缓存) {usage.get('token_input_other', 0)} / "
                f"缓存命中 {usage.get('token_input_cached', 0)} / "
                f"输出 {usage.get('token_output', 0)}",
            ]
            for r in rows[:5]:
                c = compute_cost_value(r, r.get("key") or None, pricing)
                lines.append(f"  · {r.get('key') or '?'}：{r.get('count', 0)}次 / ${c:.4f}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("budget")
    async def cmd_budget(self, event: AstrMessageEvent):
        """``/budget``：查询预算配置与当前超限状态。"""
        try:
            umo = self._umo(event)
            budgets = self.get_budgets()
            result = await self.check_budget(umo, None)
            lines = ["📋 预算配置（token）"]
            any_cfg = False
            for dim in _DIM_ORDER:
                limit = int(budgets.get(dim, 0) or 0)
                if limit > 0:
                    lines.append(f"  {dim}: {limit}")
                    any_cfg = True
            if not any_cfg:
                lines.append("  （未配置任何预算）")
            if result.get("exceeded"):
                lines.append(
                    f"⚠️ 已超限：{result.get('dim')}（用 {result.get('used')} / "
                    f"限 {result.get('limit')}）"
                )
            else:
                lines.append("✅ 当前未超限")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")

    @filter.command("optimize")
    async def cmd_optimize(self, event: AstrMessageEvent):
        """``/optimize``：静态分析 system prompt；带 ``rewrite`` 参数触发 LLM 改写。

        无参数时分析最近一次请求的 system prompt；``/optimize rewrite`` 则额外
        经配置的 provider 改写并返回精简版。
        """
        try:
            umo = self._umo(event)
            arg = str(getattr(event, "message_str", "") or "").strip()
            do_rewrite = arg.lower().startswith("rewrite")
            sp = self.last_system_prompt(umo)
            if not sp:
                yield event.plain_result("暂无 system prompt（请先发起一次对话，且归因功能开启）。")
                return
            report = self.analyze_prompt(sp)
            lines = [
                "✏️ system prompt 静态分析",
                f"长度 {report.get('length', 0)} 字符 / ≈ {report.get('tokens_est', 0)} token",
                f"冗余 {report.get('redundancy_score', 0)}% / 可缓存性 "
                f"{report.get('cacheability_score', 0)}/100",
                "建议：",
            ]
            for s in report.get("suggestions", []):
                lines.append(f"  · {s}")
            if do_rewrite:
                try:
                    rewritten = await self.rewrite_prompt(sp, umo)
                    lines.append("\n--- 改写后（前 500 字）---")
                    lines.append(rewritten[:500])
                except Exception as e:
                    lines.append(f"\n改写失败：{e}")
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
            events = self.recent_events(umo)
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
        """``/report``：生成用量 / 成本报表。"""
        # TODO 阶段4：调用 AnalyticsMixin
        yield event.plain_result("该命令将在后续阶段实现。")

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
                f"history {final.get('history', 0)} / user {final.get('user', 0)}",
                f"  → 总计 {final.get('total', 0)}",
                f"本轮插件累计注入 {inj.get('injected_total', 0)}"
                f"（system +{injected.get('system', 0)} / "
                f"tools +{injected.get('tools', 0)} / "
                f"history +{injected.get('history', 0)}）",
            ]
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")
