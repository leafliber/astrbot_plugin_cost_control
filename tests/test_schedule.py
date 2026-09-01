"""定时价格同步的调度与失败隔离测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from cost_control.schedule import CLEANUP_JOB_NAME, PRICE_SYNC_JOB_NAME, ScheduleMixin


class _FakeCronManager:
    def __init__(self) -> None:
        self.jobs: list[SimpleNamespace] = []
        self.deleted: list[str] = []
        self.added: list[dict[str, object]] = []

    async def list_jobs(self, *, job_type: str):
        assert job_type == "basic"
        return self.jobs

    async def delete_job(self, job_id: str) -> None:
        self.deleted.append(job_id)

    async def add_basic_job(self, **kwargs: object) -> None:
        self.added.append(kwargs)


class _FakeContext:
    def __init__(self, cron_manager: _FakeCronManager, config: dict[str, object]) -> None:
        self.cron_manager = cron_manager
        self._config = config

    def get_config(self):
        return self._config


class _ScheduleHost(ScheduleMixin):
    def __init__(self, *, cfg: dict[str, object], data_dir: Path) -> None:
        self.cron = _FakeCronManager()
        self.context = _FakeContext(self.cron, {"timezone": "UTC", "provider": []})
        self.cfg = cfg
        self._data_dir = str(data_dir)
        self.pricing_invalidations: list[str] = []

    def get_data_dir(self) -> str:
        return self._data_dir

    def invalidate_pricing_cache(self, reason: str) -> None:
        self.pricing_invalidations.append(reason)


def test_register_cron_keeps_price_sync_disabled_by_default(tmp_path: Path) -> None:
    host = _ScheduleHost(cfg={}, data_dir=tmp_path)

    asyncio.run(host.register_cron())

    names = {str(job["name"]) for job in host.cron.added}
    assert CLEANUP_JOB_NAME in names
    assert PRICE_SYNC_JOB_NAME not in names


def test_register_cron_adds_enabled_price_sync_and_replaces_stale_job(tmp_path: Path) -> None:
    host = _ScheduleHost(
        cfg={"price_sync": {"auto_enabled": True, "cron": "17 3 * * *"}},
        data_dir=tmp_path,
    )
    host.cron.jobs = [SimpleNamespace(name=PRICE_SYNC_JOB_NAME, job_id="old-sync")]

    asyncio.run(host.register_cron())

    assert host.cron.deleted == ["old-sync"]
    sync_jobs = [job for job in host.cron.added if job["name"] == PRICE_SYNC_JOB_NAME]
    assert len(sync_jobs) == 1
    assert sync_jobs[0]["cron_expression"] == "17 3 * * *"
    assert sync_jobs[0]["handler"] == host.sync_prices


def test_sync_prices_passes_provider_config_without_persisting_key(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    async def fake_sync_all(cfg, data_dir, *, provider_cfg):
        observed["cfg"] = cfg
        observed["data_dir"] = data_dir
        observed["provider"] = provider_cfg("gateway")
        return {"results": [{"status": "ok"}]}

    monkeypatch.setattr("cost_control.price_sources.sync_all", fake_sync_all)
    host = _ScheduleHost(cfg={"price_sources": {}}, data_dir=tmp_path)
    host.context._config["provider"] = [
        {"id": "gateway", "api_base": "https://new-api.test/v1", "key": ["secret"]}
    ]

    asyncio.run(host.sync_prices())

    assert observed["data_dir"] == str(tmp_path)
    assert observed["provider"] == host.context._config["provider"][0]
    assert host.pricing_invalidations == ["scheduled_price_sync"]


def test_sync_prices_obtains_merged_provider_from_provider_sources(tmp_path, monkeypatch) -> None:
    """cron 链路与 web 同步同口径：按 provider_source_id 合并出 api_base/key。"""
    observed: dict[str, object] = {}

    async def fake_sync_all(cfg, data_dir, *, provider_cfg):
        observed["provider"] = provider_cfg("node-a/newapi-main")
        return {"results": [{"status": "ok"}]}

    monkeypatch.setattr("cost_control.price_sources.sync_all", fake_sync_all)
    host = _ScheduleHost(cfg={"price_sources": {}}, data_dir=tmp_path)
    host.context._config.update(
        {
            "provider_sources": [
                {"id": "node-a", "api_base": "https://cron-na/v1", "key": ["sk-cron-key"]}
            ],
            "provider": [
                {"id": "node-a/newapi-main", "provider_source_id": "node-a", "model": "m1"}
            ],
        }
    )

    asyncio.run(host.sync_prices())

    p = observed["provider"]
    assert isinstance(p, dict)
    assert p["id"] == "node-a/newapi-main"
    assert p["api_base"] == "https://cron-na/v1"
    assert p["key"] == ["sk-cron-key"]


def test_grouped_cost_uses_host_effective_pricing(monkeypatch, tmp_path: Path) -> None:
    host = _ScheduleHost(cfg={}, data_dir=tmp_path)
    expected_pricing = {"catalog": {"selected": {"mode": "per_turn", "price": 0.03}}}
    observed: dict[str, object] = {}

    async def query_usage_grouped(**kwargs: object) -> list[dict[str, object]]:
        observed["query"] = kwargs
        return [{"provider_id": "gateway", "model": "image", "count": 2}]

    # _grouped_cost 现在逐行用 compute_row_cost_in_main（带主货币换算）；
    # mock 它，断言传入的 pricing 来自 host.get_pricing()。
    def fake_compute(row: object, pricing: object, main_cur: object, rates: object) -> float:
        observed["rows"] = [row]
        observed["pricing"] = pricing
        return 0.06

    host.query_usage_grouped = query_usage_grouped
    host.get_pricing = lambda: expected_pricing
    monkeypatch.setattr("cost_control.schedule.compute_row_cost_in_main", fake_compute)

    assert asyncio.run(host._grouped_cost(start=datetime.now(UTC))) == 0.06
    assert observed["pricing"] is expected_pricing
    query = observed["query"]
    assert isinstance(query, dict)
    assert query["by"] == "provider_model"
