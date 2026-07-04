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

# 时序分桶支持的列白名单（由 ``query_usage_timeseries`` 校验后传入 ``bucketize_rows``）。
TIMESERIES_BUCKETS = ("day", "hour")


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


def _bucket_key(created: Any, bucket: str) -> str | None:
    """从行的 ``created_at`` 派生桶 key（纯函数）。

    - ``day``：``"YYYY-MM-DD"``。
    - ``hour``：``"YYYY-MM-DD HH:00"``。

    输入为 None 或解析失败返回 None（该行被丢弃）。
    """
    if created is None:
        return None
    try:
        dt = created
        if isinstance(dt, datetime):
            iso = dt.isoformat(sep=" ")
        else:
            iso = str(dt)
        # datetime.isoformat / 标准字符串均以 ``YYYY-MM-DD`` 开头
        day = iso[:10]
        if bucket == "hour":
            hour = iso[11:13] if len(iso) >= 13 else "00"
            return f"{day} {hour}:00"
        return day
    except Exception:
        return None


def bucketize_rows(
    rows: list[Any],
    bucket: str = "day",
) -> list[dict[str, Any]]:
    """应用层分桶聚合（纯函数，便于脱离 DB 单测）。

    Args:
        rows: ``ProviderStat`` 对象列表（duck-typed，含 ``created_at`` 与 token 三类）。
        bucket: ``"day"`` 或 ``"hour"``。

    Returns:
        ``[{bucket, token_input_other, token_input_cached, token_output, count}, ...]``，
        按 bucket 升序。``created_at`` 缺失的行被跳过。
    """
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        key = _bucket_key(getattr(row, "created_at", None), bucket)
        if key is None:
            continue
        b = buckets.setdefault(
            key,
            {"token_input_other": 0, "token_input_cached": 0, "token_output": 0, "count": 0},
        )
        b["token_input_other"] += int(getattr(row, "token_input_other", 0) or 0)
        b["token_input_cached"] += int(getattr(row, "token_input_cached", 0) or 0)
        b["token_output"] += int(getattr(row, "token_output", 0) or 0)
        b["count"] += 1
    return [{"bucket": k, **v} for k, v in sorted(buckets.items())]


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
        model: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """按 ``by`` 分组聚合 token 用量（供成本核算按模型 / provider / 会话拆分）。

        Args:
            by: ``"model"``（按 ``provider_model``）、``"provider"``（按
            ``provider_id``）或 ``"umo"``（按会话 ``umo``）。
            umo: 会话标识，None 表示不限。
            provider: Provider ID，None 表示不限。
            model: 模型名（``provider_model``），None 表示不限。
            start: 窗口起始（含）。
            end: 窗口结束（含）。

        Returns:
            ``[{"key": str, "token_input_other": int, "token_input_cached": int,
            "token_output": int, "count": int}, ...]``，``key`` 为分组值
            （模型名 / provider id / umo）。失败返回空列表。
        """
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import func, select

        try:
            db = self.context.get_db()
            composite = by == "provider_model"
            if by == "provider":
                group_col = ProviderStat.provider_id
            elif by == "umo":
                group_col = ProviderStat.umo
            else:
                group_col = ProviderStat.provider_model
            if composite:
                stmt = select(  # type: ignore[call-overload]
                    ProviderStat.provider_id,
                    ProviderStat.provider_model,
                    func.sum(ProviderStat.token_input_other),
                    func.sum(ProviderStat.token_input_cached),
                    func.sum(ProviderStat.token_output),
                    func.count(),
                ).group_by(ProviderStat.provider_id, ProviderStat.provider_model)
            else:
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
            if model:
                stmt = stmt.where(ProviderStat.provider_model == model)
            if start:
                stmt = stmt.where(ProviderStat.created_at >= start)
            if end:
                stmt = stmt.where(ProviderStat.created_at <= end)
            async with db.get_db() as session:
                result = await session.execute(stmt)
                rows = result.all()
            out: list[dict[str, Any]] = []
            for r in rows:
                if composite:
                    out.append(
                        {
                            "provider_id": str(r[0] or ""),
                            "provider_model": str(r[1] or ""),
                            "key": str(r[1] or ""),
                            "token_input_other": int(r[2] or 0),
                            "token_input_cached": int(r[3] or 0),
                            "token_output": int(r[4] or 0),
                            "count": int(r[5] or 0),
                        }
                    )
                else:
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

    async def query_usage_timeseries(
        self,
        *,
        start: datetime,
        end: datetime | None = None,
        bucket: str = "day",
        umo: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """按天 / 小时分桶聚合 token 用量与条数（基于 ``ProviderStat``，全量历史）。

        用 SQLite ``func.strftime`` 在 SQL 层按 **UTC** 分桶（避免应用层拉全量行；
        前端用 ``toLocaleDateString`` 渲染本地日标签，UTC vs 本地最多 ±1 天偏差，
        趋势看形状可接受）。``created_at`` 是 aware UTC datetime，文本序与 ISO 序一致，
        可直接用 ``>=`` / ``<=`` 做区间筛选。

        Args:
            start: 窗口起始（含）。
            end: 窗口结束（含），None 表示至今。
            bucket: ``"day"``（默认）或 ``"hour"``，其余按 day。

        Returns:
            ``[{bucket, token_input_other, token_input_cached, token_output, count}, ...]``，
            按 bucket 升序。失败返回空列表（不抛异常）。
        """
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import func, select

        try:
            db = self.context.get_db()
            bucket_col = (
                func.strftime("%Y-%m-%d %H:00", ProviderStat.created_at)
                if bucket == "hour"
                else func.strftime("%Y-%m-%d", ProviderStat.created_at)
            )
            stmt = select(  # type: ignore[call-overload]
                bucket_col,
                func.sum(ProviderStat.token_input_other),
                func.sum(ProviderStat.token_input_cached),
                func.sum(ProviderStat.token_output),
                func.count(),
            ).group_by(bucket_col)
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
                rows = result.all()
            out: list[dict[str, Any]] = []
            for r in rows:
                bkey = r[0]
                if not bkey:
                    continue
                out.append(
                    {
                        "bucket": str(bkey),
                        "token_input_other": int(r[1] or 0),
                        "token_input_cached": int(r[2] or 0),
                        "token_output": int(r[3] or 0),
                        "count": int(r[4] or 0),
                    }
                )
            out.sort(key=lambda x: x["bucket"])
            return out
        except Exception:
            return []

    async def query_usage_timeseries_by_model(
        self,
        *,
        start: datetime,
        end: datetime | None = None,
        bucket: str = "day",
        umo: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 (bucket, provider_id, provider_model) 三元组聚合用量（用于按桶计成本）。

        与 :meth:`query_usage_timeseries` 类似，但额外按模型分组，使调用方可以
        逐桶按模型定价计算成本。返回扁平行列表（每行含 bucket + model 维度）。

        Returns:
            ``[{bucket, provider_id, provider_model, token_input_other,
            token_input_cached, token_output, count}, ...]``。失败返回空列表。
        """
        from astrbot.core.db.po import ProviderStat
        from sqlmodel import func, select

        try:
            db = self.context.get_db()
            bucket_col = (
                func.strftime("%Y-%m-%d %H:00", ProviderStat.created_at)
                if bucket == "hour"
                else func.strftime("%Y-%m-%d", ProviderStat.created_at)
            )
            stmt = select(  # type: ignore[call-overload]
                bucket_col,
                ProviderStat.provider_id,
                ProviderStat.provider_model,
                func.sum(ProviderStat.token_input_other),
                func.sum(ProviderStat.token_input_cached),
                func.sum(ProviderStat.token_output),
                func.count(),
            ).group_by(
                bucket_col,
                ProviderStat.provider_id,
                ProviderStat.provider_model,
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
                rows = result.all()
            out: list[dict[str, Any]] = []
            for r in rows:
                bkey = r[0]
                if not bkey:
                    continue
                out.append(
                    {
                        "bucket": str(bkey),
                        "provider_id": str(r[1] or ""),
                        "provider_model": str(r[2] or ""),
                        "token_input_other": int(r[3] or 0),
                        "token_input_cached": int(r[4] or 0),
                        "token_output": int(r[5] or 0),
                        "count": int(r[6] or 0),
                    }
                )
            out.sort(key=lambda x: (x["bucket"], x["provider_model"]))
            return out
        except Exception:
            return []
