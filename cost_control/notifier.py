"""告警 Mixin。

负责把预算超限、缓存命中率过低等事件主动推送给用户，并实现冷却逻辑
（同一会话两次告警的最小间隔），避免刷屏。

可测性设计：冷却判定抽成纯函数 ``cooldown_elapsed``，可单测；推送部分延迟
import astrbot 的 ``MessageChain``，保持本模块顶层零 astrbot 硬依赖（除
``AstrMessageEvent`` 类型注解，置于 ``TYPE_CHECKING`` 下）。

推送通道（已核对 astrbot 源码）：
- 有 event 时（钩子 / 命令）：``await event.send(MessageChain().message(text))``。
- 无 event 时（CronJob 回调）：``await context.send_message(umo, chain)``。

阶段 2 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .config import get_config

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

# Preference 中存冷却时间戳的 key（scope="umo"）。
_COOLDOWN_KEY = "cost_alert_cooldown"


def cooldown_elapsed(
    last_ts: float | None,
    now_ts: float,
    cooldown_seconds: int,
) -> bool:
    """判断是否已过冷却期（纯函数）。

    Args:
        last_ts: 上次推送的 Unix 时间戳（秒），None 表示从未推送。
        now_ts: 当前 Unix 时间戳（秒）。
        cooldown_seconds: 冷却秒数；``<=0`` 表示不冷却（总是允许）。

    Returns:
        True 表示可以推送（已过冷却或未配置冷却）。
    """
    if cooldown_seconds <= 0:
        return True
    if last_ts is None:
        return True
    return (now_ts - last_ts) >= cooldown_seconds


class NotifierMixin:
    """主动推送告警与冷却控制的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    # 兄弟 ``StoreMixin`` 提供（Preference 读写）。
    get_pref: Any
    set_pref: Any

    def _cooldown_seconds(self) -> int:
        """读取 ``alerts.cooldown_seconds``，解析失败回退 0（不冷却）。"""
        alerts = get_config(getattr(self, "cfg", None), "alerts", {}) or {}
        try:
            return int(alerts.get("cooldown_seconds", 0) or 0)
        except (TypeError, ValueError):
            return 0

    async def notify(self, event: AstrMessageEvent, message: str) -> bool:
        """向触发 ``event`` 的会话推送告警，带冷却去重。

        Args:
            event: 触发告警的事件，用于定位会话与推送。
            message: 告警文本。

        Returns:
            True 表示已推送，False 表示被冷却跳过或推送失败。
        """
        umo = str(
            getattr(event, "unified_msg_origin", None) or getattr(event, "session_id", None) or ""
        )
        now = datetime.now(UTC)
        # 先检查冷却（不写入）：发送成功后再标记，避免发送失败却消耗冷却窗口，
        # 导致后续告警被静默。
        if not await self._check_cooldown(umo, now):
            return False
        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(message)
            send = getattr(event, "send", None)
            if send is None:
                return False
            await send(chain)
        except Exception:
            return False
        # 发送成功后才标记冷却（失败不消耗窗口，下次仍可重试）。
        await self._mark_cooldown(umo, now)
        return True

    async def push_to_session(self, umo: str, message: str) -> bool:
        """无 event 主动推送（CronJob / 跨会话场景），不做冷却。

        Args:
            umo: 目标会话（unified_msg_origin 字符串）。
            message: 文本内容。

        Returns:
            True 表示推送成功。
        """
        if not umo:
            return False
        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(message)
            return bool(await self.context.send_message(umo, chain))
        except Exception:
            return False

    async def _check_cooldown(self, umo: str, now: datetime) -> bool:
        """检查是否已过冷却期（不写入）。读失败时放行（不阻断推送）。

        与 :meth:`_mark_cooldown` 分离，使发送失败不消耗冷却窗口。
        """
        if not umo:
            return True
        cd = self._cooldown_seconds()
        try:
            last = await self.get_pref("umo", umo, _COOLDOWN_KEY)
            last_ts = last.get("ts") if isinstance(last, dict) else None
            if not cooldown_elapsed(
                float(last_ts) if last_ts is not None else None,
                now.timestamp(),
                cd,
            ):
                return False
            return True
        except Exception:
            return True

    async def _mark_cooldown(self, umo: str, now: datetime) -> None:
        """写入新的冷却时间戳（仅在告警发送成功后调用）。写失败静默。"""
        if not umo:
            return
        try:
            await self.set_pref("umo", umo, _COOLDOWN_KEY, {"ts": now.timestamp()})
        except Exception:
            pass
