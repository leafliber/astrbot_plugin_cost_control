"""price_sources 单元测试：adapter 换算 + 同步执行器（HTTP 全 mock，离线运行）。"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time

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


def test_newapi_provider_id_supports_slashes_and_colons():
    # 4.25+ provider id 形如 <source>/<model>（含斜杠）；源 ID 后缀允许后续冒号
    assert ps.newapi_provider_id("newapi:newapi/glm-5.2") == "newapi/glm-5.2"
    assert ps.newapi_provider_id("newapi:a/b/c:v2") == "a/b/c:v2"
    assert ps.newapi_provider_id("newapi:") is None
    assert ps.newapi_provider_id("newapi:  ") is None
    assert ps.newapi_provider_id("litellm") is None


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


# ---- tiered_expr 导入验证的补充：web 全链路合并 key（详见上方 v1/invalid 两组）----


def test_web_provider_cfg_fn_merges_key_end_to_end(monkeypatch):
    """web 同步链路：_provider_cfg_fn 合并 provider_sources 的 api_base 与 key，
    sync_source 据此生成 Authorization；provider 显式字段保持权威。"""
    from types import SimpleNamespace

    from cost_control.web_api import WebApiMixin

    captured: dict[str, object] = {}

    def fake_http(url, headers, timeout):
        captured["url"] = url
        captured["authorization"] = headers.get("Authorization", "")
        return (
            __import__("json").dumps({"data": [{"model_name": "m", "model_ratio": 1}]}).encode(),
            200,
            {},
            "",
        )

    monkeypatch.setattr(ps, "http_get", fake_http)
    api = WebApiMixin()
    api.context = SimpleNamespace(
        get_config=lambda: {
            "provider_sources": [
                {"id": "na", "api_base": "https://src.example.com/v1", "key": ["sk-src-key"]}
            ],
            "provider": [{"id": "na/glm-5.2", "provider_source_id": "na", "model": "glm-5.2"}],
        },
    )
    cfg = {"price_sources": {"newapi:na-gateway": {"enabled": True, "provider_id": "na/glm-5.2"}}}
    res = ps.sync_source("newapi:na-gateway", None, cfg=cfg, provider_cfg=api._provider_cfg_fn())
    assert res.status == "ok"
    assert captured["url"] == "https://src.example.com/api/pricing"
    assert captured["authorization"] == "Bearer sk-src-key"


def test_adapt_source_dispatch_and_unknown():
    assert ps.adapt_source("modelsdev", MODELSDEV)
    assert ps.adapt_source("newapi:us", {"data": [{"model_name": "m", "model_ratio": 1}]})
    assert ps.adapt_source("unknown-src", {}) == {}


# ---- 同步执行器（mock HTTP）----


def _mock_http(body_by_url):
    """返回 http_get 替身：body_by_url[url] = (payload_dict, status, headers, error)。"""

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
        ps, "http_get", _mock_http({url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, "")})
    )
    res = ps.sync_source("litellm")
    assert res.status == "ok"
    assert res.models == 1
    assert res.prices["m"].prompt == pytest.approx(1.0)


def test_sync_source_modelsdev_304(monkeypatch):
    url = ps.SOURCE_URLS["modelsdev"]
    monkeypatch.setattr(ps, "http_get", _mock_http({url: (None, 304, {}, "")}))
    prev = SourceStatus(source="modelsdev", status="ok", models=5, etag="v1")
    res = ps.sync_source("modelsdev", prev)
    assert res.status == "ok" and res.not_modified is True
    assert res.models == 5 and res.etag == "v1"
    assert res.prices == {}


def test_sync_source_http_error(monkeypatch):
    url = ps.SOURCE_URLS["openrouter"]
    monkeypatch.setattr(ps, "http_get", _mock_http({url: (None, 500, {}, "HTTP 500")}))
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

    monkeypatch.setattr(ps, "http_get", fake)
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
        return b'{"data":[{"model_name":"m","model_ratio":1}]}', 200, {}, ""

    monkeypatch.setattr(ps, "http_get", fake)
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


def test_sync_source_newapi_settings_provider_id_is_authoritative(monkeypatch):
    """settings.provider_id 存在时优先于源 ID 后缀定位 provider。"""
    captured: dict[str, object] = {}

    def fake(url, headers, timeout):
        captured["url"] = url
        captured["authorization"] = headers.get("Authorization", "")
        return (
            __import__("json").dumps({"data": [{"model_name": "m", "model_ratio": 1}]}).encode(),
            200,
            {},
            "",
        )

    monkeypatch.setattr(ps, "http_get", fake)

    def provider_cfg(pid):
        captured["requested_pid"] = pid
        return {
            "id": pid,
            "api_base": "https://na.example.com/internal/v1/",
            "key": ["sk-real-key"],
        }

    cfg = {
        "price_sources": {
            # 后缀 backup-node 不是 provider id；provider 指向含斜杠的正式 id
            "newapi:backup-node": {"enabled": True, "provider_id": "node-a/newapi-main"}
        }
    }
    res = ps.sync_source("newapi:backup-node", None, cfg=cfg, provider_cfg=provider_cfg)
    assert res.status == "ok"
    assert captured["requested_pid"] == "node-a/newapi-main"  # 不再回退到源 ID 后缀
    assert res.provider_id == "node-a/newapi-main"
    assert captured["url"] == "https://na.example.com/internal/api/pricing"
    assert captured["authorization"] == "Bearer sk-real-key"


def test_sync_source_newapi_falls_back_to_source_suffix(monkeypatch):
    """settings 未配 provider_id 时保持旧版语义：从源 ID 后缀解析。"""
    captured: dict[str, object] = {}

    def fake(url, headers, timeout):
        captured["url"] = url
        return b'{"data":[{"model_name":"m","model_ratio":1}]}', 200, {}, ""

    monkeypatch.setattr(ps, "http_get", fake)

    def provider_cfg(pid):
        captured["pid"] = pid
        return {"id": pid, "api_base": "https://legacy/v1", "key": "legacy-k"}

    cfg = {"price_sources": {"newapi:us-eu": {"enabled": True}}}
    res = ps.sync_source("newapi:us-eu", None, cfg=cfg, provider_cfg=provider_cfg)
    assert res.status == "ok"
    assert captured["pid"] == "us-eu"
    assert res.provider_id == "us-eu"
    assert captured["url"] == "https://legacy/api/pricing"


def test_merged_provider_config_merges_source_base_and_key():
    """>=4.25.5：api_base/key 只在 provider_sources 上时经合并回填；
    provider 自身字段与 id 保持权威。"""
    cfg = {
        "provider_sources": [
            {
                "id": "na",
                "type": "openai_compatible",
                "api_base": "https://na.example.com/v1",
                "key": ["sk-src"],
                "enable": True,
            },
        ],
        "provider": [
            {"id": "na/glm-5.2", "type": "openai_compatible", "provider_source_id": "na"},
        ],
    }
    merged = ps.merged_provider_config(cfg, "na/glm-5.2")
    assert merged is not None
    assert merged["id"] == "na/glm-5.2"
    assert merged["api_base"] == "https://na.example.com/v1"
    assert merged["key"] == ["sk-src"]
    # provider 显式字段覆盖 source 同名键，且不改写入参
    cfg["provider"][0]["api_base"] = "https://override/v1"
    merged2 = ps.merged_provider_config(cfg, "na/glm-5.2")
    assert merged2 is not None
    assert merged2["api_base"] == "https://override/v1"
    assert cfg["provider_sources"][0]["api_base"] == "https://na.example.com/v1"
    assert ps.merged_provider_config(cfg, "missing") is None
    assert ps.merged_provider_config(None, "x") is None


def test_merged_provider_config_flat_legacy_passthrough():
    """平铺旧结构：无 provider_source_id 的 provider 原样返回，不被同名 source 干扰。"""
    flat = {"id": "p1", "api_base": "https://flat/v1", "key": ["flat-k"]}
    cfg = {
        "provider_sources": [{"id": "p1", "api_base": "https://bogus", "key": ["bad"]}],
        "provider": [flat],
    }
    got = ps.merged_provider_config(cfg, "p1")
    assert got == flat
    assert got is not flat  # 返回副本，避免调用方污染全局配置


def _enable_debug_caplog(caplog) -> str:
    """返回 price_sources logger 名并提升捕获级别到 DEBUG。"""
    name = f"{ps.__name__}"
    caplog.set_level(logging.DEBUG, logger=name)
    return name


def test_newapi_resolution_debug_logs_without_secrets(caplog, monkeypatch):
    """诊断日志含 source/provider_id/归一化 origin/has_key 布尔，绝不含密钥本体。"""
    _enable_debug_caplog(caplog)
    url = "https://edge.example.com/api/pricing"
    monkeypatch.setattr(
        ps,
        "http_get",
        _mock_http({url: ({"data": [{"model_name": "m", "model_ratio": 1}]}, 200, {}, "")}),
    )
    cfg = {"price_sources": {"newapi:edge": {"enabled": True}}}
    provider = {"id": "edge", "api_base": "https://edge.example.com/v1/", "key": ["sk-TOPSECRET"]}
    res = ps.sync_source("newapi:edge", None, cfg=cfg, provider_cfg=lambda pid: provider)
    assert res.status == "ok"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "source=newapi:edge" in text
    assert "provider_id=edge" in text
    assert "origin=https://edge.example.com" in text  # 归一化：去尾部 /v1 与 /v1/
    assert "has_key=True" in text
    assert "sk-TOPSECRET" not in text


def test_newapi_http_bearer_warns_without_blocking_or_leaking_key(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=ps.__name__)
    captured: dict[str, str] = {}

    def fake_http(url, headers, timeout):
        captured["url"] = url
        captured["authorization"] = headers.get("Authorization", "")
        return b'{"data":[{"model_name":"m","model_ratio":1}]}', 200, {}, ""

    monkeypatch.setattr(ps, "http_get", fake_http)
    cfg = {"price_sources": {"newapi:edge": {"enabled": True}}}
    provider = {
        "id": "edge",
        "api_base": "http://newapi.internal:3000/v1",
        "key": ["plain-http-secret"],
    }

    res = ps.sync_source("newapi:edge", None, cfg=cfg, provider_cfg=lambda pid: provider)

    assert res.status == "ok"
    assert captured == {
        "url": "http://newapi.internal:3000/api/pricing",
        "authorization": "Bearer plain-http-secret",
    }
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "明文 HTTP" in text
    assert "class=insecure_http_bearer" in text
    assert "source=newapi:edge" in text
    assert "provider_id=edge" in text
    assert "origin=http://newapi.internal:3000" in text
    assert "plain-http-secret" not in text


@pytest.mark.parametrize(
    ("api_base", "key"),
    [
        ("https://newapi.internal/v1", ["https-secret"]),
        ("http://newapi.internal/v1", []),
    ],
)
def test_newapi_http_bearer_warning_requires_http_and_key(caplog, api_base, key):
    caplog.set_level(logging.WARNING, logger=ps.__name__)
    cfg = {"price_sources": {"newapi:edge": {"enabled": True}}}
    provider = {"id": "edge", "api_base": api_base, "key": key}

    _, _, error, _, _ = ps._resolve_source_request(
        "newapi:edge",
        cfg,
        lambda pid: provider,
    )

    assert error == ""
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "insecure_http_bearer" not in text
    assert "https-secret" not in text


def test_newapi_resolution_debug_logs_missing_key_as_false(caplog, monkeypatch):
    _enable_debug_caplog(caplog)
    monkeypatch.setattr(
        ps,
        "http_get",
        _mock_http(
            {
                "https://bare/api/pricing": (
                    {"data": [{"model_name": "m", "model_ratio": 1}]},
                    200,
                    {},
                    "",
                )
            }
        ),
    )
    cfg = {"price_sources": {"newapi:bare": {"enabled": True}}}
    provider = {"id": "bare", "api_base": "https://bare/v1"}
    res = ps.sync_source("newapi:bare", None, cfg=cfg, provider_cfg=lambda pid: provider)
    assert res.status == "ok"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "has_key=False" in text


def test_newapi_resolution_debug_logs_strip_url_credentials(caplog, monkeypatch):
    """api_base 携带 userinfo 凭据时，日志 origin 只保留 host 级信息。"""
    _enable_debug_caplog(caplog)
    monkeypatch.setattr(
        ps,
        "http_get",
        _mock_http(
            {
                "https://sk-in-url@na.example.com/api/pricing": (
                    {"data": [{"model_name": "m", "model_ratio": 1}]},
                    200,
                    {},
                    "",
                )
            }
        ),
    )
    cfg = {"price_sources": {"newapi:na": {"enabled": True}}}
    provider = {"id": "na", "api_base": "https://sk-in-url@na.example.com/v1"}
    res = ps.sync_source("newapi:na", None, cfg=cfg, provider_cfg=lambda pid: provider)
    assert res.status == "ok"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "origin=https://na.example.com" in text
    assert "sk-in-url" not in text


def test_sync_all_isolates_failures_and_saves(monkeypatch, tmp_path):
    litellm_url = ps.SOURCE_URLS["litellm"]
    bodies = {
        litellm_url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, ""),
        ps.SOURCE_URLS["openrouter"]: (None, 500, {}, "HTTP 500"),
        ps.SOURCE_URLS["modelsdev"]: (None, 0, {}, "timeout"),
    }
    monkeypatch.setattr(ps, "http_get", _mock_http(bodies))
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
        "http_get",
        _mock_http({litellm_url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, "")}),
    )
    cfg = {"price_sources": {"litellm": {"enabled": True}}}
    asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert load_catalog(str(tmp_path)).prices_for_source("litellm")

    # 再次同步失败：旧价格保留
    monkeypatch.setattr(ps, "http_get", _mock_http({litellm_url: (None, 500, {}, "HTTP 500")}))
    out = asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert out["ok"] is False
    cat = load_catalog(str(tmp_path))
    assert cat.prices["litellm:m"].prompt == pytest.approx(1.0)  # 旧数据未丢
    assert cat.get_source("litellm").status == "error"
    assert cat.get_source("litellm").models == 1  # 保留旧计数


# ---- tiered_expr 导入验证与 v1: 前缀（P1-2）----


def test_newapi_tiered_expr_v1_prefix_preserved_and_executable():
    """上游 ``v1:`` 前缀原样入目录；剥离后与裸主体等价可执行。"""
    from cost_control import expr_eval as ee

    body = 'p <= 200000 ? tier("std", p*2+c*8) : tier("long", p*4+c*16)'
    raw = {
        "data": [
            {"model_name": "glm", "billing_mode": "tiered_expr", "billing_expr": f"v1:{body}"},
        ]
    }
    p = ps.adapt_newapi(raw, "newapi:us")["glm"]
    assert p.mode == "tiered_expr"
    assert p.expr == f"v1:{body}"  # 原文保留（展示/存储口径）
    assert ee.eval_tiered_expr(p.expr, {"p": 100000, "c": 5000}) == pytest.approx(
        ee.eval_tiered_expr(body, {"p": 100000, "c": 5000})
    )
    val, tier = ee.eval_tiered_expr(p.expr, {"p": 100000, "c": 5000})
    assert (val, tier) == (pytest.approx(240000.0), "std")


def test_newapi_invalid_catalog_expression_skipped_with_debug(caplog):
    """导入前验证：非法表达式整条跳过（不进目录），debug 日志给出 source/model/类别。"""
    import logging

    caplog.set_level(logging.DEBUG, logger=ps.__name__)
    raw = {
        "data": [
            {"model_name": "bad-expr", "billing_mode": "tiered_expr", "billing_expr": "v1:p * * c"},
            {
                "model_name": "bad-call",
                "billing_mode": "tiered_expr",
                "billing_expr": "os.system(1)",
            },
            {
                "model_name": "missing-expr",
                "billing_mode": "tiered_expr",
                "model_ratio": 1,
            },
            {"model_name": "good", "model_ratio": 1},
        ]
    }
    prices = ps.adapt_newapi(raw, "newapi:us")
    assert set(prices) == {"good"}  # 非法/缺失表达式均未进目录
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "source=newapi:us" in text and "model=bad-expr" in text
    assert "class=" in text


def test_sync_all_empty_response_keeps_last_valid_source_data(monkeypatch, tmp_path):
    """HTTP 200 但响应为空且此前有数据：报错并整体保留旧价格（拒绝漂移空响应）。"""
    litellm_url = ps.SOURCE_URLS["litellm"]
    monkeypatch.setattr(
        ps,
        "http_get",
        _mock_http({litellm_url: ({"m": {"input_cost_per_token": 1e-6}}, 200, {}, "")}),
    )
    cfg = {"price_sources": {"litellm": {"enabled": True}}}
    asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert load_catalog(str(tmp_path)).prices_for_source("litellm")

    # 上游返回 schema 可解析但内容为空 → 视为失效
    monkeypatch.setattr(ps, "http_get", _mock_http({litellm_url: ({}, 200, {}, "")}))
    out = asyncio.run(ps.sync_all(cfg, str(tmp_path), sources=["litellm"]))
    assert out["ok"] is False
    by = {r["source"]: r for r in out["results"]}
    assert by["litellm"]["status"] == "error"
    assert "保留旧数据" in by["litellm"]["error"]
    cat = load_catalog(str(tmp_path))
    assert cat.prices["litellm:m"].prompt == pytest.approx(1.0)  # 旧价格未被清空


def test_sync_source_rejects_first_empty_response_with_debug(caplog, monkeypatch):
    _enable_debug_caplog(caplog)
    url = ps.SOURCE_URLS["litellm"]
    monkeypatch.setattr(ps, "http_get", _mock_http({url: ({}, 200, {}, "")}))

    res = ps.sync_source("litellm")

    assert res.status == "error"
    assert res.prices == {}
    assert "empty_or_invalid_schema" in "\n".join(r.getMessage() for r in caplog.records)


def test_provider_token_resolves_env_and_skips_empty_entries(monkeypatch):
    monkeypatch.setenv("ASTRBOT_PRICE_KEY", "sk-from-env")
    assert (
        ps._provider_token({"key": ["", "$MISSING_KEY", "${ASTRBOT_PRICE_KEY}"]})
        == "sk-from-env"
    )
    assert ps._provider_token({"key": ["", "second-key"]}) == "second-key"


def test_sync_source_rejects_provider_key_for_different_origin(caplog, monkeypatch):
    _enable_debug_caplog(caplog)
    called = False

    def fake_http(url, headers, timeout):
        nonlocal called
        called = True
        return None, 0, {}, "unexpected"

    monkeypatch.setattr(ps, "http_get", fake_http)
    cfg = {
        "price_sources": {
            "newapi:edge": {
                "enabled": True,
                "base_url": "https://attacker.example/v1",
            }
        }
    }
    provider = {"api_base": "https://provider.example/v1", "key": ["sk-sensitive"]}

    res = ps.sync_source("newapi:edge", cfg=cfg, provider_cfg=lambda pid: provider)

    assert res.status == "error"
    assert called is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "credential_origin_mismatch" in text
    assert "sk-sensitive" not in text


def test_sync_source_rejects_different_origin_without_provider_key(caplog, monkeypatch):
    """绑定 provider 后即使关闭 key，也不能绕过 provider origin 校验。"""
    _enable_debug_caplog(caplog)
    called = False

    def fake_http(url, headers, timeout):
        nonlocal called
        called = True
        return None, 0, {}, "unexpected"

    monkeypatch.setattr(ps, "http_get", fake_http)
    cfg = {
        "price_sources": {
            "newapi:edge": {
                "enabled": True,
                "base_url": "https://attacker.example/v1",
                "use_provider_key": False,
            }
        }
    }
    provider = {"api_base": "https://provider.example/v1"}

    res = ps.sync_source("newapi:edge", cfg=cfg, provider_cfg=lambda pid: provider)

    assert res.status == "error"
    assert called is False
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "provider_origin_mismatch" in text


def test_provider_key_origin_normalizes_default_https_port(monkeypatch):
    captured = {}

    def fake_http(url, headers, timeout):
        captured["authorization"] = headers.get("Authorization")
        return b'{"data":[{"model_name":"m","model_ratio":1}]}', 200, {}, ""

    monkeypatch.setattr(ps, "http_get", fake_http)
    cfg = {
        "price_sources": {
            "newapi:edge": {"enabled": True, "base_url": "https://provider.example:443/v1"}
        }
    }
    provider = {"api_base": "https://provider.example/v1", "key": ["sk-safe"]}
    res = ps.sync_source("newapi:edge", cfg=cfg, provider_cfg=lambda pid: provider)
    assert res.status == "ok"
    assert captured["authorization"] == "Bearer sk-safe"


def test_credentialed_redirect_is_blocked():
    handler = ps._CredentialRedirectHandler()
    req = ps.urllib.request.Request(
        "https://provider.example/api/pricing",
        headers={"Authorization": "Bearer secret"},
    )
    with pytest.raises(ps.urllib.error.HTTPError, match="credentialed redirect blocked"):
        handler.redirect_request(
            req,
            io.BytesIO(),
            302,
            "Found",
            {},
            "https://other.example/pricing",
        )


def test_url_userinfo_redirect_is_blocked():
    handler = ps._CredentialRedirectHandler()
    req = ps.urllib.request.Request("https://user:secret@provider.example/api/pricing")
    with pytest.raises(ps.urllib.error.HTTPError, match="credentialed redirect blocked"):
        handler.redirect_request(
            req,
            io.BytesIO(),
            302,
            "Found",
            {},
            "https://other.example/pricing",
        )


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Content-Length": str(ps.MAX_RESPONSE_BYTES + 1)}, b"{}"),
        ({}, b"x" * (ps.MAX_RESPONSE_BYTES + 1)),
    ],
)
def test_http_get_rejects_oversized_response(caplog, monkeypatch, headers, body):
    _enable_debug_caplog(caplog)

    class FakeResponse:
        status = 200

        def __init__(self):
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return body[:limit]

    monkeypatch.setattr(ps.urllib.request, "urlopen", lambda req, timeout: FakeResponse())
    response_body, status, _, error = ps.http_get("https://source.example/catalog", {}, 1)
    assert response_body is None
    assert status == 200
    assert error == "响应体超过大小限制"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "origin=https://source.example" in text
    assert "exceeded" in text


def test_modelsdev_etag_preserves_quotes_and_is_reused(monkeypatch):
    url = ps.SOURCE_URLS["modelsdev"]
    payload = {"providers": {"p": {"models": {"m": {"cost": {"input": 1}}}}}}
    monkeypatch.setattr(ps, "http_get", _mock_http({url: (payload, 200, {"ETag": '"abc"'}, "")}))
    first = ps.sync_source("modelsdev")
    assert first.etag == '"abc"'

    captured = {}

    def not_modified(req_url, headers, timeout):
        captured["etag"] = headers.get("If-None-Match")
        return None, 304, {}, ""

    monkeypatch.setattr(ps, "http_get", not_modified)
    second = ps.sync_source("modelsdev", first.to_status(None))
    assert second.not_modified is True
    assert captured["etag"] == '"abc"'


def test_sync_all_serializes_same_catalog_and_preserves_both_updates(monkeypatch, tmp_path):
    active = 0
    max_active = 0
    guard = threading.Lock()

    def fake_sync(source, prev, **kwargs):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        price = ps.CatalogPrice(
            source=source,
            source_model_id=f"{source}-model",
            prompt=1,
            completion=2,
        )
        return ps.SourceResult(source=source, models=1, prices={price.source_model_id: price})

    monkeypatch.setattr(ps, "sync_source", fake_sync)
    cfg = {
        "price_sources": {
            "litellm": {"enabled": True},
            "openrouter": {"enabled": True},
        }
    }

    async def run_both():
        await asyncio.gather(
            ps.sync_all(cfg, str(tmp_path), sources=["litellm"]),
            ps.sync_all(cfg, str(tmp_path), sources=["openrouter"]),
        )

    asyncio.run(run_both())
    catalog = load_catalog(str(tmp_path))
    assert max_active == 1
    assert set(catalog.sources) == {"litellm", "openrouter"}
    assert set(catalog.prices) == {"litellm:litellm-model", "openrouter:openrouter-model"}
