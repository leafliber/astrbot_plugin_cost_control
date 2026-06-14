"""归因分析 Mixin。

在 ``on_llm_request`` 的 head / tail 两个钩子里快照上下文，估算 system /
tools / history / user 各组件的 token 占比，得到「注入归因」——即每次请求
的 token 到底花在哪个组件上，用于诊断高消耗来源与缓存破坏点。

阶段 3 实现。
"""

from __future__ import annotations

from typing import Any

from astrbot.api.provider import ProviderRequest


class AttributorMixin:
    """上下文 token 估算与注入归因的 Mixin。"""

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """粗略估算消息列表的 token 数。

        采用基于字符 / 结构的启发式估算，不依赖具体 tokenizer，
        用于归因对比而非精确计费。

        Args:
            messages: 消息列表（OpenAI 格式）。

        Returns:
            估算 token 数。
        """
        raise NotImplementedError("阶段3实现")

    async def snapshot_context(self, req: ProviderRequest) -> dict[str, int]:
        """对 ``ProviderRequest`` 的上下文做快照，分组件估算 token。

        Args:
            req: 即将发给 Provider 的请求对象，含 system prompt / tools / history。

        Returns:
            形如 ``{"system": 100, "tools": 200, "history": 500, "user": 50}``
            的各组件 token 估算 dict。
        """
        raise NotImplementedError("阶段3实现")
