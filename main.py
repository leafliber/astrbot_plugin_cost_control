"""成本控制插件主入口。

本模块定义 AstrBot 插件入口类 ``Main``，通过多继承组合 ``cost_control/`` 下
各 ``XxxMixin``，把用量采集、预算拦截、缓存诊断、提示词优化、Plugin Page 等
能力以可插拔方式挂载到单个插件上。

钩子签名约束（已核对 ``astrbot/core/pipeline/context_utils.call_event_hook`` +
``register/star_handler.py``）：``on_llm_request`` / ``on_llm_response`` /
``on_waiting_llm_request`` 走 ``call_event_hook``，其 ``assert
iscoroutinefunction(handler)`` ——故这些钩子 handler **必须是 coroutine**
（``async def ... -> None``，**不能 yield**）。要给用户返回消息用
``await event.send(MessageChain().message(text))``（见 ``notifier``）或
``event.stop_event()`` 中止。``yield`` 仅用于 ``@filter.command`` 命令 handler
（走 ``call_handler`` 洋葱模型）。
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
        """插件加载时初始化：构建运行时配置 + 建立独立 sqlite 补充表 + 注册 CronJob + 注册 Web API。

        任一步失败仅记录日志，不阻断插件加载（降级：相应能力不可用，其余正常）。

        ``self.cfg`` = ``CONFIG_DEFAULTS`` ⊕ 插件自有配置文件(``config.json``) ⊕
        AstrBot 开关(``self.config``)。详细配置存插件文件（不被 AstrBot schema 裁剪），
        schema 仅保留开关。
        """
        try:
            from .cost_control.config import (
                CONFIG_DEFAULTS,
                deep_merge,
                load_plugin_config,
                switches_from_config,
            )

            data_dir = str(self.get_data_dir())
            self._data_dir = data_dir
            self.cfg = deep_merge(
                CONFIG_DEFAULTS,
                load_plugin_config(data_dir),
                switches_from_config(getattr(self, "config", None)),
            )
        except Exception as e:
            logger.warning("[cost_control] 加载运行时配置失败，使用默认值: %s", e)
            from .cost_control.config import CONFIG_DEFAULTS

            self.cfg = dict(CONFIG_DEFAULTS)
        try:
            await self.init_store()
        except Exception as e:
            logger.warning("[cost_control] 初始化存储失败: %s", e)
        try:
            await self.register_cron()
        except Exception as e:
            logger.warning("[cost_control] CronJob 注册失败: %s", e)
        try:
            self.register_routes()
        except Exception as e:
            logger.warning("[cost_control] Web API 注册失败: %s", e)

    @filter.on_llm_request(priority=100000)
    async def on_llm_request_head(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """LLM 请求前（最高优先级）：预算硬拦截 + 归因初始快照。

        必须为 coroutine（``call_event_hook`` 的 ``iscoroutinefunction`` assert），
        **不能 yield**。超限时委托 ``apply_over_limit_chain`` 按策略链处理
        （fallback_provider 逐个尝试备用 Provider，或 stop_llm 拦截）。归因初始
        快照仅在未超限（或链路未处理）时记录。异常一律降级放行，绝不阻断主流程。
        """
        # 为本次用户请求生成 request_id（per_request 计费用；function-calling 多步复用）。
        # 独立 try，绝不影响后续预算/归因逻辑。
        try:
            self.ensure_request_id(event)
        except Exception as e:
            logger.warning("[cost_control] request_id 生成失败: %s", e)
        try:
            umo = str(getattr(event, "unified_msg_origin", None) or "")
            model = getattr(req, "model", None) or None
            result = await self.check_budget(umo, model, event=event)
            if result.get("exceeded"):
                try:
                    handled = await self.apply_over_limit_chain(event, req, result)
                except Exception as e:
                    logger.warning("[cost_control] 超限派发失败: %s", e)
                    handled = False
                if handled:
                    return
        except Exception as e:
            logger.warning("[cost_control] 预算检查失败: %s", e)
        # 阶段 3：归因初始快照（head，所有插件执行前；采样在 record_initial_context 内判定）
        try:
            self.record_initial_context(req)
        except Exception as e:
            logger.warning("[cost_control] 归因初始快照失败: %s", e)

    @filter.on_llm_request(priority=-100000)
    async def on_llm_request_tail(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """LLM 请求前（最低优先级）：归因注入差 + 缓存破坏诊断。

        在所有高优先级钩子执行完毕后：与 head 快照对比得到本轮各组件注入量，
        并与上一轮上下文签名对比做缓存破坏四类诊断。coroutine，不 yield。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", None) or "")
            self.pop_injection(req, umo)
            await self.run_cache_diag(req, umo)
        except Exception as e:
            logger.warning("[cost_control] 阶段3请求尾处理失败: %s", e)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        """LLM 响应后：采集 usage + raw cache 写补充表（阶段 1）+
        注入归因填充（阶段 3）+ 缓存命中率告警（阶段 3）。

        coroutine（走 ``call_event_hook``），不 yield。任何异常都被捕获并降级
        （仅记录日志），绝不影响 AstrBot 主流程。
        """
        try:
            record = await self.collect_response(event, resp)
            umo = record.get("umo", "") or ""
            # 阶段 3：把 tail 算出的注入归因挂到本次补充记录
            if umo:
                inj = self.consume_last_injection(umo)
                if inj:
                    record["injection_total"] = inj.get("injected_total")
                    record["attribution"] = inj.get("final")
            await self.save_supplement(record)
            # 阶段 3：缓存命中率低于阈值则告警（带冷却）
            rate, alert = self.check_hit_rate(record)
            if alert:
                await self.notify(
                    event,
                    f"⚠️ 本轮缓存命中率偏低：{rate:.0f}%"
                    "（建议检查 system prompt 稳定性 / 上下文是否被重置）",
                )
        except Exception as e:
            logger.warning("[cost_control] on_llm_response 采集失败: %s", e)
