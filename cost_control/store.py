"""存储 Mixin。

负责自有补充表的持久化（独立 sqlite 文件，放在 ``StarTools.get_data_dir`` 返回的
数据目录），并复用 AstrBot 内置 ``Preference`` 表存配置 / 状态（告警冷却、计数等）。

设计决策：补充数据（每请求的 cache_creation、归因注入量等原生 ``ProviderStat``
没有的字段）存独立 sqlite，与 AstrBot 主库解耦——零 schema 干扰、astrbot 升级免疫。
与 ``ProviderStat`` 的关联在应用层按 ``umo + provider_id + created_at`` 完成。

阶段 1 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import JSON, Field, SQLModel, select

PLUGIN_NAME = "astrbot_plugin_cost_control"


class CostSupplement(SQLModel, table=True):  # type: ignore[call-arg]
    """每请求补充采集记录。

    与 ``ProviderStat`` 一一对应（按 ``umo + provider_id + created_at`` 近似关联），
    记录原生表没有的 cache 细分、原始 usage、归因注入量等。
    """

    __tablename__ = "cost_supplements"
    # 热重载安全：插件重载时本模块被重新 import，CostSupplement 类会再次定义，
    # DeclarativeMeta 会尝试把同名 Table 二次注册进全局 SQLModel.metadata，抛
    # InvalidRequestError「Table ... is already defined」。extend_existing=True
    # 让重定义复用已注册的 Table（覆盖 columns/options），schema 不变时无副作用。
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    umo: str = Field(index=True)
    provider_id: str = Field(default="", index=True)
    provider_model: str | None = Field(default=None, index=True)
    conversation_id: str | None = Field(default=None, index=True)
    response_id: str | None = Field(default=None, index=True)

    # token 三类（冗余存储，便于独立库查询，不必 JOIN 主库）
    token_input_other: int = Field(default=0)
    token_input_cached: int = Field(default=0)
    token_output: int = Field(default=0)

    # 原生 ProviderStat 没有的 cache 细分
    cache_creation: int | None = Field(default=None)
    cache_read: int | None = Field(default=None)

    # 原始 usage（provider 原生结构的序列化，供缓存诊断排查）
    raw_usage: dict[str, Any] | None = Field(default=None, sa_type=JSON)

    # 归因（阶段 3 填充）
    injection_total: int | None = Field(default=None)
    attribution: dict[str, Any] | None = Field(default=None, sa_type=JSON)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )


class CacheEvent(SQLModel, table=True):  # type: ignore[call-arg]
    """缓存破坏诊断事件（落库，重载不丢）。

    由 ``CacheDiagMixin.run_cache_diag`` 在检测到上下文重置 / system prompt 变更 /
    工具变更 / 顺序漂移 时写入。
    """

    __tablename__ = "cache_events"
    # 热重载安全（同 CostSupplement）。
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    umo: str = Field(index=True)
    type: str = Field(index=True)
    severity: str = Field(default="medium")
    detail: str = Field(default="")
    # 上一轮 / 本轮裁剪后的上下文签名（history_len / system_hash / tools_hash /
    # contexts_count；order_drift 的 after 额外含 first_diverge_at）。供前端做
    # 结构化前后对比展示（替代仅一句 detail 文案）。
    before: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    after: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
    )


class StoreMixin:
    """补充表持久化与复用 ``Preference`` 的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any

    # 独立 sqlite engine / session maker；延迟初始化（首次 init_store 时创建）。
    # 作为类属性提供默认值，实例首次赋值后变为实例属性，多实例互不干扰。
    _engine: Any = None
    _session_maker: Any = None

    def get_data_dir(self) -> Path:
        """返回本插件的数据目录（``data/plugin_data/astrbot_plugin_cost_control``）。"""
        return StarTools.get_data_dir(PLUGIN_NAME)

    async def init_store(self) -> None:
        """初始化独立 sqlite：创建 engine + 建补充表（仅建本表，不污染全局 metadata）。"""
        data_dir = self.get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "supplement.db"
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            future=True,
            connect_args={"timeout": 30},
        )
        self._session_maker = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        # 仅创建本插件自有表，避免把 astrbot 全局 SQLModel.metadata 的其它表
        # 一并建进独立库。
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(
                    sync_conn,
                    tables=[
                        CostSupplement.__table__,  # type: ignore[attr-defined]
                        CacheEvent.__table__,  # type: ignore[attr-defined]
                    ],
                    checkfirst=True,
                )
            )

    async def _ensure_session_maker(self) -> Any:
        if self._session_maker is None:
            await self.init_store()
        return self._session_maker

    async def save_supplement(self, record: dict[str, Any]) -> None:
        """保存一条补充采集记录。

        Args:
            record: ``SupplementMixin.collect_response`` 返回的记录 dict。
        """
        row = CostSupplement(
            umo=record.get("umo", "") or "",
            provider_id=record.get("provider_id", "") or "",
            provider_model=record.get("provider_model"),
            conversation_id=record.get("conversation_id"),
            response_id=record.get("response_id"),
            token_input_other=int(record.get("token_input_other", 0) or 0),
            token_input_cached=int(record.get("token_input_cached", 0) or 0),
            token_output=int(record.get("token_output", 0) or 0),
            cache_creation=record.get("cache_creation"),
            cache_read=record.get("cache_read"),
            raw_usage=record.get("raw_usage"),
            injection_total=record.get("injection_total"),
            attribution=record.get("attribution"),
            created_at=record.get("created_at") or datetime.now(UTC),
        )
        maker = await self._ensure_session_maker()
        async with maker() as session:
            session.add(row)
            await session.commit()

    async def query_supplements(
        self,
        *,
        umo: str | None = None,
        provider_id: str | None = None,
        provider_model: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        order_by: str = "created_at",
        order_dir: str = "desc",
    ) -> list[CostSupplement]:
        """按条件查询补充记录（失败返回空列表，不抛异常）。

        Args:
            order_by: 排序列，白名单 ``created_at`` / ``token_input_other`` /
                ``token_output`` / ``umo``，其余回退 ``created_at``。
            order_dir: ``"desc"``（默认，最新优先）或 ``"asc"``。
        """
        _ORDER_WHITELIST = (
            "created_at",
            "token_input_other",
            "token_output",
            "umo",
        )
        col_name = order_by if order_by in _ORDER_WHITELIST else "created_at"
        col = getattr(CostSupplement, col_name)
        order_col = col.desc() if (order_dir or "desc").lower() != "asc" else col.asc()
        stmt = select(CostSupplement)
        if umo:
            stmt = stmt.where(CostSupplement.umo == umo)
        if provider_id:
            stmt = stmt.where(CostSupplement.provider_id == provider_id)
        if provider_model:
            stmt = stmt.where(CostSupplement.provider_model == provider_model)
        if start:
            stmt = stmt.where(CostSupplement.created_at >= start)
        if end:
            stmt = stmt.where(CostSupplement.created_at <= end)
        stmt = stmt.order_by(order_col).limit(limit)  # type: ignore[call-overload]
        try:
            maker = await self._ensure_session_maker()
            async with maker() as session:
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception:
            return []

    async def cleanup_old_supplements(self, before: datetime) -> int:
        """删除 ``created_at`` 早于 before 的补充记录与缓存事件，返回删除条数（失败返回 0）。"""
        try:
            maker = await self._ensure_session_maker()
            async with maker() as session:
                n = 0
                r1 = await session.execute(
                    delete(CostSupplement).where(CostSupplement.created_at < before)  # type: ignore[arg-type]
                )
                n += int(r1.rowcount or 0)
                r2 = await session.execute(
                    delete(CacheEvent).where(CacheEvent.created_at < before)  # type: ignore[arg-type]
                )
                n += int(r2.rowcount or 0)
                await session.commit()
                return n
        except Exception:
            return 0

    async def save_cache_event(self, record: dict[str, Any]) -> None:
        """保存一条缓存诊断事件（``run_cache_diag`` 检测到破坏时调用）。"""
        row = CacheEvent(
            umo=record.get("umo", "") or "",
            type=str(record.get("type", "") or ""),
            severity=str(record.get("severity", "medium") or "medium"),
            detail=str(record.get("detail", "") or ""),
            before=record.get("before"),
            after=record.get("after"),
            created_at=record.get("created_at") or datetime.now(UTC),
        )
        maker = await self._ensure_session_maker()
        async with maker() as session:
            session.add(row)
            await session.commit()

    async def query_cache_events(
        self,
        *,
        umo: str | None = None,
        limit: int = 50,
    ) -> list[CacheEvent]:
        """查询缓存诊断事件（最新优先，失败返回空列表）。"""
        stmt = select(CacheEvent)
        if umo:
            stmt = stmt.where(CacheEvent.umo == umo)
        order_col = getattr(CacheEvent, "created_at").desc()
        stmt = stmt.order_by(order_col).limit(limit)  # type: ignore[call-overload]
        try:
            maker = await self._ensure_session_maker()
            async with maker() as session:
                result = await session.execute(stmt)
                return list(result.scalars().all())
        except Exception:
            return []

    # ===== Preference 封装（复用 AstrBot 主库，存告警冷却 / 计数等跨会话状态） =====

    async def get_pref(
        self,
        scope: str,
        scope_id: str,
        key: str,
        default: Any = None,
    ) -> Any:
        """读取一条 Preference（value 是 dict）。找不到或出错返回 default。"""
        try:
            db = self.context.get_db()
            prefs = await db.get_preferences(scope, scope_id, key)
            if prefs:
                value = prefs[0].value
                return value if value is not None else default
        except Exception:
            pass
        return default

    async def set_pref(
        self,
        scope: str,
        scope_id: str,
        key: str,
        value: dict[str, Any],
    ) -> None:
        """写入 / 更新一条 Preference。失败静默（不崩插件）。"""
        try:
            db = self.context.get_db()
            await db.insert_preference_or_update(scope, scope_id, key, value)
        except Exception:
            pass
