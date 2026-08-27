"""``SupplementMixin`` 单测：验证 ``_extract_cache`` 对各 provider 的解析（纯函数）。"""

from types import SimpleNamespace

from cost_control.supplement import _extract_cache


def test_extract_cache_none_raw():
    cc, cr, raw = _extract_cache(None)
    assert cc is None
    assert cr is None
    assert raw is None


def test_extract_cache_anthropic():
    usage = SimpleNamespace(
        cache_creation_input_tokens=100,
        cache_read_input_tokens=200,
        input_tokens=1000,
        output_tokens=500,
    )
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cc == 100
    assert cr == 200


def test_extract_cache_openai_prompt_tokens_details():
    ptd = SimpleNamespace(cached_tokens=150)
    usage = SimpleNamespace(prompt_tokens_details=ptd, prompt_tokens=1000)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cc is None
    assert cr == 150


def test_extract_cache_openai_responses_input_tokens_details():
    # openai_responses 供应商的 raw_completion.usage 是 ResponseUsage：
    # input_tokens / output_tokens / input_tokens_details.cached_tokens
    itd = SimpleNamespace(cached_tokens=150)
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=500,
        input_tokens_details=itd,
        output_tokens_details=SimpleNamespace(reasoning_tokens=99),
    )
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cc is None
    assert cr == 150


def test_extract_cache_deepseek_extension_fields():
    usage = SimpleNamespace(prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cr == 80
    assert cc == 20


def test_extract_cache_google_usage_metadata():
    um = SimpleNamespace(cached_content_token_count=300)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage_metadata=um))
    assert cc is None
    assert cr == 300


def test_extract_cache_no_usage():
    cc, cr, raw = _extract_cache(SimpleNamespace())
    assert cc is None
    assert cr is None
    assert raw is None


# ===== request_id 采集（ensure_request_id 生成 + _read_request_id 读回）=====


def test_ensure_request_id_generates_once():
    from cost_control.supplement import SupplementMixin

    # ensure_request_id 不引用 self，传任意实例作 self 即可
    event = SimpleNamespace()
    SupplementMixin.ensure_request_id(object(), event)  # type: ignore[arg-type]
    rid1 = getattr(event, "_cost_control_request_id", None)
    assert rid1 and rid1.startswith("cc_")
    # 第二次调用幂等：不覆盖已有值
    SupplementMixin.ensure_request_id(object(), event)  # type: ignore[arg-type]
    assert getattr(event, "_cost_control_request_id", None) == rid1


def test_read_request_id_reads_back():
    from cost_control.supplement import _read_request_id

    event = SimpleNamespace(_cost_control_request_id="cc_abc123")
    assert _read_request_id(event) == "cc_abc123"


def test_read_request_id_none_when_absent():
    from cost_control.supplement import _read_request_id

    assert _read_request_id(SimpleNamespace()) is None


# ===== 计费上下文采集（billing_context） =====


def test_extract_billing_context_service_tier_from_raw_attr():
    from cost_control.supplement import _extract_billing_context

    raw = SimpleNamespace(service_tier="priority")
    ctx = _extract_billing_context(raw, {})
    assert ctx["service_tier"] == "priority"


def test_extract_billing_context_service_tier_from_raw_dict():
    from cost_control.supplement import _extract_billing_context

    ctx = _extract_billing_context({"service_tier": "flex"}, {})
    assert ctx["service_tier"] == "flex"


def test_extract_billing_context_response_overrides_request():
    from cost_control.supplement import _extract_billing_context

    raw = SimpleNamespace(service_tier="priority")
    ctx = _extract_billing_context(raw, {"service_tier": "default"})
    assert ctx["service_tier"] == "priority"


def test_extract_req_billing_from_extra_body():
    from cost_control.supplement import _extract_req_billing

    req = SimpleNamespace(extra_body={"service_tier": "fast"})
    assert _extract_req_billing(req)["service_tier"] == "fast"


def test_extract_req_billing_anthropic_beta_1h():
    from cost_control.supplement import _extract_req_billing

    req = SimpleNamespace(extra_body={}, headers={"anthropic-beta": "prompt-caching-1h"})
    ctx = _extract_req_billing(req)
    assert ctx.get("cache_ttl_1h") is True
    assert ctx["headers"]["anthropic-beta"] == "prompt-caching-1h"


def test_extract_req_billing_never_captures_authorization():
    from cost_control.supplement import _extract_req_billing

    req = SimpleNamespace(
        extra_body={},
        headers={"anthropic-beta": "x", "Authorization": "Bearer SECRET"},
    )
    ctx = _extract_req_billing(req)
    assert "Authorization" not in str(ctx)
    assert "SECRET" not in str(ctx)


def test_extract_req_billing_none_and_garbage():
    from cost_control.supplement import _extract_req_billing

    assert _extract_req_billing(None) == {}
    # headers 非 dict、extra_body 缺失等异常形状都吞掉返回 {}
    assert _extract_req_billing(SimpleNamespace(extra_body="notdict", headers=42)) == {}


def test_read_req_billing_roundtrip():
    from cost_control.supplement import SupplementMixin, _read_req_billing

    mixin = SupplementMixin()
    event = SimpleNamespace()
    req = SimpleNamespace(extra_body={"service_tier": "fast"})
    mixin.capture_req_billing(event, req)
    assert _read_req_billing(event)["service_tier"] == "fast"


def test_capture_req_billing_empty_not_stashed():
    from cost_control.supplement import SupplementMixin, _read_req_billing

    mixin = SupplementMixin()
    event = SimpleNamespace()
    mixin.capture_req_billing(event, SimpleNamespace())  # 无可提取内容
    assert _read_req_billing(event) == {}
