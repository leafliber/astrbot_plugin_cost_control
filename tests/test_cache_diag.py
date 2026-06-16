"""``CacheDiagMixin`` 纯函数单测：命中率与四类破坏诊断。"""

from cost_control.cache_diag import diagnose_changes, hit_rate


def test_hit_rate_normal():
    assert hit_rate(50, 50, 0) == 50.0


def test_hit_rate_zero():
    assert hit_rate(0, 100, 0) == 0.0


def test_hit_rate_no_data():
    assert hit_rate(None, None, None) == -1.0
    assert hit_rate(0, 0, 0) == -1.0


def test_hit_rate_with_creation():
    # 50 read / (50 + 30 + 20) = 50%
    assert hit_rate(50, 30, 20) == 50.0


def _sig(history_len, system_hash="a", tools_hash="x", hashes=None):
    return {
        "history_len": history_len,
        "system_hash": system_hash,
        "tools_hash": tools_hash,
        "contexts_hashes": hashes if hashes is not None else ["a"] * history_len,
    }


def test_diagnose_context_reset():
    last = _sig(10)
    cur = _sig(3)
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "context_reset" in types


def test_diagnose_system_prompt_change():
    last = _sig(5, system_hash="a")
    cur = _sig(5, system_hash="b")
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "system_prompt_change" in types


def test_diagnose_tools_change():
    last = _sig(5, tools_hash="x")
    cur = _sig(5, tools_hash="y")
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "tools_change" in types


def test_diagnose_order_drift():
    last = _sig(3, hashes=["a", "b", "c"])
    cur = _sig(4, hashes=["a", "b", "d", "e"])
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "order_drift" in types


def test_diagnose_no_change():
    last = _sig(5)
    cur = _sig(6)  # 正常追加一条
    assert diagnose_changes(cur, last, {}) == []


def test_diagnose_flags_disable():
    last = _sig(10)
    cur = _sig(3)
    flags = {"detect_context_reset": False}
    types = [e["type"] for e in diagnose_changes(cur, last, flags)]
    assert "context_reset" not in types


# ===== CacheEvent 落库（集成，内存 sqlite，不依赖 StarTools/真实 astrbot） =====


async def test_cache_event_save_query():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.store import CacheEvent, StoreMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[CacheEvent.__table__],
                checkfirst=True,  # type: ignore[attr-defined]
            )
        )
    store = StoreMixin()
    store._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await store.save_cache_event(
        {"umo": "s1", "type": "context_reset", "severity": "high", "detail": "重置"}
    )
    await store.save_cache_event(
        {"umo": "s1", "type": "tools_change", "severity": "medium", "detail": "工具变"}
    )
    await store.save_cache_event(
        {"umo": "s2", "type": "order_drift", "severity": "medium", "detail": "顺序漂移"}
    )

    by_s1 = await store.query_cache_events(umo="s1", limit=10)
    assert len(by_s1) == 2
    assert all(getattr(r, "umo") == "s1" for r in by_s1)

    all_rows = await store.query_cache_events(limit=10)
    assert len(all_rows) == 3
    # 最新优先
    assert getattr(all_rows[0], "created_at") >= getattr(all_rows[-1], "created_at")


async def test_cache_event_query_empty():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.store import CacheEvent, StoreMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[CacheEvent.__table__],
                checkfirst=True,  # type: ignore[attr-defined]
            )
        )
    store = StoreMixin()
    store._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    assert await store.query_cache_events(limit=10) == []
