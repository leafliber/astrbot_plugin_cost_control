"""expr_eval 测试：New API 官方测试向量对齐 + 安全拒绝 + 语法转换。"""

from __future__ import annotations

from datetime import UTC

import pytest

from cost_control import expr_eval as ee

# ---- 语法转换 ----


def test_expr_to_python_operators():
    assert ee.expr_to_python("a && b") == "a  and  b"
    assert ee.expr_to_python("a || b") == "a  or  b"
    assert ee.expr_to_python("!a") == "not a"  # 前导空格被 strip（语义不变）
    assert ee.expr_to_python("a != b") == "a != b"  # != 不动
    assert ee.expr_to_python("true && false") == "True  and  False"


def test_expr_to_python_ternary():
    assert ee.expr_to_python("a ? b : c") == "(b if a else c)"


def test_expr_to_python_ternary_right_assoc():
    # a ? b : c ? d : e  →  a ? b : (c ? d : e)
    out = ee.expr_to_python("a ? b : c ? d : e")
    assert out == "(b if a else (d if c else e))"


def test_expr_to_python_ternary_nested_true_branch():
    out = ee.expr_to_python("a ? (b ? c : d) : e")
    assert out == "(((c if b else d)) if a else e)"  # 内层多一层无害括号，语义等价


def test_expr_to_python_protects_strings():
    # 字符串内的 && 和 ? 不应被转换。
    out = ee.expr_to_python('has(model, "a&&b?c") ? 1 : 2')
    assert '"a&&b?c"' in out
    assert " and " not in out.split('"a&&b?c"')[1].split("?")[0]  # 字符串外才转


def test_expr_to_python_empty():
    with pytest.raises(ValueError):
        ee.expr_to_python("   ")


# ---- New API 官方测试向量（RunExpr 原始输出，未 /1M）----

CLAUDE = (
    'p <= 200000 ? tier("standard", p * 1.5 + c * 7.5) : tier("long_context", p * 3.0 + c * 11.25)'
)


def test_newapi_claude_standard_tier():
    val, tier = ee.eval_tiered_expr(CLAUDE, {"p": 100000, "c": 5000})
    assert val == pytest.approx(100000 * 1.5 + 5000 * 7.5)  # 187500
    assert tier == "standard"


def test_newapi_claude_long_context_tier():
    val, tier = ee.eval_tiered_expr(CLAUDE, {"p": 250000, "c": 5000})
    assert val == pytest.approx(250000 * 3.0 + 5000 * 11.25)  # 806250
    assert tier == "long_context"


def test_newapi_glm_multi_tier():
    expr = (
        'p < 32000 && c < 200 ? tier("tier1", (p*2 + c*8)) : '
        'p < 32000 && c >= 200 ? tier("tier2", (p*3 + c*14)) : '
        'tier("tier3", (p*4 + c*16))'
    )
    val, tier = ee.eval_tiered_expr(expr, {"p": 15000, "c": 100})
    assert val == pytest.approx(15000 * 2 + 100 * 8)  # 30800
    assert tier == "tier1"
    val2, tier2 = ee.eval_tiered_expr(expr, {"p": 15000, "c": 500})
    assert val2 == pytest.approx(15000 * 3 + 500 * 14)
    assert tier2 == "tier2"


def test_newapi_cache_split():
    expr = 'tier("default", p*1.5 + c*7.5 + cr*0.15 + cc*2.0 + cc1h*3.0)'
    val, tier = ee.eval_tiered_expr(expr, {"p": 100, "c": 10, "cr": 5, "cc": 2, "cc1h": 1})
    assert val == pytest.approx(150 + 75 + 0.75 + 4 + 3)  # 232.75
    assert tier == "default"


def test_service_tier_multiplier():
    expr = '(tier("base", p * 2)) * (param("service_tier") == "fast" ? 2 : 1)'
    fast, _ = ee.eval_tiered_expr(expr, {"p": 1000}, {"params": {"service_tier": "fast"}})
    assert fast == pytest.approx(4000)
    default, _ = ee.eval_tiered_expr(expr, {"p": 1000}, {"params": {"service_tier": "default"}})
    assert default == pytest.approx(2000)


def test_tier_backfills_matched_tier():
    _, tier = ee.eval_tiered_expr('tier("mytier", 42)', {"p": 1})
    assert tier == "mytier"


def test_no_tier_returns_none():
    _, tier = ee.eval_tiered_expr("p * 2", {"p": 5})
    assert tier is None


# ---- 上下文降级 ----


def test_param_missing_returns_none():
    expr = 'param("service_tier") == "fast" ? 10 : 1'
    val, _ = ee.eval_tiered_expr(expr, {"p": 1}, {})
    assert val == pytest.approx(1)


def test_header_missing_returns_empty():
    expr = 'has(header("anthropic-beta"), "1h") ? 10 : 1'
    val, _ = ee.eval_tiered_expr(expr, {"p": 1}, {})
    assert val == pytest.approx(1)


def test_param_dotted_path():
    expr = 'param("a.b")'
    val, _ = ee.eval_tiered_expr(expr, {"p": 0}, {"params": {"a": {"b": 7}}})
    assert val == pytest.approx(7)


def test_time_funcs_use_created_at():
    # created_at = 1700000000 → 2023-11-14 UTC
    ctx = {"created_at": 1_700_000_000.0}
    month, _ = ee.eval_tiered_expr("month()", {"p": 0}, ctx)
    assert month == 11
    year_day, _ = ee.eval_tiered_expr("day()", {"p": 0}, ctx)
    assert year_day == 14


# ---- 安全拒绝 ----


@pytest.mark.parametrize(
    "bad",
    [
        'os.system("echo hi")',
        "().__class__",
        "lambda: 1",
        '__import__("os")',
        "[x for x in y]",
        "open('/etc/passwd')",
        "p.__class__",
        "eval('1')",
        "1" + "+1" * 2000,  # 节点数超限
    ],
)
def test_security_rejected(bad):
    err = ee.validate_tiered_expr(bad)
    assert err is not None, f"应被拒绝：{bad}"


def test_eval_rejects_attribute():
    with pytest.raises(ValueError):
        ee.eval_tiered_expr("p.real", {"p": 1})


# ---- 验证器 ----


def test_validate_ok():
    assert ee.validate_tiered_expr(CLAUDE) is None


def test_validate_negative_rejected():
    assert ee.validate_tiered_expr("0 - p") is not None


def test_validate_syntax_error():
    assert ee.validate_tiered_expr("p * * c") is not None


def test_validate_div_zero_rejected():
    # p/0 在 p=0 向量时 ZeroDivisionError → 求值失败
    assert ee.validate_tiered_expr("p / 0") is not None


def test_validate_too_long():
    assert ee.validate_tiered_expr("p" + "+1" * 3000) is not None


# ---- 编译缓存 ----


def test_compile_cache_hit():
    ee._COMPILE_CACHE.clear()
    code1 = ee._compile("p * 2")
    size_after_first = len(ee._COMPILE_CACHE)
    code2 = ee._compile("p * 2")
    assert code1 is code2
    assert len(ee._COMPILE_CACHE) == size_after_first  # 未新增


# ---- v1: 版本前缀（New API 上游兼容）----


def test_v1_prefix_evaluates_same_as_plain_body():
    """上游 billing_expr 可能带 v1: 前缀；剥离后必须与无前缀主体等值。"""
    plain_val, plain_tier = ee.eval_tiered_expr(CLAUDE, {"p": 250000, "c": 5000})
    v1_val, v1_tier = ee.eval_tiered_expr("v1:" + CLAUDE, {"p": 250000, "c": 5000})
    assert v1_val == pytest.approx(plain_val)
    assert v1_tier == plain_tier == "long_context"


def test_v1_prefix_accepted_by_validator_and_shared_cache():
    assert ee.validate_tiered_expr("v1:" + CLAUDE) is None
    # 同一主体共享编译缓存：带/不带前缀编译产物一致
    assert ee._compile(CLAUDE) is ee._compile("v1:" + CLAUDE)


def test_v1_bare_prefix_rejected():
    # 只有前缀没有主体 → 表达式为空，校验必须拒绝
    assert ee.validate_tiered_expr("v1:") is not None


def test_referenced_variables_extracts_token_dimensions():
    expr = 'tier("t", p * 2 + cr + cc1h)'
    assert ee.referenced_variables(expr) >= {"p", "cr", "cc1h"}
    # 前缀透明 + 非法表达式安全返回空集
    assert ee.referenced_variables("v1:" + expr) >= {"p", "cr", "cc1h"}
    assert ee.referenced_variables("p * * c") == frozenset()


# ---- 变量读取兼容与时间上下文 ----


def _param_branch(ctx: dict, *, fast_value=3, legacy_value=2) -> float:
    """用可求值的分支表达式探测 param() 命中的 service_tier 形态。"""
    fast = str(fast_value)
    legacy = str(legacy_value)
    expr = (
        'param("service_tier") == "fast" ? '
        f'{fast} : param("service_tier") == "legacy" ? {legacy} : 0'
    )
    val, _ = ee.eval_tiered_expr(expr, {"p": 0}, ctx)
    return val


def test_param_reads_flat_top_level_for_legacy_records():
    """历史 billing_context 把 service_tier 存在顶层；param() 必须能读到。"""
    val = _param_branch({"service_tier": "legacy"})
    assert val == pytest.approx(2)


def test_param_prefers_nested_params_over_flat():
    # 新旧形态同时存在时优先 params 嵌套（当前写入格式）
    ctx = {"params": {"service_tier": "fast"}, "service_tier": "legacy"}
    assert _param_branch(ctx) == pytest.approx(3)


def test_len_variable_no_builtin_clash():
    # len 是变量不是内置函数，可参与运算
    val, _ = ee.eval_tiered_expr("len * 2", {"len": 100, "p": 0})
    assert val == pytest.approx(200)


# ---- 时间上下文：datetime created_at 与 IANA 时区 ----

# 2023-11-14 22:13:20 UTC；上海为次日 06:13，与 UTC 小时/日期均不同。
_EPOCH_SHANGHAI = 1_700_000_000.0


def test_time_funcs_accept_epoch_with_iana_zoneinfo():
    ctx = {"created_at": _EPOCH_SHANGHAI}
    hour_utc, _ = ee.eval_tiered_expr("hour()", {"p": 0}, ctx)
    hour_sh, _ = ee.eval_tiered_expr('hour("Asia/Shanghai")', {"p": 0}, ctx)
    day_sh, _ = ee.eval_tiered_expr('day("Asia/Shanghai")', {"p": 0}, ctx)
    day_utc, _ = ee.eval_tiered_expr("day()", {"p": 0}, ctx)
    assert (hour_utc, day_utc) == (22, 14)
    assert (hour_sh, day_sh) == (6, 15)


def test_iana_zone_differs_from_fixed_offset_only_by_name_resolution():
    # 固定偏移 "8" 与 Asia/Shanghai 在该冬令时刻应一致（均 +08:00）
    ctx = {"created_at": _EPOCH_SHANGHAI}
    fixed, _ = ee.eval_tiered_expr('hour("8")', {"p": 0}, ctx)
    iana, _ = ee.eval_tiered_expr('hour("Asia/Shanghai")', {"p": 0}, ctx)
    assert fixed == iana == 6


def test_time_funcs_accept_datetime_created_at():
    from datetime import datetime

    aware = datetime.fromtimestamp(_EPOCH_SHANGHAI, tz=UTC)
    hour_utc, _ = ee.eval_tiered_expr("hour()", {"p": 0}, {"created_at": aware})
    hour_sh, _ = ee.eval_tiered_expr('hour("Asia/Shanghai")', {"p": 0}, {"created_at": aware})
    assert (hour_utc, hour_sh) == (22, 6)


def test_naive_datetime_created_at_assumed_utc():
    """naive datetime 按仓库统一约定视为 UTC 墙钟（supplement 始终写 aware）。"""
    from datetime import datetime

    naive = datetime(2023, 11, 14, 22, 13, 20)
    hour_sh, _ = ee.eval_tiered_expr('hour("Asia/Shanghai")', {"p": 0}, {"created_at": naive})
    assert hour_sh == 6


def test_header_reads_flat_top_level_for_legacy_records():
    """历史行 headers 存在 billing_context 顶层（旧扁平形态），header() 必须能读到。"""
    expr = 'has(header("anthropic-beta"), "1h") ? 10 : 1'
    legacy_ctx = {
        "headers": {"anthropic-beta": "prompt-caching-1h"},
        "created_at": 1_700_000_000.0,
    }
    val, _ = ee.eval_tiered_expr(expr, {"p": 0}, legacy_ctx)
    assert val == pytest.approx(10)


# ---- 运行时诊断：只记名字不记值（表达式原文/头值/凭据绝不入日志）----


def test_header_miss_logs_names_only_at_debug(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="cost_control.expr_eval")
    expr = 'has(header("authorization"), "Bearer") ? 9 : 3'
    val, _ = ee.eval_tiered_expr(
        expr,
        {"p": 1},
        {
            "params": {
                "headers": {
                    "X-API-Key": "sk-SECRET-VALUE",
                    "anthropic-beta": "cc-1h-gold",
                }
            }
        },
    )
    assert val == pytest.approx(3)
    recs = [r for r in caplog.records if r.name == "cost_control.expr_eval"]
    assert any("header 未命中" in r.getMessage() for r in recs)
    assert all(r.levelname == "DEBUG" for r in recs)
    text = "\n".join(r.getMessage() for r in recs)
    assert "x-api-key" in text  # 头名允许（排查拼写）
    assert "sk-SECRET-VALUE" not in text  # 值严格禁止
    assert "cc-1h-gold" not in text  # 即便白名单头的值也不落日志


def test_param_miss_logs_keys_only_at_debug(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="cost_control.expr_eval")
    expr = 'param("credentials.token") ? 8 : 2'
    val, _ = ee.eval_tiered_expr(
        expr,
        {"p": 0},
        {"params": {"service_tier": "flex-priority-secret"}},
    )
    assert val == pytest.approx(2)
    recs = [r for r in caplog.records if r.name == "cost_control.expr_eval"]
    assert any("param 未命中" in r.getMessage() for r in recs)
    assert all(r.levelname == "DEBUG" for r in recs)
    text = "\n".join(r.getMessage() for r in recs)
    assert "params.service_tier" in text  # 只列可用键名
    assert "flex-priority-secret" not in text  # 键值严格禁止


def test_string_repeat_bomb_blocked_at_validation():
    """链式字符串重复不能通过保存前校验（防热路径 OOM）。"""
    bomb = 'has(("a"*1000)*1000*100, "a") ? 8 : 2'
    msg = ee.validate_tiered_expr(bomb)
    assert msg is not None
    assert "字符串运算结果过长" in msg


def test_string_repeat_bomb_blocked_at_eval():
    bomb = 'has(("a"*1000)*1000*100, "a") ? 8 : 2'
    with pytest.raises(ValueError, match="字符串运算结果过长"):
        ee.eval_tiered_expr(bomb, {}, {})


def test_small_string_repeat_still_works():
    val, _ = ee.eval_tiered_expr('has("ab"*3, "abab") ? 8 : 2', {}, {})
    assert val == pytest.approx(8)


def test_string_concat_bounded():
    val, _ = ee.eval_tiered_expr('has("a" + "b", "ab") ? 8 : 2', {}, {})
    assert val == pytest.approx(8)


def test_overlong_string_literal_rejected():
    # 超长字面量先被 4000 字符总长拦截（字面量 10k 上限是纵深防御）。
    msg = ee.validate_tiered_expr(f'has("{"x"*20001}", "x") ? 8 : 2')
    assert msg is not None
