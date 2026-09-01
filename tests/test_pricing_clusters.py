"""AstrBot Provider Source 聚类与倍率规范化测试。"""

from types import SimpleNamespace

from cost_control.pricing_clusters import (
    build_provider_pricing_clusters,
    normalize_pricing_multipliers,
    provider_cluster_map_from_config,
)


def test_provider_cluster_map_uses_astrbot_provider_source_id():
    assert provider_cluster_map_from_config(
        {
            "provider": [
                {"id": "gpt-4o", "provider_source_id": "openai-main"},
                {"id": "gpt-4.1", "provider_source_id": "openai-main"},
                {"id": "legacy-standalone"},
            ]
        }
    ) == {
        "gpt-4o": "openai-main",
        "gpt-4.1": "openai-main",
        "legacy-standalone": "legacy-standalone",
    }


def test_normalize_pricing_multipliers_accepts_dynamic_source_ids():
    assert normalize_pricing_multipliers(
        {
            "openai-main": "1.5",
            "用户自定义供应商": 2,
            "default-one": 1,
            "free": 0,
            "negative": -1,
            "too-large": 101,
            "": 2,
        }
    ) == {"openai-main": 1.5, "用户自定义供应商": 2.0, "free": 0.0}


def test_build_provider_pricing_clusters_only_groups_existing_providers():
    catalog = build_provider_pricing_clusters(
        [
            {
                "id": "gpt-4o",
                "supplier_id": "openai-main",
                "supplier_name": "OpenAI 主账号",
            },
            {
                "id": "gpt-4.1",
                "supplier_id": "openai-main",
                "supplier_name": "OpenAI 主账号",
            },
            {
                "id": "claude",
                "supplier_id": "anthropic-main",
                "supplier_name": "Anthropic",
            },
        ]
    )
    assert catalog == [
        {
            "id": "openai-main",
            "name": "OpenAI 主账号",
            "provider_ids": ["gpt-4o", "gpt-4.1"],
        },
        {
            "id": "anthropic-main",
            "name": "Anthropic",
            "provider_ids": ["claude"],
        },
    ]


def test_collect_provider_models_uses_astrbot_provider_sources():
    from cost_control.web_api import WebApiMixin

    api = WebApiMixin()
    api.context = SimpleNamespace(
        get_config=lambda: {
            "provider_sources": [{"id": "openai-main", "name": "OpenAI 主账号", "type": "openai"}],
            "provider": [
                {
                    "id": "gpt-primary",
                    "provider_source_id": "openai-main",
                    "model": "gpt-4o",
                    "enable": True,
                },
                {
                    "id": "gpt-cheap",
                    "provider_source_id": "openai-main",
                    "model": "gpt-4o-mini",
                    "enable": True,
                },
            ],
        },
        get_all_providers=lambda: [],
    )

    providers = api._collect_provider_models()
    assert [provider["id"] for provider in providers] == ["gpt-primary", "gpt-cheap"]
    assert {provider["supplier_id"] for provider in providers} == {"openai-main"}
    assert {provider["supplier_name"] for provider in providers} == {"OpenAI 主账号"}


def test_save_payload_accepts_and_normalizes_pricing_multipliers():
    from cost_control.web_api import WebApiMixin

    api = WebApiMixin()
    api.cfg = {}
    config, error = api._validate_save_payload(
        {"pricing_multipliers": {"openai-main": "1.5", "default": 1}}
    )
    assert error == ""
    assert config is not None
    assert config["pricing_multipliers"] == {"openai-main": 1.5}
