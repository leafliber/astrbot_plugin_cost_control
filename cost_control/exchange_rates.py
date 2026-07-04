"""汇率模块。

维护一份以 USD 为基准的汇率表（``1 USD = rate 单位货币X``），提供纯函数
:func:`convert` 做任意货币间换算，以及异步 :func:`sync_rates` 从免费 API
（``open.er-api.com``，无需 key）刷新本地汇率。

设计要点：

- **USD 锚定**：内置 ``DEFAULT_PRICING`` 与历史用户定价均以 USD 计价，汇率表以
  USD 为锚存储，主货币切换时不需改汇率表，只需交叉换算。
- **本地优先**：汇率存在插件 ``config.json``（由 ``config.py`` 持久化），
  仅在用户于设置页点「立即同步」时联网刷新；无网时用内置静态表兜底，不阻断。
- **纯函数换算**：:func:`convert` 不依赖任何 IO，便于单测。

阶段一实现。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

# 内置静态汇率表（1 USD = rate 单位货币X），随版本更新。
# 作为首次启动与无网兜底；数值仅供参考，精度不保证。
DEFAULT_RATES: dict[str, float] = {
    "USD": 1.0,
    "CNY": 7.2,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 150.0,
    "KRW": 1350.0,
    "INR": 83.0,
    "HKD": 7.8,
    "SGD": 1.35,
    "TWD": 32.0,
    "RUB": 90.0,
    "BRL": 5.0,
}

# 货币代码 → 显示符号（用于文案/前端展示）。
CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "CNY": "¥",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "KRW": "₩",
    "INR": "₹",
    "HKD": "HK$",
    "SGD": "S$",
    "TWD": "NT$",
    "RUB": "₽",
    "BRL": "R$",
}


def currency_to_symbol(code: str | None) -> str:
    """货币代码 → 显示符号，未知代码回退代码本身。"""
    c = str(code or "").strip().upper()
    return CURRENCY_SYMBOLS.get(c, c or "$")

# 免费汇率 API（无需 key，返回各货币对 USD 汇率）。
_RATES_API = "https://open.er-api.com/v6/latest/USD"


def convert(
    amount: float,
    from_cur: str,
    to_cur: str,
    rates: dict[str, float] | None,
) -> float:
    """把 ``amount`` 从 ``from_cur`` 换算到 ``to_cur``（经 USD 中转，纯函数）。

    换算公式：``amount × (rate_to / rate_from)``。

    兜底策略（任一货币不在 rates / DEFAULT_RATES 中）：
    - 目标货币缺失 → rate=1（等同原值，避免异常）；
    - 源货币缺失或 rate<=0 → 返回原值（避免除零）。
    同货币直接返回原值。

    Args:
        amount: 待换算金额。
        from_cur: 源货币代码（如 ``"USD"``）。
        to_cur: 目标货币代码（如 ``"CNY"``）。
        rates: 生效汇率表（来自 config）；为空时回退 DEFAULT_RATES。

    Returns:
        换算后的金额（float）。
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    fc = str(from_cur or "").strip().upper()
    tc = str(to_cur or "").strip().upper()
    if not fc or not tc or fc == tc:
        return amount
    tbl = rates if rates else DEFAULT_RATES
    r_from = _lookup_rate(fc, tbl)
    r_to = _lookup_rate(tc, tbl)
    if r_from <= 0:
        return amount  # 源货币无汇率，避免除零
    return round(amount * (r_to / r_from), 8)


def _lookup_rate(cur: str, rates: dict[str, float]) -> float:
    """查某货币汇率：rates 优先 → DEFAULT_RATES → 1.0 兜底。"""
    v = rates.get(cur)
    if v is None:
        v = DEFAULT_RATES.get(cur)
    try:
        f = float(v) if v is not None else 1.0
        return f if f > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def get_main_currency(cfg: Any) -> str:
    """读取主货币代码（默认 ``"USD"``）。

    兼容历史 ``"$"`` 值——自动归一化为 ``"USD"``。
    """
    raw = cfg.get("currency_symbol") if isinstance(cfg, dict) else None
    cur = str(raw or "USD").strip().upper()
    if cur in ("$", "＄", "USD"):
        return "USD"
    return cur


def get_rates(cfg: Any) -> dict[str, float]:
    """读取生效汇率表（合并 config 与 DEFAULT_RATES，config 优先）。"""
    raw = cfg.get("exchange_rates") if isinstance(cfg, dict) else None
    out: dict[str, float] = dict(DEFAULT_RATES)
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def get_rate_updated_at(cfg: Any) -> str:
    """读取汇率最近同步时间（ISO，空=未同步过）。"""
    if isinstance(cfg, dict):
        return str(cfg.get("exchange_rates_updated_at") or "")
    return ""


async def sync_rates(timeout: float = 10.0) -> tuple[dict[str, float], str, str]:
    """从免费 API 同步最新汇率，返回 (rates, updated_at, error)。

    调用 ``open.er-api.com/v6/latest/USD``，解析 ``rates`` 字段。成功返回
    更新后的汇率表与当前 UTC 时间；失败返回 ``(DEFAULT_RATES, "", errmsg)``。

    本函数只负责取数与解析，不写 config（由调用方持久化）。

    Args:
        timeout: 请求超时秒数。

    Returns:
        ``(rates, updated_at, error)``。error 非空表示失败（此时 rates 为静态表）。
    """
    import urllib.request

    try:
        req = urllib.request.Request(
            _RATES_API, headers={"Accept": "application/json", "User-Agent": "cost-control/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        raw_rates = data.get("rates") if isinstance(data, dict) else None
        if not isinstance(raw_rates, dict) or "USD" not in raw_rates:
            return dict(DEFAULT_RATES), "", "汇率 API 返回数据格式异常"
        # 保留数值型，过滤非法
        rates: dict[str, float] = {}
        for k, v in raw_rates.items():
            try:
                rates[str(k).upper()] = float(v)
            except (TypeError, ValueError):
                continue
        # 确保基准 USD=1.0
        rates["USD"] = 1.0
        updated_at = datetime.now(UTC).isoformat()
        return rates, updated_at, ""
    except Exception as e:
        return dict(DEFAULT_RATES), "", str(e)


__all__ = [
    "DEFAULT_RATES",
    "convert",
    "get_main_currency",
    "get_rates",
    "get_rate_updated_at",
    "sync_rates",
]
