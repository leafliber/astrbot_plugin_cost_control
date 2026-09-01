"""``UsageQueryMixin`` 单测：验证 ``aggregate_rows`` 聚合逻辑（纯函数）。"""

from datetime import UTC, datetime
from types import SimpleNamespace

from cost_control.usage_query import aggregate_rows, bucketize_rows


class _Row:
    """模拟 ``ProviderStat`` 行的 duck-typed 对象。"""

    def __init__(self, token_input_other: int, token_input_cached: int, token_output: int) -> None:
        self.token_input_other = token_input_other
        self.token_input_cached = token_input_cached
        self.token_output = token_output


def test_aggregate_empty():
    result = aggregate_rows([])
    assert result == {
        "token_input_other": 0,
        "token_input_cached": 0,
        "token_output": 0,
        "count": 0,
    }


def test_aggregate_single():
    result = aggregate_rows([_Row(100, 50, 200)])
    assert result["token_input_other"] == 100
    assert result["token_input_cached"] == 50
    assert result["token_output"] == 200
    assert result["count"] == 1


def test_aggregate_multiple():
    rows = [_Row(100, 50, 200), _Row(300, 0, 100), _Row(0, 0, 0)]
    result = aggregate_rows(rows)
    assert result["token_input_other"] == 400
    assert result["token_input_cached"] == 50
    assert result["token_output"] == 300
    assert result["count"] == 3


def test_aggregate_tolerates_none_attrs():
    # 缺少属性或为 None 时按 0 处理
    class _Partial:
        token_input_other = 5
        # 缺 token_input_cached / token_output

    result = aggregate_rows([_Partial()])  # type: ignore[list-item]
    assert result["token_input_other"] == 5
    assert result["token_input_cached"] == 0
    assert result["token_output"] == 0
    assert result["count"] == 1


class _TSRow:
    """带 ``created_at`` 的 duck-typed 行，用于 ``bucketize_rows``。"""

    def __init__(self, created_at: datetime, other: int, cached: int, output: int) -> None:
        self.created_at = created_at
        self.token_input_other = other
        self.token_input_cached = cached
        self.token_output = output


def test_bucketize_empty():
    assert bucketize_rows([], "day") == []


def test_bucketize_skips_missing_created_at():
    class _NoTs:
        token_input_other = 10
        token_input_cached = 0
        token_output = 5

    assert bucketize_rows([_NoTs()], "day") == []  # type: ignore[list-item]


def test_bucketize_by_day():
    rows = [
        _TSRow(datetime(2026, 6, 1, 3, 0, tzinfo=UTC), 100, 10, 50),
        _TSRow(datetime(2026, 6, 1, 22, 0, tzinfo=UTC), 200, 0, 30),
        _TSRow(datetime(2026, 6, 2, 1, 0, tzinfo=UTC), 50, 5, 20),
    ]
    result = bucketize_rows(rows, "day")
    assert [b["bucket"] for b in result] == ["2026-06-01", "2026-06-02"]
    d1 = result[0]
    assert d1["token_input_other"] == 300
    assert d1["token_output"] == 80
    assert d1["count"] == 2
    assert result[1]["count"] == 1


def test_bucketize_by_hour():
    rows = [
        _TSRow(datetime(2026, 6, 1, 1, 30, tzinfo=UTC), 10, 0, 0),
        _TSRow(datetime(2026, 6, 1, 1, 59, tzinfo=UTC), 5, 0, 0),
        _TSRow(datetime(2026, 6, 1, 2, 5, tzinfo=UTC), 7, 0, 0),
    ]
    result = bucketize_rows(rows, "hour")
    assert [b["bucket"] for b in result] == ["2026-06-01 01:00", "2026-06-01 02:00"]
    assert result[0]["token_input_other"] == 15
    assert result[1]["token_input_other"] == 7


async def test_cost_rows_keep_minute_only_for_scheduled_providers():
    from astrbot.core.db.po import ProviderStat
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.usage_query import UsageQueryMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c, tables=[ProviderStat.__table__], checkfirst=True
            )
        )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                ProviderStat(
                    umo="u",
                    provider_id="scheduled",
                    provider_model="m",
                    token_input_other=10,
                    created_at=datetime(2026, 8, 31, 1, 0, tzinfo=UTC),
                ),
                ProviderStat(
                    umo="u",
                    provider_id="scheduled",
                    provider_model="m",
                    token_input_other=20,
                    created_at=datetime(2026, 8, 31, 2, 0, tzinfo=UTC),
                ),
                ProviderStat(
                    umo="u",
                    provider_id="plain",
                    provider_model="m",
                    token_input_other=30,
                    created_at=datetime(2026, 8, 31, 1, 0, tzinfo=UTC),
                ),
                ProviderStat(
                    umo="u",
                    provider_id="plain",
                    provider_model="m",
                    token_input_other=40,
                    created_at=datetime(2026, 8, 31, 2, 0, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

    query = UsageQueryMixin()
    query.context = SimpleNamespace(get_db=lambda: SimpleNamespace(get_db=maker))
    pricing = {
        "schedules": {
            "scheduled": {
                "enabled": True,
                "periods": [{"enabled": True}],
            }
        }
    }
    rows = await query.query_usage_cost_rows(pricing)
    scheduled = [row for row in rows if row["provider_id"] == "scheduled"]
    plain = [row for row in rows if row["provider_id"] == "plain"]
    assert len(scheduled) == 2
    assert all(row["created_at"] is not None for row in scheduled)
    assert len(plain) == 1
    assert plain[0]["token_input_other"] == 70
    assert plain[0]["created_at"] is None
    await engine.dispose()
