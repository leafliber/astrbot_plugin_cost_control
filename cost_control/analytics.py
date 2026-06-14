"""报表 Mixin。

交叉聚合用量 / 成本 / 缓存命中 / 归因，生成日报 / 周报 / 月报，
供 ``/report`` 命令、Plugin Page、CronJob 推送复用。

阶段 4 实现。
"""

from __future__ import annotations

from typing import Any


class AnalyticsMixin:
    """生成交叉报表的 Mixin。"""

    async def build_report(self, *, window: str = "daily") -> dict[str, Any]:
        """构建指定时间窗的用量 / 成本 / 缓存 / 归因综合报表。

        Args:
            window: 时间窗，可选 ``"daily"`` / ``"weekly"`` / ``"monthly"``。

        Returns:
            报表 dict，含 ``usage`` / ``cost`` / ``cache_hit_rate`` /
            ``attribution`` / ``top_sessions`` 等字段。
        """
        raise NotImplementedError("阶段4实现")
