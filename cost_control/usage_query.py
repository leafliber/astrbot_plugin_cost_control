"""用量查询 Mixin。

封装对原生 ``ProviderStat`` 表的四维（会话 / 用户 / 模型 / 全局）聚合查询，
供预算检查、报表、命令复用。

可测性设计：聚合逻辑抽成模块级纯函数 ``aggregate_rows``（不依赖 astrbot /
sqlmodel，可单测）；DB 查询在 ``UsageQueryMixin`` 方法内延迟 import astrbot，
使本模块顶层零第三方依赖，本地可直接 import 测试。

阶段 1 实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def aggregate_rows(rows: list[Any]) -> dict[str, Any]:
    """聚合 ``ProviderStat`` 行（或类似对象）的 token 三类与条数。

    纯函数，仅依赖每个行的 ``token_input_other`` / ``token_input_cached`` /
    ``token_output`` 属性，可脱离 DB 单测。

    Args:
        rows: ``ProviderStat`` 对象列表（或任何含上述属性的 duck-typed 对象）。

    Returns:
        含 ``token_input_other`` / ``token_input_cached`` / ``token_output`` /
        ``count`` 的聚合 dict。
    """
    s_other = 0
    s_cached = 0
    s_output = 0
    for row in rows:
        s_other += int(getattr(row, "token_input_other", 0) or 0)
        s_cached += int(getattr(row, "token_input_cached", 0) or 0)
        s_output += int(getattr(row, "token_output", 0) or 0)
    return {
        "token_input_other": s_other,
        "token_input_cached": s_cached,
        "token_output": s_output,
        "count": len(rows),
    }


class UsageQueryMixin:
    """查询 ``ProviderStat`` 聚合用量的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any

    async def query_usage(
        self,
        *,
        umo: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        """按条件聚合查询 token 用量（SQL SUM/COUNT，失败返回零值）。

        时间窗口基于 ``ProviderStat.created_at``（UTC datetime）；注意
        ``start_time`` / ``end_time`` 是 float（耗时/时间戳），不适合做窗口筛选。

        Args:
            umo: 会话标识（unified message origin），None 表示不限。
            provider: Provider ID，None 表示不限。
            model: 模型名，None 表示不限。
            start: 窗口起始时间（含）。
            end: 窗口结束时间（含）。

        Returns:
            包含 ``token_input_other`` / ``token_input_cached`` / ``token_output``
            / ``count`` 的聚合 dict。
        """
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import func, select

        zero: dict[str, Any] = {
            "token_input_other": 0,
            "token_input_cached": 0,
            "token_output": 0,
            "count": 0,
        }
        try:
            db = self.context.get_db()
            stmt = select(
                func.sum(ProviderStat.token_input_other),
                func.sum(ProviderStat.token_input_cached),
                func.sum(ProviderStat.token_output),
                func.count(),
            )
            if umo:
                stmt = stmt.where(ProviderStat.umo == umo)
            if provider:
                stmt = stmt.where(ProviderStat.provider_id == provider)
            if model:
                stmt = stmt.where(ProviderStat.provider_model == model)
            if start:
                stmt = stmt.where(ProviderStat.created_at >= start)
            if end:
                stmt = stmt.where(ProviderStat.created_at <= end)
            async with db.get_db() as session:
                result = await session.execute(stmt)
                row = result.one()
            return {
                "token_input_other": int(row[0] or 0),
                "token_input_cached": int(row[1] or 0),
                "token_output": int(row[2] or 0),
                "count": int(row[3] or 0),
            }
        except Exception:
            return zero

    async def query_records(
        self,
        *,
        umo: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[Any]:
        """按条件查询 ``ProviderStat`` 明细行（最新优先，失败返回空列表）。"""
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import select

        try:
            db = self.context.get_db()
            stmt = select(ProviderStat)
            if umo:
                stmt = stmt.where(ProviderStat.umo == umo)
            if provider:
                stmt = stmt.where(ProviderStat.provider_id == provider)
            if model:
                stmt = stmt.where(ProviderStat.provider_model == model)
            if start:
                stmt = stmt.where(ProviderStat.created_at >= start)
            if end:
                stmt = stmt.where(ProviderStat.created_at <= end)
            stmt = stmt.order_by(ProviderStat.created_at.desc()).limit(limit)
            async with db.get_db() as session:
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception:
            return []

    async def query_usage_grouped(
        self,
        *,
        by: str = "model",
        umo: str | None = None,
        provider: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """按 ``by`` 分组聚合 token 用量（供成本核算按模型 / provider 拆分）。

        Args:
            by: ``"model"``（按 ``provider_model``）或 ``"provider"``（按 ``provider_id``）。
            umo: 会话标识，None 表示不限。
            provider: Provider ID，None 表示不限。
            start: 窗口起始（含）。
            end: 窗口结束（含）。

        Returns:
            ``[{"key": str, "token_input_other": int, "token_input_cached": int,
            "token_output": int, "count": int}, ...]``，``key`` 为分组值
            （模型名或 provider id）。失败返回空列表。
        """
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import func, select

        try:
            db = self.context.get_db()
            group_col = (
                ProviderStat.provider_id if by == "provider" else ProviderStat.provider_model
            )
            stmt = select(  # type: ignore[call-overload]
                group_col,
                func.sum(ProviderStat.token_input_other),
                func.sum(ProviderStat.token_input_cached),
                func.sum(ProviderStat.token_output),
                func.count(),
            ).group_by(group_col)
            if umo:
                stmt = stmt.where(ProviderStat.umo == umo)
            if provider:
                stmt = stmt.where(ProviderStat.provider_id == provider)
            if start:
                stmt = stmt.where(ProviderStat.created_at >= start)
            if end:
                stmt = stmt.where(ProviderStat.created_at <= end)
            async with db.get_db() as session:
                result = await session.execute(stmt)
                rows = result.all()
            out: list[dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "key": str(r[0] or ""),
                        "token_input_other": int(r[1] or 0),
                        "token_input_cached": int(r[2] or 0),
                        "token_output": int(r[3] or 0),
                        "count": int(r[4] or 0),
                    }
                )
            return out
        except Exception:
            return []
