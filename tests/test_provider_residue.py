"""已删除 Provider 残留识别与精确清理测试。"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from cost_control.web_api import _deleted_provider_residues


def test_deleted_provider_residues_excludes_current_and_unions_usage_pricing():
    result = _deleted_provider_residues(
        [{"id": "active"}],
        {
            "active": {"tokens": 99, "count": 1},
            "deleted-used": {"tokens": 120, "count": 3},
        },
        {
            "active": {"mode": "per_turn", "price": 1},
            "deleted-priced": {"mode": "per_turn", "price": 2},
        },
    )

    assert result == [
        {
            "provider_id": "deleted-used",
            "tokens": 120,
            "count": 3,
            "has_pricing": False,
        },
        {
            "provider_id": "deleted-priced",
            "tokens": 0,
            "count": 0,
            "has_pricing": True,
        },
    ]


def test_reset_all_payload_clears_pricing_schedules_multipliers_and_selections():
    from cost_control.web_api import WebApiMixin

    api = WebApiMixin()
    api.cfg = {
        "pricing": {"p": {"mode": "per_turn", "price": 1}},
        "pricing_schedules": {"p": {"enabled": False, "periods": []}},
        "pricing_multipliers": {"source": 1.5},
        "price_selections": {"p": {"m": {"price_key": "litellm:m"}}},
    }
    merged, error = api._validate_save_payload(
        {
            "pricing": {},
            "pricing_schedules": {},
            "pricing_multipliers": {},
            "price_selections": {},
        }
    )
    assert error == ""
    assert merged is not None
    assert merged["pricing"] == {}
    assert merged["pricing_schedules"] == {}
    assert merged["pricing_multipliers"] == {}
    assert merged["price_selections"] == {}

    frontend = (Path(__file__).parents[1] / "frontend/src/views/PricingView.tsx").read_text(
        encoding="utf-8"
    )
    reset_call = frontend[frontend.index("const reset = async") :]
    assert "price_selections: {}" in reset_call
    assert "pricing_schedules: {}" in reset_call

    records = (Path(__file__).parents[1] / "frontend/src/views/RecordsView.tsx").read_text(
        encoding="utf-8"
    )
    assert "r.cost == null" in records
    assert '"—"' in records


async def test_expr_validate_import_error_reports_actual_exception(monkeypatch):
    import sys

    from quart import Quart

    from cost_control.web_api import WebApiMixin

    monkeypatch.setitem(sys.modules, "cost_control.expr_eval", None)
    api = WebApiMixin()
    app = Quart(__name__)

    async with app.test_request_context("/", method="POST", json={"expr": "p"}):
        result = await api.api_pricing_expr_validate()

    assert result["success"] is False
    assert "表达式引擎导入失败" in result["error"]
    assert "P2 待实现" not in result["error"]


async def test_delete_supplements_by_provider_is_exact():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.store import CostSupplement, StoreMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[CostSupplement.__table__],
                checkfirst=True,
            )
        )
    store = StoreMixin()
    store._session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    for provider_id in ("deleted", "deleted-similar", "deleted"):
        await store.save_supplement(
            {
                "umo": "session",
                "provider_id": provider_id,
                "provider_model": "model",
                "created_at": datetime.now(UTC),
            }
        )

    assert await store.delete_supplements_by_provider("deleted") == 2
    assert await store.query_supplements(provider_id="deleted", limit=10) == []
    remaining = await store.query_supplements(provider_id="deleted-similar", limit=10)
    assert len(remaining) == 1


async def test_delete_native_usage_by_provider_is_exact():
    from astrbot.core.db.po import ProviderStat
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel, select

    from cost_control.usage_query import UsageQueryMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[ProviderStat.__table__],
                checkfirst=True,
            )
        )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                ProviderStat(umo="s1", provider_id="deleted", provider_model="m"),
                ProviderStat(umo="s2", provider_id="deleted", provider_model="m"),
                ProviderStat(umo="s3", provider_id="deleted-similar", provider_model="m"),
            ]
        )
        await session.commit()

    query = UsageQueryMixin()
    query.context = SimpleNamespace(get_db=lambda: SimpleNamespace(get_db=maker))
    assert await query.delete_usage_by_provider("deleted") == 2

    async with maker() as session:
        remaining = list((await session.execute(select(ProviderStat))).scalars().all())
    assert [row.provider_id for row in remaining] == ["deleted-similar"]


async def test_delete_provider_data_rejects_active_provider():
    from quart import Quart

    from cost_control.web_api import WebApiMixin

    api = WebApiMixin()
    api._collect_provider_models = lambda: [{"id": "active"}]
    api.delete_usage_by_provider = lambda _pid: (_ for _ in ()).throw(
        AssertionError("active provider must not be deleted")
    )
    app = Quart(__name__)

    async with app.test_request_context(
        "/",
        method="POST",
        json={"provider_id": "active", "confirm": "DELETE_PROVIDER_DATA"},
    ):
        result = await api.api_action_delete_provider_data()

    assert result["success"] is False
    assert "仍在当前配置" in result["error"]


async def test_delete_provider_data_cleans_usage_supplements_and_pricing(tmp_path):
    from quart import Quart

    from cost_control.config import load_plugin_config
    from cost_control.web_api import WebApiMixin

    async def delete_usage(provider_id):
        assert provider_id == "deleted"
        return 3

    async def delete_supplements(provider_id):
        assert provider_id == "deleted"
        return 2

    api = WebApiMixin()
    api.cfg = {
        "enabled": True,
        "pricing": {
            "deleted": {"mode": "per_turn", "price": 1},
            "keep": {"mode": "per_turn", "price": 2},
        },
    }
    api._data_dir = str(tmp_path)
    api._collect_provider_models = lambda: [{"id": "keep"}]
    api.delete_usage_by_provider = delete_usage
    api.delete_supplements_by_provider = delete_supplements
    app = Quart(__name__)

    async with app.test_request_context(
        "/",
        method="POST",
        json={"provider_id": "deleted", "confirm": "DELETE_PROVIDER_DATA"},
    ):
        result = await api.api_action_delete_provider_data()

    assert result == {
        "success": True,
        "data": {
            "provider_id": "deleted",
            "usage_deleted": 3,
            "supplements_deleted": 2,
            "pricing_deleted": True,
        },
    }
    assert set(api.cfg["pricing"]) == {"keep"}
    assert set(load_plugin_config(str(tmp_path))["pricing"]) == {"keep"}
