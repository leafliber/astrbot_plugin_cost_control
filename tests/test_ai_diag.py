"""``AiDiagMixin`` 单测：默认诊断 Provider 选择的回退路径（纯函数级 mock）。"""

from types import SimpleNamespace

from astrbot.core.provider.entities import ProviderType

from cost_control.ai_diag import AiDiagMixin


def _make_mixin(provider_insts: list, using=None) -> AiDiagMixin:
    """构造仅含 ``context.provider_manager`` 的 AiDiagMixin 实例。"""
    m = AiDiagMixin()
    m.context = SimpleNamespace(
        provider_manager=SimpleNamespace(
            provider_insts=provider_insts,
            get_using_provider=lambda **kwargs: using,
        )
    )
    return m


def _provider(pid: str, adapter: str, ptype: ProviderType) -> SimpleNamespace:
    return SimpleNamespace(meta=lambda: SimpleNamespace(id=pid, type=adapter, provider_type=ptype))


def test_default_provider_id_prefers_using_provider():
    m = _make_mixin(
        [_provider("p1", "openai_chat_completion", ProviderType.CHAT_COMPLETION)],
        using=_provider("using", "zhipu_chat_completion", ProviderType.CHAT_COMPLETION),
    )
    assert m._get_default_provider_id() == "using"


def test_default_provider_id_falls_back_to_first_chat_provider():
    # meta.type 是 adapter 名（openai_responses 等），永远不等于 "chat_completion"；
    # 回退须按 provider_type 枚举命中 chat provider 并跳过 TTS/STT 等。
    m = _make_mixin(
        [
            _provider("tts", "azure_tts", ProviderType.TEXT_TO_SPEECH),
            _provider("stt", "openai_whisper_api", ProviderType.SPEECH_TO_TEXT),
            _provider("resp", "openai_responses", ProviderType.CHAT_COMPLETION),
            _provider("chat", "openai_chat_completion", ProviderType.CHAT_COMPLETION),
        ],
        using=None,
    )
    assert m._get_default_provider_id() == "resp"


def test_default_provider_id_none_without_chat_provider():
    m = _make_mixin(
        [_provider("tts", "azure_tts", ProviderType.TEXT_TO_SPEECH)],
        using=None,
    )
    assert m._get_default_provider_id() is None


async def test_collect_diag_data_budgets_report_real_usage_and_cost(monkeypatch):
    """预算维度必须上报真实 token/花费用量与货币口径，而非恒 0。"""
    from datetime import UTC, datetime

    from cost_control.ai_diag import AiDiagMixin

    m = AiDiagMixin()
    m.cfg = {
        "budgets": {"global_daily": 1000, "per_session_daily": 500, "per_model_daily": 100},
        "budgets_cost": {"global_daily": 2.0},
        "refresh_time": "00:00",
    }
    m.context = SimpleNamespace(
        get_config=lambda: {"provider": []},
    )
    m.get_budgets = lambda: {"global_daily": 1000, "per_session_daily": 500, "per_model_daily": 100}
    m.get_budgets_cost = lambda: {"global_daily": 2.0}
    m.get_pricing = lambda: {}

    async def query_usage(*, start=None, **kw):
        # 日窗口 100 token，月窗口 300 token（按 start 先后区分）。
        return {"token_input_other": 100 if start.day >= 28 else 300, "count": 1}

    async def query_usage_grouped(*, by=None, start=None, **kw):
        if by == "umo":
            return [{"token_input_other": 50, "token_input_cached": 10, "token_output": 5}]
        if by == "model":
            return [{"token_input_other": 20, "token_input_cached": 0, "token_output": 0}]
        return []

    async def query_user_token_totals(start, **kw):
        return [("u1", 70)]

    async def query_usage_cost_rows(pricing, **kw):
        return []

    async def query_cache_events(**kw):
        return []

    async def query_supplements(**kw):
        return []

    async def query_records(**kw):
        return []

    async def query_usage_records(**kw):
        return []

    m.query_usage = query_usage
    m.query_usage_grouped = query_usage_grouped
    m.query_user_token_totals = query_user_token_totals
    m.query_usage_cost_rows = query_usage_cost_rows
    m.query_cache_events = query_cache_events
    m.query_supplements = query_supplements

    # overview 分支依赖的聚合接口（本测试只关心 budgets）。
    async def build_report(**kw):
        return {}

    m.build_report = build_report

    data = await m._collect_diag_data()
    dims = {d["dimension"]: d for d in data["budgets"]["dimensions"]}
    assert dims["每日全局"]["token_used"] > 0
    assert dims["每日全局"]["cost_used"] == 0  # 无定价数据时花费为 0 而非缺失
    assert dims["每日全局"]["cost_limit"] == 2.0
    assert "currency" in dims["每日全局"]
    # 局部维度取真实最大主体用量（50+10+5=65），不再恒 0。
    assert dims["每会话·每日"]["token_used"] == 65
    assert dims["每模型·每日"]["token_used"] == 20
    assert "每日全局" in data["budgets"]["currency"] or data["budgets"]["currency"]
