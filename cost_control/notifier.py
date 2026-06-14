"""告警 Mixin。

负责把预算超限、缓存命中率过低等事件主动推送给用户，并实现冷却逻辑
（同一会话两次告警的最小间隔），避免刷屏。

阶段 2 实现。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent


class NotifierMixin:
    """主动推送告警与冷却控制的 Mixin。"""

    async def notify(self, event: AstrMessageEvent, message: str) -> None:
        """向触发 ``event`` 的会话推送一条告警消息。

        内部实现冷却：若同一会话在 ``alerts.cooldown_seconds`` 内已推送过，
        则跳过本次。

        Args:
            event: 触发告警的 ``AstrMessageEvent``，用于定位会话。
            message: 告警文本。
        """
        raise NotImplementedError("阶段2实现")
