"""``config`` 模块纯函数 + 插件配置文件 IO 单测。"""

import os

from cost_control.config import (
    coerce_to_default_type,
    deep_merge,
    get_enabled_price_sources,
    get_price_sources,
    get_pricing,
    load_plugin_config,
    mark_migration_done,
    migration_done,
    normalize_price_sources,
    save_plugin_config,
    switches_from_config,
)

# ===== deep_merge =====


def test_deep_merge_nested():
    assert deep_merge({"a": 1, "b": {"x": 1}}, {"b": {"y": 2}}) == {"a": 1, "b": {"x": 1, "y": 2}}


def test_deep_merge_scalar_override():
    assert deep_merge({"a": 1}, {"a": 5}) == {"a": 5}


def test_deep_merge_nondict_replaces():
    assert deep_merge({"a": {"b": 1}}, {"a": 9}) == {"a": 9}


def test_deep_merge_multi_source_order():
    # 后者覆盖前者
    assert deep_merge({}, {"a": 1}, {"a": 2}) == {"a": 2}
    assert deep_merge({"a": {"x": 1}}, {"a": {"y": 2}}, {"a": {"x": 3}}) == {"a": {"x": 3, "y": 2}}


def test_deep_merge_base_nondict():
    assert deep_merge(5, {"a": 1}) == {"a": 1}


# ===== coerce_to_default_type =====


def test_coerce_bool():
    assert coerce_to_default_type(1, True) is True
    assert coerce_to_default_type(0, True) is False


def test_coerce_int():
    assert coerce_to_default_type("5", 0) == 5
    assert coerce_to_default_type(-3, 10) == 0  # 负数归 0
    assert coerce_to_default_type("abc", 7) == 7  # 非法回退默认


def test_coerce_float():
    assert coerce_to_default_type("1.5", 0.0) == 1.5
    assert coerce_to_default_type("x", 2.0) == 2.0


def test_coerce_str():
    assert coerce_to_default_type(123, "") == "123"


def test_coerce_list():
    assert coerce_to_default_type([1, 2], []) == [1, 2]
    assert coerce_to_default_type("nope", []) == []


def test_coerce_dict_fixed_keys_missing_backfill():
    assert coerce_to_default_type({"a": 1}, {"a": 0, "b": 0}) == {"a": 1, "b": 0}


def test_coerce_dict_none():
    assert coerce_to_default_type(None, {"a": 0}) == {"a": 0}


def test_coerce_dict_empty_accepts_any():
    out = coerce_to_default_type({"gpt-4o": {"input": 2.5}}, {})
    assert out == {"gpt-4o": {"input": 2.5}}


# ===== switches_from_config =====


def test_switches_from_config_extracts_only_enabled():
    # schema 只保留总开关 enabled；其余键（即便存在）一律不抽取，
    # 它们的值由插件自有 config.json 承载。
    raw = {
        "enabled": False,
        "alerts": {"enabled": True, "cooldown_seconds": 99},
        "cache_diag": {"detect_context_reset": False, "cache_hit_rate_alert_threshold": 50},
        "budgets": {"global_daily": 1000},
    }
    sw = switches_from_config(raw)
    assert sw == {"enabled": False}


def test_switches_from_config_empty():
    assert switches_from_config({}) == {}
    assert switches_from_config(None) == {}


# ===== 插件配置文件 IO =====


def test_plugin_config_round_trip(tmp_path):
    d = str(tmp_path)
    cfg = {
        "budgets": {"global_daily": 1000},
        "pricing": {"gpt-4o": {"input": 2.5}},
        "alerts": {"enabled": True},
    }
    save_plugin_config(d, cfg)
    assert load_plugin_config(d) == cfg
    # 文件确实写出
    assert os.path.exists(os.path.join(d, "config.json"))


def test_plugin_config_missing_returns_empty(tmp_path):
    assert load_plugin_config(str(tmp_path)) == {}


def test_plugin_config_overwrite(tmp_path):
    d = str(tmp_path)
    save_plugin_config(d, {"a": 1})
    save_plugin_config(d, {"a": 2, "b": 3})
    assert load_plugin_config(d) == {"a": 2, "b": 3}


# ===== 一次性迁移标记 =====


def test_migration_done_default_false():
    assert migration_done({}, "fix_mislabeled_cost_currency") is False
    assert migration_done(None, "fix_mislabeled_cost_currency") is False
    assert migration_done({"migrations": "bad-type"}, "fix_mislabeled_cost_currency") is False


def test_mark_migration_done_sets_and_persists(tmp_path):
    d = str(tmp_path)
    cfg: dict = {"budgets": {"global_daily": 1000}}
    save_plugin_config(d, cfg)

    assert migration_done(cfg, "fix_mislabeled_cost_currency") is False
    mark_migration_done(cfg, "fix_mislabeled_cost_currency")
    assert migration_done(cfg, "fix_mislabeled_cost_currency") is True
    # 其它配置不受影响
    assert cfg["budgets"] == {"global_daily": 1000}

    save_plugin_config(d, cfg)
    reloaded = load_plugin_config(d)
    assert migration_done(reloaded, "fix_mislabeled_cost_currency") is True
    # 再跑一次标记幂等
    mark_migration_done(cfg, "fix_mislabeled_cost_currency")
    assert migration_done(cfg, "fix_mislabeled_cost_currency") is True


def test_mark_migration_done_creates_block_when_missing():
    cfg: dict = {}
    mark_migration_done(cfg, "m1")
    mark_migration_done(cfg, "m2")
    assert cfg["migrations"] == {"m1": True, "m2": True}


def test_price_sources_backfill_public_defaults_for_partial_config():
    """动态 New API 源不能覆盖三种公共源的默认配置。"""
    sources = get_price_sources({"price_sources": {"newapi:gateway": {"enabled": True}}})
    assert set(sources) == {"modelsdev", "litellm", "openrouter", "newapi:gateway"}
    assert get_enabled_price_sources({"price_sources": {"newapi:gateway": {"enabled": True}}}) == [
        "modelsdev",
        "litellm",
        "openrouter",
        "newapi:gateway",
    ]


def test_normalize_price_sources_rejects_unknown_source_ids():
    normalized = normalize_price_sources(
        {
            "modelsdev": {"enabled": False},
            "newapi:gateway": {"enabled": True, "provider_id": "gateway"},
            "custom-url": {"enabled": True},
            "newapi:": {"enabled": True},
        }
    )
    assert normalized["modelsdev"]["enabled"] is False
    assert normalized["newapi:gateway"]["provider_id"] == "gateway"
    assert "custom-url" not in normalized
    assert "newapi:" not in normalized


def test_get_pricing_includes_normalized_cluster_multipliers():
    pricing = get_pricing(
        {
            "pricing_multipliers": {
                "openai-main": "1.25",
                "default-one": 1,
                "free": 0,
                "invalid-negative": -0.5,
            }
        }
    )
    assert pricing["multipliers"] == {"openai-main": 1.25, "free": 0.0}
