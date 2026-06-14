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
    get_budgets: Any
    check_budget: Any

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
        """``/optimize``：分析并优化 system prompt。"""
        # TODO 阶段3：调用 PromptOptimizerMixin
        yield event.plain_result("该命令将在后续阶段实现。")

    @filter.command("cache")
    async def cmd_cache(self, event: AstrMessageEvent):
        """``/cache``：查看缓存命中率与破坏诊断。"""
        # TODO 阶段3：调用 CacheDiagMixin
        yield event.plain_result("该命令将在后续阶段实现。")

    @filter.command("report")
    async def cmd_report(self, event: AstrMessageEvent):
        """``/report``：生成用量 / 成本报表。"""
        # TODO 阶段4：调用 AnalyticsMixin
        yield event.plain_result("该命令将在后续阶段实现。")

    @filter.command("attribution")
    async def cmd_attribution(self, event: AstrMessageEvent):
        """``/attribution``：查看 token 注入归因。"""
        # TODO 阶段3：调用 AttributorMixin
        yield event.plain_result("该命令将在后续阶段实现。")
