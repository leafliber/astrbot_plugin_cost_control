"""存储 Mixin。

负责自有补充表的持久化（独立 sqlite 文件，放在 ``StarTools.get_data_dir`` 返回的
数据目录），并复用 AstrBot 内置 ``Preference`` 表存配置 / 状态（告警冷却、计数等）。

设计决策：补充数据（每请求的 cache_creation、归因注入量、user_id 等原生 ``ProviderStat``
没有的字段）存独立 sqlite，与 AstrBot 主库解耦——零 schema 干扰、astrbot 升级免疫。
与 ``ProviderStat`` 的关联在应用层按 ``umo + provider_id + created_at`` 完成。

阶段 1 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import JSON, Field, SQLModel, select

PLUGIN_NAME = "astrbot_plugin_cost_control"


class CostSupplement(SQLModel, table=True):  # type: ignore[call-arg]
    """每请求补充采集记录。

    与 ``ProviderStat`` 一一对应（按 ``umo + provider_id + created_at`` 近似关联），
    记录原生表没有的 cache 细分、原始 usage、归因注入量、发送者 user_id 等。
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
    # 用户请求 ID（一次用户消息触发的完整 pipeline；function-calling 多步 LLM 调用
    # 共享同一值）。由 on_llm_request_head 生成并挂到 event，on_llm_response 读回。
    # 用于 per_request 计费模式按 distinct request_id 计数。主表 ProviderStat 无此字段。
    request_id: str | None = Field(default=None, index=True)

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

    # 发送者 user_id（从 AstrMessageEvent.get_sender_id 读取，用于按用户的
    # 局部阈值 override；ProviderStat 无此字段，存本表）。
    user_id: str | None = Field(default=None, index=True)

    # 固化的原始货币成本金额（按当时计费货币算出，展示时按当前汇率换算到主货币）。
    cost_amount: float | None = Field(default=None)
    # 固化的原始计费货币代码（如 "USD"/"CNY"；NULL=历史数据，回退主货币）。
    currency_symbol: str | None = Field(default=None)

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
        """初始化独立 sqlite：创建 engine + 建补充表（仅建本表，不污染全局 metadata）。

        对已存在的旧库调用一次幂等 ``ALTER TABLE`` 迁移，补充缺失列与索引
        （SQLAlchemy ``create_all(checkfirst=True)`` 只建不存在的表，不加列；
        新增字段需要手工 ALTER 才能补齐）。任何失败仅记录日志，不阻断插件加载。
        """
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
            # 幂等迁移：补齐新加的列与索引（旧库只有旧 schema 时必要）。
            await self._migrate_supplement_columns(conn)

    async def _migrate_supplement_columns(self, conn: Any) -> None:
        """幂等迁移：检查 ``cost_supplements`` 实际列，缺则 ``ALTER TABLE ADD COLUMN``。

        当前迁移项：
        - ``user_id TEXT``（按用户 override 需要；索引由后续 ``CREATE INDEX IF NOT EXISTS`` 兜底）
        - ``request_id TEXT``（per_request 计费按 distinct request_id 计数；索引兜底）

        任何 SQLite 错误吞掉（开发期重命名/删除列属人为操作，不应阻断）。
        """
        try:
            res = await conn.execute(text("PRAGMA table_info(cost_supplements)"))
            cols = {row[1] for row in res.fetchall()}
            if "user_id" not in cols:
                await conn.execute(text("ALTER TABLE cost_supplements ADD COLUMN user_id TEXT"))
            if "request_id" not in cols:
                await conn.execute(text("ALTER TABLE cost_supplements ADD COLUMN request_id TEXT"))
            if "cost_amount" not in cols:
                await conn.execute(text("ALTER TABLE cost_supplements ADD COLUMN cost_amount REAL"))
            if "currency_symbol" not in cols:
                await conn.execute(
                    text("ALTER TABLE cost_supplements ADD COLUMN currency_symbol TEXT")
                )
            # 索引（IF NOT EXISTS 在 SQLite 3.8+ 可用）
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cost_supplements_user_id "
                    "ON cost_supplements (user_id)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_cost_supplements_request_id "
                    "ON cost_supplements (request_id)"
                )
            )
        except Exception:
            # 迁移失败不阻断；后续写入缺失字段会被 SQLAlchemy 兜底为 None
            pass

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
            request_id=record.get("request_id"),
            token_input_other=int(record.get("token_input_other", 0) or 0),
            token_input_cached=int(record.get("token_input_cached", 0) or 0),
            token_output=int(record.get("token_output", 0) or 0),
            cache_creation=record.get("cache_creation"),
            cache_read=record.get("cache_read"),
            raw_usage=record.get("raw_usage"),
            injection_total=record.get("injection_total"),
            attribution=record.get("attribution"),
            user_id=record.get("user_id"),
            cost_amount=record.get("cost_amount"),
            currency_symbol=record.get("currency_symbol"),
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
        user_id: str | None = None,
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
        if user_id:
            stmt = stmt.where(CostSupplement.user_id == user_id)
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

    async def query_user_token_total(
        self,
        user_id: str,
        start: datetime,
    ) -> int:
        """按 user_id 聚合自 ``start`` 以来的 token 三类总和（纯 SQL，失败返回 0）。

        用于按用户 override 的实时 used 计算（ProviderStat 无 user_id，必须查补充表）。
        """
        try:
            from sqlalchemy import func

            maker = await self._ensure_session_maker()
            async with maker() as session:
                stmt = select(
                    func.coalesce(func.sum(CostSupplement.token_input_other), 0),
                    func.coalesce(func.sum(CostSupplement.token_input_cached), 0),
                    func.coalesce(func.sum(CostSupplement.token_output), 0),
                ).where(CostSupplement.user_id == user_id)
                if start:
                    stmt = stmt.where(CostSupplement.created_at >= start)
                row = (await session.execute(stmt)).first()
                if not row:
                    return 0
                return int(row[0] or 0) + int(row[1] or 0) + int(row[2] or 0)
        except Exception:
            return 0

    async def query_user_cost_total(
        self,
        user_id: str,
        start: datetime,
        pricing: dict[str, Any],
    ) -> float:
        """按 user_id 聚合自 ``start`` 以来的花费（supplement 路径，精确含 per_request）。

        逐行按 (provider_id, model) 解析定价规则：

        - per_token / per_turn：逐行算后求和。
        - per_request：按 provider 聚合 distinct ``request_id`` 数 × price（**精确**，
          supplement 表有 request_id；主表路径无此字段只能近似）。request_id 为 NULL
          的行无法归属，跳过。
        """
        try:
            from .cost import _cost_per_token, resolve_pricing

            maker = await self._ensure_session_maker()
            async with maker() as session:
                stmt = select(CostSupplement).where(CostSupplement.user_id == user_id)
                if start:
                    stmt = stmt.where(CostSupplement.created_at >= start)
                result = await session.execute(stmt)
                rows = list(result.scalars().all())

            total = 0.0
            # per_request：按 provider 聚合 distinct request_id（精确）
            req_prices: dict[str, float] = {}
            for r in rows:
                try:
                    provider_id = getattr(r, "provider_id", "") or None
                    model = getattr(r, "provider_model", None)
                    rule = resolve_pricing(provider_id, model, pricing)
                    if rule is None:
                        continue
                    mode = rule.get("mode", "per_token")
                    if mode == "per_token":
                        total += _cost_per_token(
                            {
                                "token_input_other": int(getattr(r, "token_input_other", 0) or 0),
                                "token_input_cached": int(getattr(r, "token_input_cached", 0) or 0),
                                "token_output": int(getattr(r, "token_output", 0) or 0),
                                "cache_creation": getattr(r, "cache_creation", None),
                            },
                            rule,
                        )
                    elif mode == "per_turn":
                        total += float(rule.get("price", 0.0) or 0.0)
                    elif mode == "per_request":
                        pid = provider_id or ""
                        req_prices[pid] = float(rule.get("price", 0.0) or 0.0)
                except Exception:
                    continue

            # per_request 精确：每个 provider 的 distinct request_id 数 × price
            if req_prices:
                distinct: dict[str, set[str]] = {}
                for r in rows:
                    pid = getattr(r, "provider_id", "") or ""
                    if pid not in req_prices:
                        continue
                    rid = getattr(r, "request_id", None)
                    if rid:
                        distinct.setdefault(pid, set()).add(str(rid))
                for pid, price in req_prices.items():
                    total += len(distinct.get(pid, set())) * price
            return round(total, 6)
        except Exception:
            return 0.0

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

    async def purge_module(self, module: str) -> int:
        """清空指定模块的全部数据（不可恢复），返回删除条数。

        Args:
            module: 模块标识，支持：

                - ``supplements`` — 补充采集记录（CostSupplement 表）
                - ``cache_events`` — 缓存破坏事件（CacheEvent 表）
                - ``usage_stats`` — 原生用量记录（ProviderStat 表）
                - ``ai_diag`` — AI 诊断缓存文件

        未知模块返回 0。
        """
        try:
            if module == "supplements":
                maker = await self._ensure_session_maker()
                async with maker() as session:
                    r = await session.execute(
                        delete(CostSupplement)  # type: ignore[arg-type]
                    )
                    await session.commit()
                    return int(r.rowcount or 0)
            elif module == "cache_events":
                maker = await self._ensure_session_maker()
                async with maker() as session:
                    r = await session.execute(
                        delete(CacheEvent)  # type: ignore[arg-type]
                    )
                    await session.commit()
                    return int(r.rowcount or 0)
            elif module == "usage_stats":
                # 清空 AstrBot 原生 ProviderStat 表
                from astrbot.core.db.po import ProviderStat
                from sqlalchemy import delete as sa_delete

                session_maker = self.context.session_maker  # type: ignore[attr-defined]
                async with session_maker() as session:
                    r = await session.execute(sa_delete(ProviderStat))
                    await session.commit()
                    return int(r.rowcount or 0)
            elif module == "ai_diag":
                import os

                # _diag_cache_path 由 AiDiagMixin 提供（MRO 组合后可用）
                path = getattr(self, "_diag_cache_path", lambda: None)()
                if path and os.path.exists(path):
                    os.remove(path)
                    return 1
                return 0
        except Exception:
            return 0
        return 0

    async def backfill_cost_amounts(self, pricing: dict[str, Any]) -> int:
        """一次性回填 ``cost_amount IS NULL`` 的存量记录（幂等）。

        逐行按 ``(provider_id, provider_model)`` 解析定价规则（历史定价无 currency
        字段，按 USD 口径算），把 USD 金额固化为 ``cost_amount``、
        ``currency_symbol="USD"``。失败行跳过（保持 NULL，展示时按主货币回退重算）。
        已有值的行不动。

        Args:
            pricing: :func:`get_pricing` 返回的 ``{"defaults", "user"}`` 结构。

        Returns:
            成功回填的行数（失败返回 0）。
        """
        try:
            from .cost import compute_cost_value

            maker = await self._ensure_session_maker()
            async with maker() as session:
                # 仅取 cost_amount 为 NULL 的行
                stmt = select(CostSupplement).where(CostSupplement.cost_amount.is_(None))  # type: ignore[union-attr]
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
                n = 0
                for r in rows:
                    try:
                        usage = {
                            "token_input_other": int(getattr(r, "token_input_other", 0) or 0),
                            "token_input_cached": int(getattr(r, "token_input_cached", 0) or 0),
                            "token_output": int(getattr(r, "token_output", 0) or 0),
                            "cache_creation": getattr(r, "cache_creation", None),
                        }
                        cost_usd = round(
                            compute_cost_value(
                                usage,
                                getattr(r, "provider_id", "") or None,
                                getattr(r, "provider_model", None),
                                pricing,
                            ),
                            6,
                        )
                        r.cost_amount = cost_usd  # type: ignore[assignment]
                        r.currency_symbol = "USD"  # type: ignore[assignment]
                        n += 1
                    except Exception:
                        continue
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
