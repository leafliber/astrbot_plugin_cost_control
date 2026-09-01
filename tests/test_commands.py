"""``/report`` 参数解析回归测试。

AstrBot 的 ``CommandFilter`` 只在局部变量剥离命令名并把参数解析进
``parsed_params``（以 kwargs 注入 handler），**不改写** ``event.message_str``
（仍含 "/report" 前缀）——旧的整串 ``message_str`` 比较永远不等于
daily/weekly/monthly，静默回退 daily。此文件锁定 kwargs 注入口径。
"""

import asyncio
from types import SimpleNamespace

from cost_control.commands import CommandsMixin


class _ReportHost(CommandsMixin):
    """最小宿主：记录 build_report 收到的 window，其余字段给空报表。"""

    def __init__(self) -> None:
        self.cfg = {}
        self.windows: list[str] = []

    def build_report(self, window: str = "daily") -> dict:
        self.windows.append(window)
        return {
            "usage": {},
            "cost": 0.0,
            "cache_hit_rate": 0,
            "cache_samples": 0,
            "avg_injection": 0,
            "injection_samples": 0,
            "cost_by_model": [],
            "top_sessions": [],
        }


def _run(host: _ReportHost, window: str | None):
    """模拟 AstrBot 调度：event.message_str 带命令名，参数经 kwargs 注入。"""
    event = SimpleNamespace(
        message_str="/report " + (window or ""),
        plain_result=lambda s: s,
    )
    kwargs = {"window": window} if window is not None else {}

    async def _collect():
        return [out async for out in host.cmd_report(event, **kwargs)]

    return asyncio.run(_collect())


def test_report_window_from_kwargs_not_message_str():
    # 回归：message_str 是 "/report weekly"（带命令名），参数经 kwargs 到达。
    host = _ReportHost()
    _run(host, "weekly")
    assert host.windows == ["weekly"]


def test_report_window_normalizes_case_and_space():
    host = _ReportHost()
    _run(host, "  MONTHLY ")
    assert host.windows == ["monthly"]


def test_report_window_invalid_falls_back_daily():
    host = _ReportHost()
    _run(host, "yearly")  # 不在白名单 → daily
    assert host.windows == ["daily"]


def test_report_window_default_daily_without_kwargs():
    host = _ReportHost()
    _run(host, None)
    assert host.windows == ["daily"]
