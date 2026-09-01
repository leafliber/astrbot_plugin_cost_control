"""补充采集 Mixin。

在 ``on_llm_response`` 钩子里解析 ``LLMResponse.usage``（``TokenUsage``）与
``LLMResponse.raw_completion`` 中的 cache 字段（如 Anthropic 的
``cache_creation_input_tokens`` / ``cache_read_input_tokens``），补充原生
``ProviderStat`` 未记录的信息，交由 ``StoreMixin.save_supplement`` 写入独立库。

注意：``TokenUsage`` 仅含 ``input_other`` / ``input_cached`` / ``output``，
``cache_creation`` 需从 ``raw_completion`` 解析。不同 provider 的 raw 类型不同
（Anthropic Message / OpenAI ChatCompletion / OpenAI Response（Responses API）/
Google GenerateContentResponse），cache 字段命名各异，按 duck-typing 兼容，
解析失败降级为 None。

计费上下文（service_tier / 1h 缓存写标志）一律取**响应侧**：Anthropic
``usage.cache_creation.ephemeral_1h_input_tokens`` 与 OpenAI
``service_tier``。AstrBot 4.25.5 的 ``ProviderRequest`` 没有
``extra_body`` / ``headers`` 字段，请求侧提取链路是永不触发的死代码，已删除。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

# 生产环境经 root logger 汇入 astrbot/loguru；测试经 caplog 可捕获。
logger = logging.getLogger("cost_control.supplement")


def _extract_cache(
    raw: Any,
) -> tuple[int | None, int | None, dict[str, Any] | None, int | None]:
    """从 raw_completion 提取 cache 细分与原始 usage。

    按 provider 类型 duck-typing，逐个尝试已知字段路径；全部失败返回
    ``(None, None, raw_usage, None)``。

    注意（已对照 AstrBot 4.25.5 ``openai_source._extract_usage``）：
    ``TokenUsage.input_other = prompt_tokens - cached_tokens`` 已包含缓存
    未命中的输入 token。DeepSeek 系（``prompt_cache_hit_tokens`` /
    ``prompt_cache_miss_tokens``）的 miss token 同样已计入 ``input_other``
    （其响应无 ``prompt_tokens_details``，AstrBot 取不到 hit 数）——因此
    ``prompt_cache_miss_tokens`` **不得**映射为 ``cache_creation``，否则同一段
    token 按 input + cache_creation 收两遍（≈2 倍；DeepSeek 缓存本无写入费）。

    Returns:
        ``(cache_creation, cache_read, raw_usage_dict, cache_creation_1h)``。
        ``cache_creation_1h`` 来自 Anthropic ``usage.cache_creation.
        ephemeral_1h_input_tokens``（1h 缓存写，2× 价）；无该细分返回 None。
    """
    if raw is None:
        return None, None, None, None

    usage = getattr(raw, "usage", None)  # OpenAI / Anthropic
    usage_meta = getattr(raw, "usage_metadata", None)  # Google

    raw_usage_dict: dict[str, Any] | None = None
    try:
        if usage is not None and hasattr(usage, "model_dump"):
            raw_usage_dict = usage.model_dump()
    except Exception:
        raw_usage_dict = None

    cache_creation: int | None = None
    cache_read: int | None = None
    cache_creation_1h: int | None = None

    try:
        if usage is not None:
            # Anthropic 风格：原生 cache_creation / cache_read
            cc = getattr(usage, "cache_creation_input_tokens", None)
            cr = getattr(usage, "cache_read_input_tokens", None)
            if cc is not None:
                cache_creation = int(cc)
            if cr is not None:
                cache_read = int(cr)
            # Anthropic 缓存写细分：usage.cache_creation.ephemeral_1h_input_tokens
            # （CacheCreation 对象；SDK >= 0.62）。请求侧 ProviderRequest 无
            # headers/extra_body 可判定 1h（4.25.5 实测），响应侧细分是唯一信号。
            ccd = getattr(usage, "cache_creation", None)
            if ccd is not None:
                e1h = getattr(ccd, "ephemeral_1h_input_tokens", None)
                if e1h is None and isinstance(ccd, dict):
                    e1h = ccd.get("ephemeral_1h_input_tokens")
                if e1h is not None:
                    cache_creation_1h = int(e1h)
            # OpenAI 风格：prompt_tokens_details.cached_tokens
            if cache_read is None:
                ptd = getattr(usage, "prompt_tokens_details", None)
                cached = getattr(ptd, "cached_tokens", None) if ptd is not None else None
                if cached is not None:
                    cache_read = int(cached)
            # OpenAI Responses API 风格：input_tokens_details.cached_tokens
            # （openai_responses 供应商的 raw_completion 是 Response 对象）
            if cache_read is None:
                itd = getattr(usage, "input_tokens_details", None)
                cached = getattr(itd, "cached_tokens", None) if itd is not None else None
                if cached is not None:
                    cache_read = int(cached)
            # DeepSeek 等扩展字段（部分 provider 直接挂在 usage 上）。
            # 只取 hit 作 cache_read；miss 留在 input_other（见 docstring）。
            if cache_read is None:
                dsh = getattr(usage, "prompt_cache_hit_tokens", None)
                if dsh is not None:
                    cache_read = int(dsh)
        # Google 风格：usage_metadata.cached_content_token_count
        if usage_meta is not None and cache_read is None:
            ccc = getattr(usage_meta, "cached_content_token_count", None)
            if ccc is not None:
                cache_read = int(ccc)
    except Exception:
        pass

    return cache_creation, cache_read, raw_usage_dict, cache_creation_1h


def _safe_sender_id(event: Any) -> str | None:
    """从 ``AstrMessageEvent`` 读取发送者 user_id（健壮封装，绝不抛异常）。

    AstrBot 4.25.5 暴露 ``event.get_sender_id()``（返回 ``message_obj.sender.user_id``，
    平台统一处理为 str；不同平台可能是 QQ 号 / 微信 openid / 钉钉 staff_id）。读不到
    或抛异常一律返回 ``None``（按用户 override 在 user_id 为空时自然不会命中）。
    """
    try:
        fn = getattr(event, "get_sender_id", None)
        if not callable(fn):
            return None
        v = fn()
        if v is None:
            return None
        s = str(v).strip()
        return s or None
    except Exception:
        return None


def _read_request_id(event: Any) -> str | None:
    """读回 ``on_llm_request_head`` 挂到 event 上的用户请求 ID（健壮只读）。

    一次用户请求（pipeline）在 function-calling 多步场景下触发多次 LLM 调用，
    head 钩子为同一 event 生成一次 ``_cost_control_request_id``。读不到（插件
    中途加载、event 未经过 head、或 AstrBot 每次 clone 新 event）返回 ``None``。
    """
    try:
        rid = getattr(event, "_cost_control_request_id", None)
        if rid is None:
            return None
        s = str(rid).strip()
        return s or None
    except Exception:
        return None


def _extract_billing_context(
    raw: Any,
    cache_creation_1h: int | None = None,
) -> dict[str, Any]:
    """从**响应侧**提取计费上下文，归一化到 ``{"params": {...}}`` 形态。

    计费字段全部来自响应（权威且可得）：

    - ``service_tier``：OpenAI ``ChatCompletion.service_tier``（attr 或 dict）；
    - ``cache_ttl_1h``：Anthropic ``usage.cache_creation.ephemeral_1h_input_tokens
      > 0`` 时置 True（1h 缓存写，2× 价）。

    请求侧（``ProviderRequest.extra_body`` / ``.headers``）在 AstrBot 4.25.5
    并不存在这两个字段（已核对 ``core/provider/entities.py``），旧版从这里提取
    的链路是永不触发的死代码，已删除；``expr_eval.param()`` 读取口径不变。
    全异常吞掉返回 ``{}``。
    """
    try:
        params: dict[str, Any] = {}
        st = getattr(raw, "service_tier", None)
        if st is None and isinstance(raw, dict):
            st = raw.get("service_tier")
        if st is not None:
            s = str(st).strip()
            if s:
                params["service_tier"] = s
        if cache_creation_1h is not None and int(cache_creation_1h) > 0:
            params["cache_ttl_1h"] = True
        if not params:
            return {}
        out = {"params": params}
        logger.debug(
            "[cost_control] 计费上下文字段 params_keys=%s",
            sorted(params),
        )
        return out
    except Exception:
        return {}


class SupplementMixin:
    """``on_llm_response`` 钩子补充采集 usage + cache 字段的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any

    async def collect_response(
        self,
        event: AstrMessageEvent,
        resp: Any,
    ) -> dict[str, Any]:
        """从 LLM 响应解析 usage 与 raw cache 字段，组装补充记录 dict。

        Args:
            event: 触发响应的 ``AstrMessageEvent``。
            resp: ``LLMResponse`` 对象，含 ``usage`` 与 ``raw_completion``。

        Returns:
            包含 umo / provider_id / provider_model / token 三类 /
            cache_creation / cache_read / raw_usage / response_id / created_at
            / cost_amount / currency_symbol 的补充记录 dict。
        """
        from .cost import TieredExprEvaluationError, compute_cost_with_currency

        usage = getattr(resp, "usage", None)
        token_input_other = int(getattr(usage, "input_other", 0) or 0)
        token_input_cached = int(getattr(usage, "input_cached", 0) or 0)
        token_output = int(getattr(usage, "output", 0) or 0)

        raw = getattr(resp, "raw_completion", None)
        cache_creation, cache_read, raw_usage, cache_creation_1h = _extract_cache(raw)

        umo = self._get_umo(event)
        conversation_id = self._get_conversation_id(event)
        provider_id, provider_model = await self._get_provider_info(umo, raw)
        response_id = getattr(resp, "id", None)
        user_id = _safe_sender_id(event)
        request_id = _read_request_id(event)

        # 计费上下文（service_tier / 1h 缓存判定），供 per_tier / tiered_expr 求值。
        # 全部来自响应侧（ProviderRequest 无 extra_body/headers 可提取，见
        # _extract_billing_context docstring）。
        billing_context = _extract_billing_context(raw, cache_creation_1h)
        bc_params = billing_context.get("params") if isinstance(billing_context, dict) else None
        bc_params = bc_params if isinstance(bc_params, dict) else {}
        # 1h 缓存写：优先 Anthropic 响应侧 ephemeral_1h 细分；provider 未返回
        # 细分但计费上下文标记了 1h 时，退化为整段 cache_creation 按 1h 计。
        cc1h = int(cache_creation_1h or 0)
        if cc1h <= 0 and bc_params.get("cache_ttl_1h"):
            cc1h = int(cache_creation or 0)

        # 固化原始货币成本金额与符号（展示时按当前汇率换算到主货币）。
        created = datetime.now(UTC)
        usage_dict = {
            "token_input_other": token_input_other,
            "token_input_cached": token_input_cached,
            "token_output": token_output,
            "cache_creation": cache_creation,
            "cache_creation_1h": cc1h,
            "billing_context": billing_context,
            "created_at": created,
        }
        raw_cost: float | None
        try:
            raw_cost, cur = compute_cost_with_currency(
                usage_dict, provider_id, provider_model, self.get_pricing()
            )
        except TieredExprEvaluationError:
            # 表达式失败不得固化为 0；保留 NULL 供后续修正规则后重算/回填。
            raw_cost, cur = None, "USD"
        except Exception as e:
            # 其余计算失败同样不得静默固化为 0 成本（资损防线）。
            logging.getLogger(__name__).warning(
                "[cost_control] 成本计算异常，保留 NULL 待回填: %s", e, exc_info=True
            )
            raw_cost, cur = None, "USD"
        # tiered_expr 命中的阶梯名（tier() 回填到 usage_dict），供审计与调试；
        # 求值失败时把失败类别固化进 billing_context.params（绝不静默归零）。
        matched_tier = usage_dict.pop("_matched_tier", None)
        pricing_period_id = usage_dict.pop("_pricing_period_id", None)
        pricing_period_name = usage_dict.pop("_pricing_period_name", None)
        expr_error = usage_dict.pop("_expr_error", None)
        if expr_error and isinstance(billing_context, dict):
            billing_context.setdefault("params", {})["expr_error"] = str(expr_error)

        return {
            "umo": umo,
            "provider_id": provider_id or "",
            "provider_model": provider_model,
            "conversation_id": conversation_id,
            "token_input_other": token_input_other,
            "token_input_cached": token_input_cached,
            "token_output": token_output,
            "cache_creation": cache_creation,
            "cache_read": cache_read,
            "raw_usage": raw_usage,
            "response_id": response_id,
            "request_id": request_id,
            "user_id": user_id,
            "billing_context": billing_context or None,
            "matched_tier": matched_tier,
            "pricing_period_id": pricing_period_id,
            "pricing_period_name": pricing_period_name,
            "cost_amount": round(raw_cost, 6) if raw_cost is not None else None,
            "currency_symbol": cur,
            "created_at": created,
        }

    def _get_umo(self, event: Any) -> str:
        return str(
            getattr(event, "unified_msg_origin", None) or getattr(event, "session_id", None) or ""
        )

    def _get_conversation_id(self, event: Any) -> str | None:
        # on_llm_response 阶段 event 不直接暴露 conversation_id；留空，
        # 后续按 umo + created_at 与 ProviderStat 关联。
        cid = getattr(event, "conversation_id", None)
        return str(cid) if cid else None

    async def _get_provider_info(
        self,
        umo: str | None,
        raw: Any,
    ) -> tuple[str | None, str | None]:
        provider_id: str | None = None
        model: str | None = None
        try:
            prov = self.context.get_using_provider(umo)
            if prov is not None:
                meta = prov.meta()
                provider_id = meta.id
                model = meta.model
        except Exception:
            pass
        if model is None and raw is not None:
            try:
                model = getattr(raw, "model", None)
            except Exception:
                model = None
        return provider_id, model

    def ensure_request_id(self, event: Any) -> None:
        """为一次用户请求（pipeline）生成 request_id 并挂到 event（幂等、绝不抛异常）。

        在 ``on_llm_request_head``（最高优先级）调用：若 event 还没有
        ``_cost_control_request_id`` 则生成 ``cc_<16hex>`` 并 setattr。后续同 event
        的多次 LLM 调用（function-calling 多步）复用同一值，供 per_request 按请求计数。

        假设：AstrBot pipeline 在一次用户消息内复用同一 event 对象。若实际每次 clone
        新 event，request_id 退化为每次调用各一个（等同于 per_turn）——降级可接受。
        """
        try:
            if getattr(event, "_cost_control_request_id", None):
                return
            rid = f"cc_{uuid.uuid4().hex[:16]}"
            try:
                setattr(event, "_cost_control_request_id", rid)
            except Exception:
                pass
        except Exception:
            pass
