"""``AttributorMixin`` 纯函数 + 快照 / 注入差单测。"""

from cost_control.attributor import (
    AUDIO_TOKEN_EST,
    IMAGE_TOKEN_EST,
    AttributorMixin,
    _content_part_tokens,
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
        image_urls: list | None = None,
        audio_urls: list | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.contexts = contexts if contexts is not None else []
        self.prompt = prompt
        self.func_tool = func_tool
        self.extra_user_content_parts = extra if extra is not None else []
        self.image_urls = image_urls if image_urls is not None else []
        self.audio_urls = audio_urls if audio_urls is not None else []


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
    # total 现为五维之和（system + tools + history + user + extra）
    assert snap["total"] == (
        snap["system"] + snap["tools"] + snap["history"] + snap["user"] + snap["extra"]
    )


def test_snapshot_context_splits_user_and_extra():
    """user 仅含 prompt（+ 媒体块），extra 单独估算 extra_user_content_parts。"""
    m = _mixin()

    class _TextPart:
        type = "text"
        text = "额外指令"

    class _ImgPart:
        type = "image_url"

    req = _FakeReq(prompt="你好", extra=[_TextPart(), _ImgPart()])
    snap = m.snapshot_context(req)
    assert snap["user"] == _str_tokens("你好")
    assert snap["extra"] == _str_tokens("额外指令") + IMAGE_TOKEN_EST


def test_snapshot_context_user_includes_media():
    """user 维度含 image_urls / audio_urls 媒体块估值。"""
    m = _mixin()
    req = _FakeReq(prompt="看图", image_urls=["a", "b"], audio_urls=["c"])
    snap = m.snapshot_context(req)
    assert snap["user"] == _str_tokens("看图") + 2 * IMAGE_TOKEN_EST + AUDIO_TOKEN_EST


def test_content_part_tokens_non_text():
    """_content_part_tokens 覆盖所有块类型，含非文本。"""

    class _Text:
        type = "text"
        text = "hi"

    class _Think:
        type = "think"
        think = "思考"

    class _Img:
        type = "image_url"

    class _Audio:
        type = "audio_url"

    assert _content_part_tokens(_Text()) == _str_tokens("hi")
    assert _content_part_tokens(_Think()) == _str_tokens("思考")
    assert _content_part_tokens(_Img()) == IMAGE_TOKEN_EST
    assert _content_part_tokens(_Audio()) == AUDIO_TOKEN_EST
    # dict 回退到 _content_tokens
    assert _content_part_tokens({"type": "text", "text": "x"}) == _str_tokens("x")


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


def test_injection_diff_detects_extra_growth():
    """extra 维度的插件注入也应被注入差捕获。"""
    m = _mixin()
    req = _FakeReq(system_prompt="A", prompt="hi")
    m.record_initial_context(req)

    class _TextPart:
        type = "text"
        text = "插件注入的额外指令"

    req.extra_user_content_parts.append(_TextPart())
    result = m.pop_injection(req, umo="u3")
    assert result is not None
    assert result["injected"]["extra"] > 0
    assert result["injected_total"] == sum(result["injected"].values())


def test_pop_injection_without_initial_returns_none():
    m = _mixin()
    req = _FakeReq(system_prompt="A")
    assert m.pop_injection(req, umo="u2") is None
