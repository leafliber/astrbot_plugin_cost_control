"""expr_eval 测试：New API 官方测试向量对齐 + 安全拒绝 + 语法转换。"""

from __future__ import annotations

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


def test_len_variable_no_builtin_clash():
    # len 是变量不是内置函数，可参与运算
    val, _ = ee.eval_tiered_expr("len * 2", {"len": 100, "p": 0})
    assert val == pytest.approx(200)


def test_time_funcs_accept_datetime_created_at():
    """生产调用方（supplement/store）传 datetime 对象：按请求时刻计价而非求值时刻。"""
    from datetime import UTC, datetime

    dt = datetime.fromtimestamp(1_700_000_000.0, tz=UTC)  # 2023-11-14 UTC
    month, _ = ee.eval_tiered_expr("month()", {"p": 0}, {"created_at": dt})
    assert month == 11
    day, _ = ee.eval_tiered_expr("day()", {"p": 0}, {"created_at": dt})
    assert day == 14
    # naive datetime 按 UTC 解释（库表 created_at 统一以 UTC 写入）
    naive = datetime(2023, 11, 14, 22, 13, 20)
    m2, _ = ee.eval_tiered_expr("month()", {"p": 0}, {"created_at": naive})
    assert m2 == 11
    # 时区换算：+08:00 下已是 11-15
    tz_day, _ = ee.eval_tiered_expr('day("+08:00")', {"p": 0}, {"created_at": dt})
    assert tz_day == 15


def test_time_funcs_accept_iso_string_created_at():
    val, _ = ee.eval_tiered_expr("month()", {"p": 0}, {"created_at": "2023-11-14T22:13:20Z"})
    assert val == 11
