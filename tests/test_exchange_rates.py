"""汇率换算纯函数单测：内置货币换算与未知货币兜底。"""

from cost_control.exchange_rates import DEFAULT_RATES, _lookup_rate, convert


def test_convert_usd_to_cny_uses_default_rates():
    assert abs(convert(10.0, "USD", "CNY", None) - 10.0 * DEFAULT_RATES["CNY"]) < 1e-6


def test_convert_same_currency_returns_original():
    assert convert(3.5, "USD", "USD", None) == 3.5
    assert convert(3.5, "", "CNY", None) == 3.5


def test_convert_case_insensitive_currency_codes():
    assert abs(convert(1.0, "usd", "cny", None) - DEFAULT_RATES["CNY"]) < 1e-6


def test_convert_unknown_source_returns_original():
    # VND 不在汇率表：绝不能被隐式当作 USD（rate=1）再乘目标汇率、虚增数量级。
    assert convert(100.0, "VND", "CNY", None) == 100.0


def test_convert_unknown_target_returns_original():
    assert convert(100.0, "CNY", "VND", None) == 100.0


def test_convert_both_unknown_returns_original():
    assert convert(100.0, "VND", "CAD", None) == 100.0


def test_convert_prefers_custom_rates_over_defaults():
    rates = {"CNY": 7.0}
    assert abs(convert(10.0, "USD", "CNY", rates) - 70.0) < 1e-6


def test_convert_custom_rate_unlocks_unknown_currency_pair():
    # 用户在设置页同步 / 手工补过汇率后，非内置货币也能正常换算。
    rates = {"VND": 25000.0}
    assert abs(convert(100.0, "USD", "VND", rates) - 2_500_000.0) < 1e-2


def test_convert_invalid_amount_becomes_zero():
    assert convert("bad", "USD", "CNY", None) == 0.0


def test_lookup_rate_builtins_available_in_empty_table():
    assert _lookup_rate("USD", {}) == 1.0
    assert _lookup_rate("CNY", {}) == DEFAULT_RATES["CNY"]


def test_lookup_rate_unknown_returns_none_not_one():
    assert _lookup_rate("VND", {}) is None


def test_lookup_rate_invalid_or_nonpositive_returns_none():
    assert _lookup_rate("XYZ", {"XYZ": -1}) is None
    assert _lookup_rate("XYZ", {"XYZ": 0}) is None
    assert _lookup_rate("XYZ", {"XYZ": "bad"}) is None
    assert _lookup_rate("XYZ", {"XYZ": None}) is None


# ===== sync_rates：阻塞取数必须下沉线程，结果原样回传 =====


def test_sync_rates_delegates_to_blocking_impl(monkeypatch):
    import asyncio

    import cost_control.exchange_rates as er

    seen: list[float] = []

    def fake_blocking(timeout: float):
        seen.append(timeout)
        return ({"USD": 1.0, "CNY": 7.0}, "t0", "")

    monkeypatch.setattr(er, "_sync_rates_blocking", fake_blocking)
    rates, updated_at, err = asyncio.run(er.sync_rates(timeout=3.0))
    assert err == ""
    assert rates == {"USD": 1.0, "CNY": 7.0}
    assert updated_at == "t0"
    assert seen == [3.0]


def test_sync_rates_propagates_error_from_thread(monkeypatch):
    import asyncio

    import cost_control.exchange_rates as er

    monkeypatch.setattr(
        er, "_sync_rates_blocking", lambda timeout: (dict(er.DEFAULT_RATES), "", "boom")
    )
    _rates, _updated_at, err = asyncio.run(er.sync_rates())
    assert err == "boom"


def test_sync_rates_blocking_invalid_payload_returns_error(monkeypatch):
    import urllib.request
    from unittest.mock import patch

    import cost_control.exchange_rates as er

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return b'{"rates": []}'  # 非法：rates 非 dict

    with patch.object(urllib.request, "urlopen", return_value=_Resp()):
        rates, updated_at, err = er._sync_rates_blocking(1.0)
    assert err == "汇率 API 返回数据格式异常"
    assert updated_at == ""
    assert rates == dict(er.DEFAULT_RATES)
