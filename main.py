"""成本控制插件主入口。

本模块定义 AstrBot 插件入口类 ``Main``，通过多继承组合 ``cost_control/`` 下
各 ``XxxMixin``，把用量采集、预算拦截、缓存诊断、提示词优化、Plugin Page 等
能力以可插拔方式挂载到单个插件上。

阶段 0：仅骨架，所有钩子方法为 TODO 占位。
"""

from __future__ import annotations

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .cost_control.analytics import AnalyticsMixin
from .cost_control.attributor import AttributorMixin
from .cost_control.budget import BudgetMixin
from .cost_control.cache_diag import CacheDiagMixin
from .cost_control.commands import CommandsMixin
from .cost_control.cost import CostMixin
from .cost_control.notifier import NotifierMixin
from .cost_control.prompt_optimizer import PromptOptimizerMixin
from .cost_control.schedule import ScheduleMixin
from .cost_control.store import StoreMixin
from .cost_control.supplement import SupplementMixin
from .cost_control.usage_query import UsageQueryMixin
from .cost_control.web_api import WebApiMixin


class Main(
    StoreMixin,
    UsageQueryMixin,
    CostMixin,
    SupplementMixin,
    AttributorMixin,
    BudgetMixin,
    NotifierMixin,
    CacheDiagMixin,
    PromptOptimizerMixin,
    AnalyticsMixin,
    ScheduleMixin,
    WebApiMixin,
    CommandsMixin,
    Star,
):
    """成本控制插件入口。

    继承顺序约定：底层存储 / 数据 Mixin 在前，业务 Mixin 居中，
    命令 / Web API 等入口型 Mixin 在后，最后是 ``Star``。
    Mixin 自身不定义 ``__init__``，统一使用本类的 ``__init__``。
    """

    def __init__(self, context: Context, config) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}

    async def initialize(self) -> None:
        """插件加载时初始化：建立独立 sqlite 补充表 + 注册 CronJob。

        任一步失败仅记录日志，不阻断插件加载（降级：相应能力不可用，其余正常）。
        """
        try:
            await self.init_store()
        except Exception as e:
            logger.warning("[cost_control] 初始化存储失败: %s", e)
        try:
            await self.register_cron()
        except Exception as e:
            logger.warning("[cost_control] CronJob 注册失败: %s", e)

    @filter.on_llm_request(priority=100000)
    async def on_llm_request_head(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前（最高优先级）：阶段 2 预算硬拦截；阶段 3 上下文快照。

        预算超限时调 ``event.stop_event()`` 中止后续 LLM 调用，并返回提示。
        ``fallback_provider`` 策略暂降级为拦截（切换 provider 机制待阶段 3）。
        异常一律降级放行，绝不阻断主流程。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", None) or "")
            model = getattr(req, "model", None) or None
            result = await self.check_budget(umo, model)
            if result.get("exceeded"):
                policy = result.get("policy") or {}
                action = policy.get("action", "stop_llm")
                msg = (
                    f"⏸ 已超出预算（{result.get('dim')}）："
                    f"用 {result.get('used')} / 限 {result.get('limit')} token"
                )
                if action != "stop_llm":
                    msg += "\n（fallback 策略暂未启用，已拦截）"
                event.stop_event()
                yield event.plain_result(msg)
                return
        except Exception as e:
            logger.warning("[cost_control] 预算检查失败: %s", e)
        # TODO 阶段3：快照初始上下文 token

    @filter.on_llm_request(priority=-100000)
    async def on_llm_request_tail(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """LLM 请求前（最低优先级）：快照最终上下文，算注入差。

        阶段 3 实现：在所有高优先级钩子执行完毕后，记录最终上下文，
        与 head 快照对比，得到各组件（system / tools / history / user）的
        token 注入量。
        """
        # TODO 阶段3：快照最终上下文，算注入差
        ...

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        """LLM 响应后：采集 usage + raw cache 写补充表（阶段 1）。

        阶段 2-3 在此扩展：预算阈值检查、缓存破坏诊断、主动告警。
        任何异常都被捕获并降级（仅记录日志），绝不影响 AstrBot 主流程。
        """
        try:
            record = await self.collect_response(event, resp)
            await self.save_supplement(record)
        except Exception as e:
            logger.warning("[cost_control] on_llm_response 采集失败: %s", e)
