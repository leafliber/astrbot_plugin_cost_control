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


def test_diagnose_includes_before_after():
    last = _sig(8, system_hash="a", tools_hash="x")
    cur = _sig(3, system_hash="a", tools_hash="x")  # 触发 context_reset
    evs = diagnose_changes(cur, last, {})
    reset = next(e for e in evs if e["type"] == "context_reset")
    # 裁剪后的前后签名落库，便于前端结构化对比
    assert reset["before"] == {
        "history_len": 8,
        "system_hash": "a",
        "tools_hash": "x",
        "contexts_count": 8,
    }
    assert reset["after"]["history_len"] == 3
    assert reset["after"]["contexts_count"] == 3


def test_diagnose_order_drift_has_diverge():
    last = _sig(3, hashes=["a", "b", "c"])
    cur = _sig(4, hashes=["a", "b", "d", "e"])  # 下标 2 开始分歧
    evs = diagnose_changes(cur, last, {})
    drift = next(e for e in evs if e["type"] == "order_drift")
    assert drift["after"]["first_diverge_at"] == 2


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
        {
            "umo": "s1",
            "type": "context_reset",
            "severity": "high",
            "detail": "重置",
            "before": {
                "history_len": 8,
                "system_hash": "a",
                "tools_hash": "x",
                "contexts_count": 8,
            },
            "after": {
                "history_len": 3,
                "system_hash": "a",
                "tools_hash": "x",
                "contexts_count": 3,
            },
        }
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
    # before/after 结构化字段正确落库并可查回
    reset_row = next(r for r in by_s1 if getattr(r, "type") == "context_reset")
    assert reset_row.before["history_len"] == 8
    assert reset_row.after["history_len"] == 3

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
