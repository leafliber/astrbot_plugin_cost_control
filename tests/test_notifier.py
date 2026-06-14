"""``NotifierMixin`` 纯函数单测：冷却判定。"""

from cost_control.notifier import cooldown_elapsed


def test_cooldown_disabled_when_zero():
    assert cooldown_elapsed(None, 100.0, 0) is True


def test_cooldown_never_sent_allows():
    assert cooldown_elapsed(None, 100.0, 300) is True


def test_cooldown_within_window_blocks():
    assert cooldown_elapsed(100.0, 200.0, 300) is False


def test_cooldown_passed_allows():
    assert cooldown_elapsed(100.0, 500.0, 300) is True


def test_cooldown_exact_boundary_allows():
    # >= 阈值即放行
    assert cooldown_elapsed(100.0, 400.0, 300) is True
