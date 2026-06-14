"""缓存破坏诊断 Mixin。

识别四类导致 prompt cache 失效的原因，并在命中率低于阈值时触发告警：

1. **上下文重置**：会话上下文被意外清空，导致历史缓存全部失效。
2. **system prompt 变更**：多轮间 system prompt 内容变化，前缀不匹配。
3. **工具定义变更**：tools 集合在多轮间变化，破坏缓存键。
4. **上下文顺序漂移**：消息顺序变化导致前缀不匹配。

阶段 3 实现。
"""

from __future__ import annotations

from typing import Any


class CacheDiagMixin:
    """缓存破坏四类诊断的 Mixin。"""

    async def diagnose(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """对一条补充采集记录做缓存破坏诊断。

        Args:
            record: ``SupplementMixin.collect_response`` 保存的记录，含 usage、
                cache 字段与上下文快照。

        Returns:
            诊断结果列表，每项形如
            ``{"type": "context_reset", "severity": "high", "detail": "..."}``；
            无问题则返回空列表。
        """
        raise NotImplementedError("阶段3实现")
