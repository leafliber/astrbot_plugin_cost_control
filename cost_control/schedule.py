"""调度 Mixin。

复用 AstrBot 内置 ``CronJob`` 表注册定时任务：每日报告推送、历史补充
记录清理（按 ``schedule.retain_days`` 配置）。

阶段 2 实现。
"""

from __future__ import annotations


class ScheduleMixin:
    """注册 CronJob 日报与清理任务的 Mixin。"""

    async def register_cron(self) -> None:
        """向 ``CronJob`` 表注册本插件的定时任务。

        若已注册则跳过，避免重复。任务包括：
        - 每日报告（按 ``alerts.daily_report_time``）。
        - 历史清理（按 ``schedule.retain_days``）。
        """
        raise NotImplementedError("阶段2实现")

    async def daily_report(self) -> None:
        """CronJob 触发的日报回调：构建报表并推送给订阅用户 / 群。"""
        raise NotImplementedError("阶段2实现")
