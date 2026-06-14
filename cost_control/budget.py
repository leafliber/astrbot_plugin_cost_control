"""预算 Mixin。

按会话 / 用户 / 模型 / 全局四维，日 / 月两个时间窗，检查 token 用量是否
超出 ``budgets`` 配置的阈值，并按 ``over_limit_policy`` 决定拦截或切换
备用 Provider。

阶段 2 实现。
"""

from __future__ import annotations

from typing import Any


class BudgetMixin:
    """预算阈值检查的 Mixin。"""

    async def check_budget(self, umo: str) -> dict[str, Any]:
        """检查指定会话当前是否超出任一预算维度。

        Args:
            umo: 会话标识（unified message origin）。

        Returns:
            形如 ``{"exceeded": bool, "dim": "per_session_daily", "limit": 10000,
            "used": 12000, "policy": {...}}`` 的检查结果 dict。
        """
        raise NotImplementedError("阶段2实现")
