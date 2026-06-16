"""``config`` 模块纯函数 + 插件配置文件 IO 单测。"""

import os

from cost_control.config import (
    coerce_to_default_type,
    deep_merge,
    load_plugin_config,
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


def test_switches_from_config_extracts_only_switches():
    raw = {
        "enabled": False,
        "alerts": {"enabled": True, "cooldown_seconds": 99},  # cooldown 非 switch，不抽
        "cache_diag": {"detect_context_reset": False, "cache_hit_rate_alert_threshold": 50},
        "budgets": {"global_daily": 1000},  # 非 switch，整体不抽
    }
    sw = switches_from_config(raw)
    assert sw == {
        "enabled": False,
        "alerts": {"enabled": True},
        "cache_diag": {"detect_context_reset": False},
    }


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
