"""Web API Mixin。

通过 AstrBot 的 ``register_web_api`` 机制对外暴露 REST 接口与 SSE 流，
供 Plugin Page 前端（``page/``）通过 bridge SDK 拉取数据。

阶段 4 实现。
"""

from __future__ import annotations


class WebApiMixin:
    """注册 REST + SSE Web API 路由的 Mixin。"""

    def register_routes(self) -> None:
        """在 ``Main.__init__`` 中调用，注册所有 Web API 路由。

        阶段 4 实现：注册以下端点：
        - ``GET /api/usage`` 用量聚合查询
        - ``GET /api/cost`` 成本查询
        - ``GET /api/cache`` 缓存命中率与诊断
        - ``GET /api/attribution`` 归因报表
        - ``GET /api/report`` 综合报表
        - ``GET /api/stream`` SSE 实时事件流
        """
        # TODO 阶段4：注册 Web API 路由
        raise NotImplementedError("阶段4实现")
