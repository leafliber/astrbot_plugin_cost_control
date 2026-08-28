"""New API 兼容的计费表达式求值器（无第三方依赖，ast 白名单安全求值）。

语法兼容 expr-lang 计费表达式子集：`&&`/`||`/`!`、三元 `cond ? a : b`（右结合）、
`true`/`false`，以及变量与内置函数。表达式系数约定为 ``$ / 1M token``，本模块
:func:`eval_tiered_expr` 返回表达式**原始输出值**，``/ 1_000_000`` 换算由调用方
（``cost.compute_cost``）统一处理，与 New API ``quotaConversion`` v1 语义一致。

兼容上游 ``v1:`` 版本前缀：编译/校验前剥掉前缀执行 v1 语义主体，存储与展示仍
保留原文。上下文支持两种计费字段形态：请求侧字段挂在 ``context["params"]`` 下
（当前写入格式），以及历史记录中的扁平顶层键（只读兼容）；时间函数同时接受
epoch 秒与 ``datetime`` 的 ``created_at``，时区参数兼容固定偏移与 IANA 名称。
"""

from __future__ import annotations

import ast
import hashlib
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any

# 生产环境经 root logger 汇入 astrbot/loguru；测试经 caplog 可捕获。
logger = logging.getLogger("cost_control.expr_eval")

# ---- 语法转换 ----

# 变量名（运算符/函数名之外，表达式可用的 token 维度）。
VARIABLE_NAMES = ("p", "c", "len", "cr", "cc", "cc1h", "img", "img_o", "ai", "ao")
_VARIABLE_NAME_SET = frozenset(VARIABLE_NAMES)


def split_version_prefix(expr: str) -> str:
    """剥掉 New API 上游计费表达式的 ``v1:`` 语义版本前缀，返回可执行主体。

    上游 ``billing_expr`` 形如 ``v1:p*2+c*8``；前缀是 quotaConversion 版本标记
    而非表达式语法。编译/校验统一经此归一化，因此调用方可原样保存/展示上游
    原文。无前缀或空值原样返回。
    """
    s = str(expr or "").strip()
    if len(s) >= 3 and s[:3].lower() == "v1:":
        s = s[3:].strip()
    return s


def _extract_strings(expr: str) -> tuple[str, list[str]]:
    """把字符串字面量替换为占位符，返回 (处理后的表达式, 字面量列表)。

    防止 ``&&``/``?`` 等运算符替换误伤字符串内容。支持单/双引号与反斜杠转义。
    """
    out: list[str] = []
    strings: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in ('"', "'"):
            j = i + 1
            buf = [ch]
            while j < n:
                if expr[j] == "\\" and j + 1 < n:
                    buf.append(expr[j])
                    buf.append(expr[j + 1])
                    j += 2
                    continue
                buf.append(expr[j])
                if expr[j] == ch:
                    break
                j += 1
            placeholder = f"\x00STR{len(strings)}\x00"  # 哨兵占位符，不与表达式内容冲突
            strings.append("".join(buf))
            out.append(placeholder)
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), strings


def _restore_strings(expr: str, strings: list[str]) -> str:
    for idx, s in enumerate(strings):
        expr = expr.replace(f"\x00STR{idx}\x00", s)
    return expr


def _convert_parens(s: str) -> str:
    """递归转换每个顶层括号组内部的三元，返回重组后的字符串。"""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "(":
            depth = 1
            j = i + 1
            while j < n and depth:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            if depth != 0:
                raise ValueError("括号不匹配")
            inner = s[i + 1 : j - 1]
            out.append("(" + _convert_ternary(inner) + ")")
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _convert_ternary(expr: str) -> str:
    """把三元 ``cond ? a : b`` 转成 ``(a if cond else b)``（右结合，支持括号内嵌套）。

    先用 :func:`_convert_parens` 递归转换括号组内部，再在本层（括号深度 0）
    通过计数匹配 ``?`` 对应的 ``:``（嵌套 ``?`` 深度 +1、对应 ``:`` 回落）完成右结合转换。
    """
    expr = _convert_parens(expr.strip())
    depth_paren = 0
    depth_tern = 0
    q_pos = -1
    for i, ch in enumerate(expr):
        if ch in "([":
            depth_paren += 1
        elif ch in ")]":
            depth_paren -= 1
        elif depth_paren == 0:
            if ch == "?":
                if depth_tern == 0 and q_pos == -1:
                    q_pos = i
                depth_tern += 1
            elif ch == ":" and depth_tern > 0:
                depth_tern -= 1
                if depth_tern == 0 and q_pos != -1:
                    cond = expr[:q_pos]
                    rest = expr[q_pos + 1 :]
                    # rest 形如 "a : b"，需在括号/三元深度 0 找到分隔的 ":"
                    colon = _find_top_colon(rest)
                    if colon == -1:
                        raise ValueError("三元表达式缺少 ':'")
                    a = rest[:colon]
                    b = rest[colon + 1 :]
                    return (
                        f"({_convert_ternary(a)} if {_convert_ternary(cond)} "
                        f"else {_convert_ternary(b)})"
                    )
    if q_pos != -1:
        raise ValueError("三元表达式缺少 ':'")
    return expr


def _find_top_colon(s: str) -> int:
    """在括号深度 0、三元深度 0 处找分隔 ``a : b`` 的 ``:`` 下标。"""
    depth_paren = 0
    depth_tern = 0
    for i, ch in enumerate(s):
        if ch in "([":
            depth_paren += 1
        elif ch in ")]":
            depth_paren -= 1
        elif depth_paren == 0:
            if ch == "?":
                depth_tern += 1
            elif ch == ":":
                if depth_tern == 0:
                    return i
                depth_tern -= 1
    return -1


def expr_to_python(expr: str) -> str:
    """expr-lang → Python：``&&``/``||``/``!``、三元（右结合）、``true``/``false``。"""
    if not expr or not expr.strip():
        raise ValueError("表达式为空")
    s, strings = _extract_strings(expr.strip())
    s = s.replace("&&", " and ").replace("||", " or ")
    # 单字符 '!' → 'not '，但不能动 '!='。
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "!" and (i + 1 >= n or s[i + 1] != "="):
            out.append(" not ")
        else:
            out.append(s[i])
        i += 1
    s = "".join(out)
    s = _convert_ternary(s)
    # 布尔字面量（词边界，避免误伤占位符/标识符）。
    s = _replace_keyword(s, "true", "True")
    s = _replace_keyword(s, "false", "False")
    s = _restore_strings(s, strings)
    return s


def _replace_keyword(s: str, word: str, repl: str) -> str:
    out: list[str] = []
    i, n, wl = 0, len(s), len(word)
    while i < n:
        if (
            s.startswith(word, i)
            and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_"))
            and (i + wl >= n or not (s[i + wl].isalnum() or s[i + wl] == "_"))
        ):
            out.append(repl)
            i += wl
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# ---- 安全校验（白名单 AST 节点）----

_ALLOWED_NODES = frozenset(
    {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    }
)
_ALLOWED_FUNCS = frozenset(
    {
        "tier",
        "has",
        "param",
        "header",
        "hour",
        "minute",
        "weekday",
        "month",
        "day",
        "max",
        "min",
        "abs",
        "ceil",
        "floor",
    }
)
_MAX_NODES = 1000  # 节点总数上限，防资源消耗


def _check_node(node: ast.AST) -> None:
    """拒绝 Attribute/Import/Lambda/推导式/非白名单 Call 等。"""
    count = 0
    for sub in ast.walk(node):
        count += 1
        if count > _MAX_NODES:
            raise ValueError(f"表达式过于复杂（节点数超过 {_MAX_NODES}）")
        if type(sub) not in _ALLOWED_NODES:
            raise ValueError(f"不允许的语法：{type(sub).__name__}")
        if isinstance(sub, ast.Call):
            if not isinstance(sub.func, ast.Name) or sub.func.id not in _ALLOWED_FUNCS:
                raise ValueError("仅允许调用白名单函数")
        if isinstance(sub, ast.Name) and sub.id.startswith("__"):
            raise ValueError("不允许双下划线标识符")


def referenced_variables(expr: str) -> frozenset[str]:
    """返回表达式中实际引用的 token 维度变量名集合（编译失败返回空集）。

    用于 New API 变量 opt-in 归一化：只有显式出现在表达式里的细分变量
    （cr/cc/cc1h 等）才会把对应 token 从粗粒度维度中拆走。
    """
    try:
        code = _compile(expr)
    except Exception:
        return frozenset()
    return frozenset(name for name in code.co_names if name in _VARIABLE_NAME_SET)


# ---- 编译缓存 ----

_COMPILE_CACHE: OrderedDict[str, Any] = OrderedDict()
_COMPILE_CACHE_MAX = 256


def _compile(expr: str) -> Any:
    # 缓存键对齐上游语义：剥掉版本前缀后同一主体共享编译缓存。
    body = split_version_prefix(expr)
    key = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if key in _COMPILE_CACHE:
        _COMPILE_CACHE.move_to_end(key)
        return _COMPILE_CACHE[key]
    try:
        py = expr_to_python(body)
        tree = ast.parse(py, mode="eval")
        _check_node(tree)
    except Exception as e:
        logger.debug(
            "[cost_control] 表达式解析失败 class=%s len=%d",
            type(e).__name__,
            len(expr),
        )
        raise
    code = compile(tree, "<tiered_expr>", "eval")
    _COMPILE_CACHE[key] = code
    if len(_COMPILE_CACHE) > _COMPILE_CACHE_MAX:
        _COMPILE_CACHE.popitem(last=False)
    return code


# ---- 求值 ----


@dataclass
class TierTrace:
    matched_tier: str | None = None


def _parse_tz(tz: Any) -> tzinfo:
    """解析时区参数：固定偏移（``"Z"``/``"+8"``/``"+08:00"``/数字）或 IANA 名称。

    IANA 形式（如 ``Asia/Shanghai``）用标准库 zoneinfo；非法值一律回退 UTC，
    保证时间函数永不抛异常。
    """
    if tz is None or tz == "" or tz == "Z":
        return UTC
    if isinstance(tz, (int, float)):
        return timezone(timedelta(hours=float(tz)))
    s = str(tz).strip()
    try:
        if ":" in s:
            sign = 1
            if s.startswith("-"):
                sign = -1
            s2 = s.lstrip("+-")
            hh, mm = s2.split(":", 1)
            return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
        # 纯数字串按固定小时偏移处理（含小数，如 "-5.5"）。
        return timezone(timedelta(hours=float(s)))
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(s)
    except Exception:
        return UTC


def _context_dt(context: dict[str, Any], tz: Any) -> datetime:
    """从 context.created_at 构造指定时区 datetime；缺失/异常回退当前时刻。

    ``created_at`` 同时接受 epoch 秒（历史口径）与 ``datetime`` 对象（supplement
    新写入口径）；naive datetime 视为 UTC。
    """
    resolved = _parse_tz(tz)
    created = context.get("created_at") if isinstance(context, dict) else None
    if isinstance(created, datetime):
        dt = created if created.tzinfo is not None else created.replace(tzinfo=UTC)
        try:
            return dt.astimezone(resolved)
        except Exception:
            return datetime.now(resolved)
    try:
        ts = float(created or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return datetime.now(resolved)
    try:
        return datetime.fromtimestamp(ts, resolved)
    except Exception:
        return datetime.now(resolved)


def eval_tiered_expr(
    expr: str,
    variables: dict[str, float],
    context: dict[str, Any] | None = None,
) -> tuple[float, str | None]:
    """求值表达式，返回 (原始输出值, 命中的 tier 名)。不做 ``/1M``——由调用方决定。"""
    ctx = context or {}
    trace = TierTrace()

    def _tier(name: Any, value: Any) -> float:
        trace.matched_tier = str(name)
        return float(value)

    def _has(s: Any, sub: Any) -> bool:
        if s is None:
            return False
        return str(sub) in str(s)

    def _header(key: Any) -> str:
        # 当前形态：billing_context["params"]["headers"]；历史行兜底读顶层 headers。
        headers: dict[str, Any] | None = None
        params = ctx.get("params") if isinstance(ctx, dict) else None
        if isinstance(params, dict) and isinstance(params.get("headers"), dict):
            headers = params["headers"]
        elif isinstance(ctx, dict) and isinstance(ctx.get("headers"), dict):
            headers = ctx["headers"]
        if headers is None:
            return ""
        k = str(key).lower()
        for hk, hv in headers.items():
            if str(hk).lower() == k:
                return str(hv)
        # 只记录请求头键名，绝不记录值；帮助排查 request-rule 表达式的头名拼写。
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[cost_control] header 未命中 key=%s 可用=%s",
                k,
                sorted(str(h).lower() for h in headers),
            )
        return ""

    def _param(path: Any) -> Any:
        # 先查 params 嵌套（当前写入格式），再回退历史扁平顶层键。
        for scope in (
            ctx.get("params") if isinstance(ctx, dict) else None,
            ctx,
        ):
            if not isinstance(scope, dict):
                continue
            cur: Any = scope
            for part in str(path).split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    break
            else:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "[cost_control] param 命中 path=%s keys=%s",
                        path,
                        sorted(str(k) for k in scope.keys()),
                    )
                return cur
        if logger.isEnabledFor(logging.DEBUG):
            keys: set[str] = set()
            for scope_key, scope_v in (
                ("params", ctx.get("params") if isinstance(ctx, dict) else None),
                ("ctx", ctx),
            ):
                if isinstance(scope_v, dict):
                    keys.update(f"{scope_key}.{k}" for k in scope_v)
            # 只记录可用键名，绝不记录值（可能含请求体内容）。
            logger.debug("[cost_control] param 未命中 path=%s 可用键=%s", path, sorted(keys))
        return None

    namespace: dict[str, Any] = {
        "tier": _tier,
        "has": _has,
        "header": _header,
        "param": _param,
        "hour": lambda tz="": _context_dt(ctx, tz).hour,
        "minute": lambda tz="": _context_dt(ctx, tz).minute,
        # Go time.Weekday 语义：Sunday=0 … Saturday=6（Python weekday() 为 Mon=0）。
        "weekday": lambda tz="": (_context_dt(ctx, tz).weekday() + 1) % 7,
        "month": lambda tz="": _context_dt(ctx, tz).month,
        "day": lambda tz="": _context_dt(ctx, tz).day,
        "max": max,
        "min": min,
        "abs": abs,
        "ceil": math.ceil,
        "floor": math.floor,
    }
    for name in VARIABLE_NAMES:
        try:
            namespace[name] = float(variables.get(name, 0.0) or 0.0)
        except Exception:
            namespace[name] = 0.0

    code = _compile(expr)
    result = eval(code, {"__builtins__": {}}, dict(namespace))  # 白名单 AST 已限制可执行范围
    return float(result), trace.matched_tier


# ---- 验证（保存前 smoke test）----


def validate_tiered_expr(expr: str) -> str | None:
    """返回 ``None`` 表示通过；否则返回中文错误信息。多组测试向量 + 非负/非 NaN/Inf。"""
    expr = (expr or "").strip()
    if not expr:
        return "表达式为空"
    if len(expr) > 4000:
        return "表达式过长（>4000 字符）"
    try:
        _compile(expr)
    except Exception as e:
        return f"语法错误或含不允许的结构：{e}"
    vectors = [
        {"p": 0.0, "c": 0.0, "len": 0.0, "cr": 0.0, "cc": 0.0, "cc1h": 0.0},
        {"p": 1000.0, "c": 1000.0, "len": 2000.0, "cr": 0.0, "cc": 0.0, "cc1h": 0.0},
        {"p": 200000.0, "c": 1000.0, "len": 200100.0, "cr": 5000.0, "cc": 0.0, "cc1h": 0.0},
        {
            "p": 1000000.0,
            "c": 100000.0,
            "len": 1100000.0,
            "cr": 10000.0,
            "cc": 2000.0,
            "cc1h": 1000.0,
        },
    ]
    ctx = {
        "headers": {"service_tier": "default"},
        "params": {"service_tier": "default"},
        "created_at": 1_700_000_000.0,
    }
    for vec in vectors:
        try:
            val, _ = eval_tiered_expr(expr, vec, ctx)
        except Exception as e:
            return f"求值失败：{e}"
        if math.isnan(val) or math.isinf(val):
            return "表达式结果为 NaN/Inf"
        if val < 0:
            return "表达式结果不能为负"
    return None
