"""``AttributorMixin`` 纯函数 + 快照 / 注入差单测。"""

from cost_control.attributor import (
    AttributorMixin,
    _content_tokens,
    _str_tokens,
    estimate_tokens,
)


class _FakeReq:
    """duck-typed ProviderRequest（仅供 snapshot_context 的 getattr 访问）。"""

    def __init__(
        self,
        system_prompt: str = "",
        contexts: list | None = None,
        prompt: str = "",
        func_tool=None,
        extra: list | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.contexts = contexts if contexts is not None else []
        self.prompt = prompt
        self.func_tool = func_tool
        self.extra_user_content_parts = extra if extra is not None else []


def test_str_tokens_ascii():
    assert _str_tokens("hello world!") == max(1, int(12 * 0.25))


def test_str_tokens_cjk():
    assert _str_tokens("你好世界测试") == max(1, int(6 * 0.6))


def test_str_tokens_empty():
    assert _str_tokens("") == 0


def test_content_tokens_str():
    assert _content_tokens("abc") == _str_tokens("abc")


def test_content_tokens_multimodal():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "input_audio", "input_audio": {"data": "y"}},
    ]
    assert _content_tokens(content) == _str_tokens("hello") + 85 + 200


def test_estimate_tokens_messages():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    assert estimate_tokens(msgs) == _str_tokens("hi") + _str_tokens("hello there")


def _mixin() -> AttributorMixin:
    m = AttributorMixin.__new__(AttributorMixin)
    return m


def test_snapshot_context_totals():
    m = _mixin()
    req = _FakeReq(
        system_prompt="你是助手",
        contexts=[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "在的"}],
        prompt="现在呢",
    )
    snap = m.snapshot_context(req)
    assert snap["system"] > 0
    assert snap["history"] > 0
    assert snap["total"] == snap["system"] + snap["tools"] + snap["history"]


def test_injection_diff_detects_history_growth():
    m = _mixin()
    req = _FakeReq(system_prompt="A", contexts=[{"role": "user", "content": "hi"}])
    m.record_initial_context(req)
    # 模拟其它插件 / 多轮追加历史
    req.contexts.extend(
        [
            {"role": "assistant", "content": "历史追加" * 20},
            {"role": "user", "content": "继续" * 10},
        ]
    )
    result = m.pop_injection(req, umo="u1")
    assert result is not None
    assert result["injected"]["history"] > 0
    assert result["injected_total"] == sum(result["injected"].values())
    assert m.last_system_prompt("u1") == "A"


def test_pop_injection_without_initial_returns_none():
    m = _mixin()
    req = _FakeReq(system_prompt="A")
    assert m.pop_injection(req, umo="u2") is None
