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
