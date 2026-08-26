"""price_sources 单元测试：adapter 换算 + 同步执行器（HTTP 全 mock，离线运行）。"""

from __future__ import annotations

import asyncio

import pytest

from cost_control import price_sources as ps
from cost_control.price_catalog import SourceStatus, load_catalog

# ---- adapter 换算 ----

MODELSDEV = {
    "providers": {
        "openai": {
            "models": {
                "gpt-4o": {
                    "cost": {
                        "input": 2.5,
                        "output": 10.0,
                        "cache_read": 1.25,
                        "cache_write": 3.125,
                        "tiers": [
                            {
                                "tier": {"type": "context", "size": 200000},
                                "input": 5.0,
                                "output": 15.0,
                            }
                        ],
                    },
                    "experimental": {"modes": {"fast": {"cost": {"input": 5.0, "output": 20.0}}}},
                },
                "bad": {"name": "no-cost"},
            }
        }
    }
}


def test_modelsdev_basic_and_tiers():
    prices = ps.adapt_modelsdev(MODELSDEV)
    assert "openai/gpt-4o" in prices
    assert "openai/bad" not in prices  # 无 cost 字段跳过
    p = prices["openai/gpt-4o"]
    assert p.mode == "per_tier"
    assert p.prompt == 2.5
    assert p.completion == 10.0
    assert p.cache_read == 1.25
    assert p.cache_creation == 3.125
    assert len(p.context_tiers) == 1
    assert p.context_tiers[0]["threshold_tokens"] == 200000
    assert len(p.service_tiers) == 1
    assert p.service_tiers[0]["match"] == "priority"
    assert all(p.configured.values())


def test_modelsdev_old_top_level_format():
    # 旧格式：顶层即 provider map（值含 models 字段）。
    raw = {"openai": {"models": {"m1": {"cost": {"input": 1.0}}}}}
    prices = ps.adapt_modelsdev(raw)
    assert prices["openai/m1"].prompt == 1.0
    assert prices["openai/m1"].mode == "per_token"


def test_litellm_per_token_to_per_million():
    raw = {
        "gpt-4o": {
            "input_cost_per_token": 2.5e-6,
            "output_cost_per_token": 10e-6,
            "cache_read_input_token_cost": 1.25e-6,
            "cache_creation_input_token_cost": 3.125e-6,
        },
        "meta": {"note": "not a model"},
    }
    prices = ps.adapt_litellm(raw)
    p = prices["gpt-4o"]
    assert p.prompt == pytest.approx(2.5)
    assert p.completion == pytest.approx(10.0)
    assert p.cache_read == pytest.approx(1.25)
    assert p.cache_creation == pytest.approx(3.125)
    assert p.mode == "per_token"
    assert "meta" not in prices  # 无价格字段跳过


def test_openrouter_string_prices():
    raw = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "pricing": {
                    "prompt": "0.0000025",
                    "completion": "0.00001",
                    "input_cache_read": "0.00000125",
                    "input_cache_write": "0.000003125",
                },
            },
            {"id": "no-pricing"},
        ]
    }
    prices = ps.adapt_openrouter(raw)
    p = prices["openai/gpt-4o"]
    assert p.prompt == pytest.approx(2.5)
    assert p.completion == pytest.approx(10.0)
    assert p.cache_read == pytest.approx(1.25)
    assert p.cache_creation == pytest.approx(3.125)
    assert "no-pricing" not in prices


# ---- New API ----


def test_derive_newapi_root():
    assert ps.derive_newapi_root("https://na.example.com/v1") == "https://na.example.com"
    assert ps.derive_newapi_root("https://na.example.com/v1/") == "https://na.example.com"
    assert ps.derive_newapi_root("https://na.example.com/sub/v1") == "https://na.example.com/sub"
    assert ps.derive_newapi_root("https://na.example.com") == "https://na.example.com"
    assert ps.derive_newapi_root("") == ""


def test_newapi_ratio_mode():
    raw = {
        "data": [
            {
                "model_name": "gpt-4o",
                "quota_type": 0,
                "model_ratio": 1.25,
                "completion_ratio": 4,
                "cache_ratio": 0.5,
                "create_cache_ratio": 1.25,
                "group_ratio": {"default": 1},
                "enable_groups": ["default"],
            }
        ]
    }
    prices = ps.adapt_newapi(raw, "newapi:us")
    p = prices["gpt-4o"]
    # 1 ratio = $2/1M
    assert p.prompt == pytest.approx(2.5)
    assert p.completion == pytest.approx(10.0)
    assert p.cache_read == pytest.approx(1.25)
    assert p.cache_creation == pytest.approx(3.125)
    assert all(p.configured.values())


def test_newapi_defaults_when_ratios_missing():
    raw = {"data": [{"model_name": "x", "model_ratio": 1.0}]}
    p = ps.adapt_newapi(raw, "newapi:us")["x"]
    assert p.prompt == pytest.approx(2.0)
    assert p.completion == pytest.approx(2.0)  # completion_ratio 缺省=1
    assert p.cache_read == pytest.approx(2.0)  # cache_ratio 缺省=输入价
    assert p.cache_creation == pytest.approx(2.5)  # create_cache_ratio 缺省=1.25×输入
    assert p.configured["prompt"] is True
    assert p.configured["completion"] is False  # 缺省派生，非显式配置


def test_newapi_fixed_price(tmp_path):
    raw = {"data": [{"model_name": "img", "quota_type": 1, "model_price": 0.03}]}
    p = ps.adapt_newapi(raw, "newapi:us")["img"]
    assert p.mode == "per_turn"
    assert p.price == pytest.approx(0.03)

    from cost_control.price_catalog import PriceCatalog

    catalog = PriceCatalog()
    catalog.replace_source_prices("newapi:us", {p.source_model_id: p})
    catalog.save(str(tmp_path))
    loaded = load_catalog(str(tmp_path))
    assert loaded.prices[p.price_key].price == pytest.approx(0.03)


def test_newapi_tiered_expr_preserved():
    expr = 'p <= 200000 ? tier("std", p*2+c*8) : tier("long", p*4+c*16)'
    raw = {"data": [{"model_name": "glm", "billing_mode": "tiered_expr", "billing_expr": expr}]}
    p = ps.adapt_newapi(raw, "newapi:us")["glm"]
    assert p.mode == "tiered_expr"
    assert p.expr == expr  # 原样保留


def test_adapt_source_dispatch_and_unknown():
    assert ps.adapt_source("modelsdev", MODELSDEV)
    assert ps.adapt_source("newapi:us", {"data": [{"model_name": "m", "model_ratio": 1}]})
    assert ps.adapt_source("unknown-src", {}) == {}


# ---- 同步执行器（mock HTTP）----


def _mock_http(body_by_url):
    """返回 _http_get 替身：body_by_url[url] = (payload_dict, status, headers, error)。"""

    def fake(url, headers, timeout):
        if url not in body_by_url:
            return None, 0, {}, "connection refused"
        payload, status, resp_headers, err = body_by_url[url]
        import json as _json

        body = None if payload is None else _json.dumps(payload).encode()
        return body, status, resp_headers, err

    return fake


def test_sync_source_success(monkeypatch):
    url = ps.SOURCE_URLS["litellm"]
    monkeypatch.setattr(
        ps, "_http_get", _mock_http({url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, "")})
    )
    res = ps.sync_source("litellm")
    assert res.status == "ok"
    assert res.models == 1
    assert res.prices["m"].prompt == pytest.approx(1.0)


def test_sync_source_modelsdev_304(monkeypatch):
    url = ps.SOURCE_URLS["modelsdev"]
    monkeypatch.setattr(ps, "_http_get", _mock_http({url: (None, 304, {}, "")}))
    prev = SourceStatus(source="modelsdev", status="ok", models=5, etag="v1")
    res = ps.sync_source("modelsdev", prev)
    assert res.status == "ok" and res.not_modified is True
    assert res.models == 5 and res.etag == "v1"
    assert res.prices == {}


def test_sync_source_http_error(monkeypatch):
    url = ps.SOURCE_URLS["openrouter"]
    monkeypatch.setattr(ps, "_http_get", _mock_http({url: (None, 500, {}, "HTTP 500")}))
    res = ps.sync_source("openrouter")
    assert res.status == "error"
    assert res.error == "HTTP 500"
    assert res.prices == {}


def test_sync_source_unknown():
    res = ps.sync_source("nope")
    assert res.status == "error"
    assert "未知价格源" in res.error


def test_sync_source_newapi_uses_provider_key(monkeypatch):
    captured = {}

    def fake(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return (
            __import__("json").dumps({"data": [{"model_name": "m", "model_ratio": 1}]}).encode(),
            200,
            {},
            "",
        )

    monkeypatch.setattr(ps, "_http_get", fake)
    cfg = {"price_sources": {"newapi:us": {"enabled": True, "use_provider_key": True}}}
    provider_cfg = lambda pid: {"api_base": "https://na/v1", "key": ["sk-secret"]}  # noqa: E731
    res = ps.sync_source("newapi:us", None, cfg=cfg, provider_cfg=provider_cfg)
    assert res.status == "ok"
    assert captured["url"] == "https://na/api/pricing"
    assert captured["headers"]["Authorization"] == "Bearer sk-secret"



def test_sync_source_newapi_strips_v1_from_manual_base_url(monkeypatch):
    """手动填写的 base_url 带 /v1 时同样剥掉，避免拼出 /v1/api/pricing。"""
    captured = {}

    def fake(url, headers, timeout):
        captured["url"] = url
        return b"{}", 200, {}, ""

    monkeypatch.setattr(ps, "_http_get", fake)
    cfg = {"price_sources": {"newapi:us": {"enabled": True, "base_url": "https://na/x/v1/"}}}
    res = ps.sync_source("newapi:us", None, cfg=cfg, provider_cfg=lambda pid: None)
    assert res.status == "ok"
    assert captured["url"] == "https://na/x/api/pricing"


def test_provider_cfg_fn_falls_back_to_provider_sources_api_base():
    """4.25+ source 架构：provider 无顶层 api_base 时从 provider_sources[] 回填。"""
    from types import SimpleNamespace

    from cost_control.web_api import WebApiMixin

    api = WebApiMixin()
    api.context = SimpleNamespace(
        get_config=lambda: {
            "provider_sources": [
                {"id": "newapi", "api_base": "http://localhost:3000/v1", "enable": True},
            ],
            "provider": [
                {"id": "newapi/glm-5.2", "provider_source_id": "newapi", "model": "glm-5.2"},
            ],
        },
    )
    fn = api._provider_cfg_fn()
    p = fn("newapi/glm-5.2")
    assert p is not None
    assert p["api_base"] == "http://localhost:3000/v1"
    # derive 链路最终得到正确 URL
    url = ps.newapi_pricing_url(ps.derive_newapi_root(str(p["api_base"])))
    assert url == "http://localhost:3000/api/pricing"
    # 顶层有 api_base 的旧结构不受影响
    legacy_fn_ret = None
    api.context.get_config = lambda: {
        "provider": [{"id": "p1", "api_base": "https://x/v1"}],
    }
    legacy_fn_ret = api._provider_cfg_fn()("p1")
    assert legacy_fn_ret["api_base"] == "https://x/v1"

def test_sync_all_isolates_failures_and_saves(monkeypatch, tmp_path):
    litellm_url = ps.SOURCE_URLS["litellm"]
    bodies = {
        litellm_url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, ""),
        ps.SOURCE_URLS["openrouter"]: (None, 500, {}, "HTTP 500"),
        ps.SOURCE_URLS["modelsdev"]: (None, 0, {}, "timeout"),
    }
    monkeypatch.setattr(ps, "_http_get", _mock_http(bodies))
    cfg = {
        "price_sources": {
            "modelsdev": {"enabled": True},
            "litellm": {"enabled": True},
            "openrouter": {"enabled": True},
        }
    }
    out = asyncio.run(ps.sync_all(cfg, str(tmp_path)))
    assert out["ok"] is False  # 有源失败
    by = {r["source"]: r for r in out["results"]}
    assert by["litellm"]["status"] == "ok"
    assert by["openrouter"]["status"] == "error"
    assert by["modelsdev"]["status"] == "error"

    cat = load_catalog(str(tmp_path))
    # 成功源写入价格
    assert cat.prices_for_source("litellm")
    assert cat.prices["litellm:m"].prompt == pytest.approx(1.0)
    # 失败源状态 error，且无价格
    assert cat.get_source("openrouter").status == "error"
    assert cat.prices_for_source("openrouter") == {}


def test_sync_all_failure_preserves_old_prices(monkeypatch, tmp_path):
    # 先成功写入 litellm
    litellm_url = ps.SOURCE_URLS["litellm"]
    monkeypatch.setattr(
        ps,
        "_http_get",
        _mock_http({litellm_url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, "")}),
    )
    cfg = {"price_sources": {"litellm": {"enabled": True}}}
    asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert load_catalog(str(tmp_path)).prices_for_source("litellm")

    # 再次同步失败：旧价格保留
    monkeypatch.setattr(ps, "_http_get", _mock_http({litellm_url: (None, 500, {}, "HTTP 500")}))
    out = asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert out["ok"] is False
    cat = load_catalog(str(tmp_path))
    assert cat.prices["litellm:m"].prompt == pytest.approx(1.0)  # 旧数据未丢
    assert cat.get_source("litellm").status == "error"
    assert cat.get_source("litellm").models == 1  # 保留旧计数
