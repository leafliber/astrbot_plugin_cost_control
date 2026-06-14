"""命令 Mixin。

注册本插件提供的所有交互命令，每个命令对应一个 ``@filter.command`` 装饰的
handler。具体逻辑在各阶段逐步实现。

阶段 0：仅注册命令骨架，handler 内部为 TODO。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter


class CommandsMixin:
    """注册 ``/cost`` ``/budget`` ``/optimize`` ``/cache`` ``/report``
    ``/attribution`` 命令的 Mixin。"""

    @filter.command("cost")
    async def cmd_cost(self, event: AstrMessageEvent) -> None:
        """``/cost``：查询当前会话 token 用量与成本。"""
        # TODO 阶段1：调用 UsageQueryMixin + CostMixin 输出
        ...

    @filter.command("budget")
    async def cmd_budget(self, event: AstrMessageEvent) -> None:
        """``/budget``：查询或设置预算阈值。"""
        # TODO 阶段2：读取 / 修改 budgets 配置
        ...

    @filter.command("optimize")
    async def cmd_optimize(self, event: AstrMessageEvent) -> None:
        """``/optimize``：分析并优化 system prompt。"""
        # TODO 阶段3：调用 PromptOptimizerMixin
        ...

    @filter.command("cache")
    async def cmd_cache(self, event: AstrMessageEvent) -> None:
        """``/cache``：查看缓存命中率与破坏诊断。"""
        # TODO 阶段3：调用 CacheDiagMixin
        ...

    @filter.command("report")
    async def cmd_report(self, event: AstrMessageEvent) -> None:
        """``/report``：生成用量 / 成本报表。"""
        # TODO 阶段4：调用 AnalyticsMixin
        ...

    @filter.command("attribution")
    async def cmd_attribution(self, event: AstrMessageEvent) -> None:
        """``/attribution``：查看 token 注入归因。"""
        # TODO 阶段3：调用 AttributorMixin
        ...
