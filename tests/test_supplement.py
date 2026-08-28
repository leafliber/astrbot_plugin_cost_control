"""``SupplementMixin`` 单测：验证 ``_extract_cache`` 对各 provider 的解析（纯函数）。"""

from types import SimpleNamespace

import pytest

from cost_control.supplement import (
    SupplementMixin,
    _extract_billing_context,
    _extract_cache,
    _extract_req_billing,
)


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


# ===== 计费上下文采集（billing_context，归一化到 params 嵌套形态） =====


def test_extract_billing_context_service_tier_from_raw_attr():

    raw = SimpleNamespace(service_tier="priority")
    ctx = _extract_billing_context(raw, {})
    assert ctx["params"]["service_tier"] == "priority"


def test_extract_billing_context_service_tier_from_raw_dict():

    ctx = _extract_billing_context({"service_tier": "flex"}, {})
    assert ctx["params"]["service_tier"] == "flex"


def test_extract_billing_context_response_overrides_request():

    raw = SimpleNamespace(service_tier="priority")
    req_ctx = _extract_req_billing(SimpleNamespace(extra_body={"service_tier": "default"}))
    ctx = _extract_billing_context(raw, req_ctx)
    assert ctx["params"]["service_tier"] == "priority"


def test_extract_billing_context_nests_whitelist_header_under_params():

    req_ctx = _extract_req_billing(SimpleNamespace(headers={"anthropic-beta": "prompt-caching-1h"}))
    ctx = _extract_billing_context(None, req_ctx)
    assert ctx["params"]["headers"] == {"anthropic-beta": "prompt-caching-1h"}
    assert ctx["params"]["cache_ttl_1h"] is True


def test_extract_billing_context_accepts_already_nested_request_context():
    ctx = _extract_billing_context(
        None,
        {"params": {"service_tier": "priority", "cache_ttl_1h": True}},
    )
    assert ctx["params"] == {"service_tier": "priority", "cache_ttl_1h": True}


def test_extract_billing_context_debug_logs_names_only(caplog):
    import logging as _logging

    from cost_control.supplement import _extract_req_billing

    caplog.set_level(_logging.DEBUG, logger="cost_control.supplement")
    # 请求侧带 Authorization：白名单过滤后只剩计费头，日志只见头名不见值。
    req_ctx = _extract_req_billing(
        SimpleNamespace(
            extra_body={"service_tier": "flex"},
            headers={"anthropic-beta": "prompt-caching-1h", "Authorization": "Bearer SECRET"},
        )
    )
    ctx = _extract_billing_context(None, req_ctx)
    assert "Authorization" not in str(ctx)
    assert "SECRET" not in str(ctx)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "anthropic-beta" in text  # 只记录头名
    assert "Bearer" not in text and "SECRET" not in text  # 绝不记录值


# ===== collect_response 集成：billing_context 贯通到成本计算 =====


class _CollectHost(SupplementMixin):
    """最小宿主：绕开真实 context/store，只驱动 collect_response 逻辑。"""

    def __init__(self, pricing: dict) -> None:
        self.context = SimpleNamespace(
            # 返回带 meta() 的 provider（id=prov, model=m），走真实定价匹配链路
            get_using_provider=lambda umo: SimpleNamespace(
                meta=lambda: SimpleNamespace(id="prov", model="m")
            )
        )
        self._pricing = pricing

    def get_pricing(self) -> dict:
        return self._pricing

    def _get_umo(self, event: object) -> str:  # noqa: D102 - 继承签名
        return "session-1"


def test_collect_response_service_tier_flows_into_per_tier_cost():
    import asyncio

    user = {
        "prov": {
            "mode": "per_tier",
            "base": {"input": 1.0, "output": 2.0},
            "service_tiers": [{"match": "priority", "input_multiplier": 2.0}],
        }
    }
    host = _CollectHost({"user": user})
    resp = SimpleNamespace(
        id="r1",
        usage=SimpleNamespace(input_other=1_000_000, input_cached=0, output=0),
        raw_completion=SimpleNamespace(service_tier="priority"),
    )
    rec = asyncio.run(host.collect_response(SimpleNamespace(unified_msg_origin="u"), resp))
    # params 嵌套形态写入记录，供 tiered_expr 的 param() / per_tier 读取
    assert rec["billing_context"]["params"]["service_tier"] == "priority"
    # priority 倍率 2.0 生效：1M × 2.0 = $2
    assert rec["cost_amount"] == pytest.approx(2.0)


def test_collect_response_service_tier_flows_into_tiered_expr_param():
    import asyncio

    user = {
        "prov": {
            "mode": "tiered_expr",
            "expr": '(tier("std", p * (param("service_tier") == "fast" ? 4 : 2)))',
        }
    }
    host = _CollectHost({"user": user})
    resp = SimpleNamespace(
        id="r2",
        usage=SimpleNamespace(input_other=100_000, input_cached=0, output=0),
        raw_completion=None,
    )
    event = SimpleNamespace(unified_msg_origin="u", _cost_control_billing_req={})
    # 模拟请求侧 fast tier（capture_req_billing 正常链路）
    host.capture_req_billing(event, SimpleNamespace(extra_body={"service_tier": "fast"}))
    rec = asyncio.run(host.collect_response(event, resp))
    assert rec["billing_context"]["params"]["service_tier"] == "fast"
    # p=100000 × 4（fast） / 1M = $0.4，param() 必须读到嵌套后的 service_tier
    assert rec["cost_amount"] == pytest.approx(0.4)


def test_collect_response_expr_failure_records_class_not_silent(caplog):
    import asyncio
    import logging as _logging

    user = {"prov": {"mode": "tiered_expr", "expr": "p * * c"}}  # 语法错误
    host = _CollectHost({"user": user})
    resp = SimpleNamespace(
        id="r3",
        usage=SimpleNamespace(input_other=10, input_cached=0, output=0),
        raw_completion=None,
    )
    # 失败降级路径要求 debug 级可定位日志（不含凭据/请求体）。
    caplog.set_level(_logging.DEBUG, logger="cost_control.cost")
    rec = asyncio.run(host.collect_response(SimpleNamespace(unified_msg_origin="u"), resp))
    assert rec["cost_amount"] is None  # 失败成本保留 NULL，禁止固化为假零值
    ctx = rec.get("billing_context") or {}
    assert ((ctx.get("params") or {}).get("expr_error")) == "SyntaxError"
    cost_records = [r for r in caplog.records if r.name == "cost_control.cost"]
    assert cost_records, "失败路径必须发出 cost_control.cost 日志"
    # 安全诊断必须停留在 DEBUG 级（不得升级为 WARNING/ERROR 打断计费主链路）。
    assert all(r.levelname == "DEBUG" for r in cost_records)
    text = "\n".join(r.getMessage() for r in cost_records)
    assert "class=SyntaxError" in text  # debug 日志含失败类别
    assert "p * * c" not in text  # 绝不记录表达式原文


def test_collect_response_missing_expr_sets_explicit_marker(caplog):
    """规则缺 expr 的降级路径同样要有显式 _expr_error/billing_context 标记与 debug 日志。"""
    import asyncio
    import logging as _logging

    host = _CollectHost({"user": {"prov": {"mode": "tiered_expr"}}})
    resp = SimpleNamespace(
        id="r5",
        usage=SimpleNamespace(input_other=7, input_cached=0, output=0),
        raw_completion=None,
    )
    caplog.set_level(_logging.DEBUG, logger="cost_control.cost")
    rec = asyncio.run(host.collect_response(SimpleNamespace(unified_msg_origin="u"), resp))
    assert rec["cost_amount"] is None
    ctx = rec.get("billing_context") or {}
    assert ((ctx.get("params") or {}).get("expr_error")) == "missing_expr"
    cost_records = [r for r in caplog.records if r.name == "cost_control.cost"]
    assert any("class=missing_expr" in r.getMessage() for r in cost_records)
    assert all(r.levelname == "DEBUG" for r in cost_records)


def test_collect_response_success_debug_logs_stay_safe(caplog):
    """求值成功路径的变量类别诊断必须是 DEBUG 且绝不泄漏表达式/用量数值。"""
    import asyncio
    import logging as _logging

    user = {"prov": {"mode": "tiered_expr", "expr": 'tier("std", p * 2)'}}
    host = _CollectHost({"user": user})
    resp = SimpleNamespace(
        id="r6",
        usage=SimpleNamespace(input_other=123_456, input_cached=2_000, output=999),
        raw_completion=None,
    )
    caplog.set_level(_logging.DEBUG, logger="cost_control.cost")
    rec = asyncio.run(host.collect_response(SimpleNamespace(unified_msg_origin="u"), resp))
    assert rec["cost_amount"] > 0
    cost_records = [r for r in caplog.records if r.name == "cost_control.cost"]
    assert any("tiered_expr 归一化变量" in r.getMessage() for r in cost_records)
    assert all(r.levelname == "DEBUG" for r in cost_records)
    text = "\n".join(r.getMessage() for r in cost_records)
    assert 'tier("std", p * 2)' not in text  # 表达式原文只出现在长度计数里
    assert "123456" not in text and "123_456" not in text  # 用量数值绝不入日志


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


def test_collect_response_created_datetime_reaches_time_funcs(monkeypatch):
    """collect_response 固化的 created_at 是 datetime；时间函数（IANA 时区）必须能用。

    PR#10 #4：时间函数只认 epoch 导致时段倍率全部回退当前时刻——这里用固定时刻的
    month() 分支证明 datetime 被真正消费。
    """
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace as NS

    import cost_control.supplement as supplement_mod

    fixed = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)  # 上海 2023-11-15 06:13
    monkeypatch.setattr(supplement_mod, "datetime", NS(now=lambda tz=None: fixed))

    user = {
        "prov": {
            "mode": "tiered_expr",
            # 用可求值分支探测 IANA 时区下的日期：11 月 ×3，否则 ×1
            "expr": (
                'tier("t", p * (month("Asia/Shanghai") == 11 '
                '? day("Asia/Shanghai") == 15 ? 3 : 1 : 1))'
            ),
        }
    }
    host = _CollectHost({"user": user})
    resp = NS(
        id="r4",
        usage=NS(input_other=100_000, input_cached=0, output=0),
        raw_completion=None,
    )
    rec = asyncio.run(host.collect_response(NS(unified_msg_origin="u"), resp))
    # p=100k × 3 → $0.3；若 datetime 未被消费则回退 now()（非 2023-11）得 ×1
    assert rec["cost_amount"] == pytest.approx(0.3)
