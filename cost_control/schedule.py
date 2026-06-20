"""调度 Mixin。

复用 AstrBot 内置 ``CronJob``（``self.context.cron_manager``）注册定时任务：
每日成本报告推送、历史补充记录清理。

注册策略（已核对 ``astrbot/core/cron/manager.py``）：``add_basic_job`` 的 python
回调存在内存字典 ``_basic_handlers``，重启后丢失会被跳过。故本 Mixin 在
``initialize`` 阶段每次重新注册，并先按 job ``name`` 清理旧记录，保证幂等，
配合 ``persistent=False``（不持久化 handler 引用，由插件生命周期自管理）。

cron 触发时按 ``handler()`` 无参调用（``payload`` 为空）。

阶段 2 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from astrbot import logger

from .budget import day_window_start, month_window_start, resolve_tz
from .config import get_config, get_pricing
from .cost import compute_cost_grouped

REPORT_JOB_NAME = "cost_control_daily_report"
CLEANUP_JOB_NAME = "cost_control_cleanup"
# 历史清理固定在本地 04:00（避开日报时段）。
_CLEANUP_CRON = "0 4 * * *"


def hhmm_to_cron(hhmm: str) -> str:
    """把 ``"HH:MM"`` 转成 5 字段 cron ``"M H * * *"``（纯函数）。

    解析失败回退 ``"0 9 * * *"``（每天 09:00）。
    """
    try:
        parts = str(hhmm).strip().split(":")
        hh = int(parts[0]) % 24
        mm = int(parts[1]) % 60
        return f"{mm} {hh} * * *"
    except (ValueError, IndexError):
        return "0 9 * * *"


class ScheduleMixin:
    """注册 CronJob 日报与清理任务的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    # 兄弟 Mixin 提供。
    query_usage: Any
    query_usage_grouped: Any
    cleanup_old_supplements: Any
    push_to_session: Any

    def _report_cron(self) -> str:
        """从 ``alerts.daily_report_time`` 解析出日报 cron 表达式。"""
        alerts = get_config(getattr(self, "cfg", None), "alerts", {}) or {}
        if isinstance(alerts, dict):
            return hhmm_to_cron(str(alerts.get("daily_report_time", "09:00")))
        return hhmm_to_cron("09:00")

    async def register_cron(self) -> None:
        """幂等注册日报与清理 CronJob。

        先按 name 清理同名旧 job（热重载 / 重启残留），再 ``persistent=False``
        重新注册。任何异常仅记日志，不阻断插件加载。
        """
        try:
            cm = self.context.cron_manager
            existing = await cm.list_jobs(job_type="basic")
            for j in existing or []:
                if j.name in (REPORT_JOB_NAME, CLEANUP_JOB_NAME):
                    try:
                        await cm.delete_job(j.job_id)
                    except Exception as e:
                        logger.warning("[cost_control] 删除旧 CronJob 失败: %s", e)

            tz_key = resolve_tz(self.context).key
            sched = get_config(getattr(self, "cfg", None), "schedule", {}) or {}
            enable_report = bool(
                sched.get("enable_daily_report", False) if isinstance(sched, dict) else False
            )
            # 仅当显式启用时才注册日报 CronJob（默认关闭，避免主动推送打扰）。
            # 历史清理 job 不发消息，始终注册。
            if enable_report:
                await cm.add_basic_job(
                    name=REPORT_JOB_NAME,
                    cron_expression=self._report_cron(),
                    handler=self.daily_report,
                    description="cost_control 每日成本日报",
                    timezone=tz_key,
                    enabled=True,
                    persistent=False,
                )
            await cm.add_basic_job(
                name=CLEANUP_JOB_NAME,
                cron_expression=_CLEANUP_CRON,
                handler=self.cleanup_old,
                description="cost_control 历史数据清理",
                timezone=tz_key,
                enabled=True,
                persistent=False,
            )
            logger.info("[cost_control] CronJob 注册完成 (daily_report=%s)", enable_report)
        except Exception as e:
            logger.warning("[cost_control] CronJob 注册失败: %s", e)

    async def daily_report(self) -> None:
        """CronJob 回调：构建日报并推送给 ``alerts.daily_report_to`` 的每个会话。"""
        try:
            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)

            day_usage = await self.query_usage(start=d_start)
            month_usage = await self.query_usage(start=m_start)

            day_cost = await self._grouped_cost(start=d_start)
            month_cost = await self._grouped_cost(start=m_start)

            text = (
                "📊 成本日报\n"
                f"今日：{day_usage.get('count', 0)} 次调用，成本 ≈ ${day_cost:.4f}\n"
                f"  输入(非缓存) {day_usage.get('token_input_other', 0)} / "
                f"缓存命中 {day_usage.get('token_input_cached', 0)} / "
                f"输出 {day_usage.get('token_output', 0)}\n"
                f"本月：{month_usage.get('count', 0)} 次调用，成本 ≈ ${month_cost:.4f}"
            )

            alerts = get_config(getattr(self, "cfg", None), "alerts", {}) or {}
            targets = alerts.get("daily_report_to", []) if isinstance(alerts, dict) else []
            for umo in targets or []:
                await self.push_to_session(str(umo), text)
        except Exception as e:
            logger.warning("[cost_control] 日报生成失败: %s", e)

    async def cleanup_old(self) -> None:
        """CronJob 回调：按 ``schedule.retain_days`` 清理过期补充记录。"""
        try:
            sched = get_config(getattr(self, "cfg", None), "schedule", {}) or {}
            days = int(sched.get("retain_days", 0) or 0) if isinstance(sched, dict) else 0
            if days <= 0:
                return
            before = datetime.now(UTC) - timedelta(days=days)
            n = await self.cleanup_old_supplements(before)
            if n:
                logger.info("[cost_control] 清理 %d 条过期补充记录", n)
        except Exception as e:
            logger.warning("[cost_control] 历史清理失败: %s", e)

    async def _grouped_cost(self, *, start: datetime) -> float:
        """按 (provider,model) 分组聚合用量并求和成本（无定价的行计 0）。"""
        try:
            rows = await self.query_usage_grouped(by="provider_model", start=start)
            pricing = get_pricing(getattr(self, "cfg", None))
            return compute_cost_grouped(rows, pricing)
        except Exception:
            return 0.0
