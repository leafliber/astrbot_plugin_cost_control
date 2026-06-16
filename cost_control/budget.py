"""预算 Mixin。

按会话 / 用户 / 模型 / 全局四维，日 / 月两个时间窗，检查 token 用量是否
超出 ``budgets`` 配置的阈值。超限维度与 ``over_limit_strategies`` 策略链一起返回，
供 ``on_llm_request`` 按 fallback ladder 执行（``fallback_provider`` 逐个尝试备用
Provider，首个成功返回响应；``stop_llm`` 硬拦截）。

可测性设计：窗口计算（``day_window_start`` / ``month_window_start``）与逐维
比较（``check_dimensions``）抽成模块级纯函数，不依赖 astrbot / DB，可单测；
DB 查询在 ``BudgetMixin.check_budget`` 内复用 ``UsageQueryMixin.query_usage``。

时区说明：``ProviderStat.created_at`` 是 UTC aware datetime；预算窗口（日 / 月）
按 **astrbot 主配置的 ``timezone``**（默认 ``Asia/Shanghai``，即用户感知的本地
时间）计算——``resolve_tz`` 从 ``context.get_config()`` 读取该设置。窗口边界在
本地时区算出后转 UTC，再与 ``created_at`` 比较。``refresh_time``（``"HH:MM"``）
按本地时区解释。

阶段 2 实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .attributor import _str_tokens
from .config import (
    CONFIG_DEFAULTS,
    enabled_strategies,
    get_config,
    migrate_legacy_policy,
    normalize_strategy,
)


def resolve_tz(context: Any) -> ZoneInfo:
    """从 astrbot 主配置读取 ``timezone``（IANA 名，默认 ``Asia/Shanghai``）。

    读 / 解析失败一律回退 ``Asia/Shanghai``，保证不抛异常。
    """
    tz_name = "Asia/Shanghai"
    try:
        cfg = context.get_config()
        val = cfg.get("timezone") if hasattr(cfg, "get") else None
        if val:
            tz_name = str(val)
    except Exception:
        pass
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def day_window_start(refresh_time: str, now_utc: datetime, tz: ZoneInfo) -> datetime:
    """计算当前「日预算窗口」的起始 UTC 时刻（按本地时区 ``tz`` 解释 ``refresh_time``）。

    算法：把 ``now_utc`` 转到本地时区，取当日 ``refresh_time`` 时刻；若该时刻尚未
    到达则回退到昨日同时刻；最后转回 UTC（用于与 ``created_at`` 比较）。

    Args:
        refresh_time: ``"HH:MM"`` 格式（按本地时区解释）。解析失败按 ``"00:00"``。
        now_utc: 当前 UTC 时刻（aware）。
        tz: 本地时区（``resolve_tz`` 返回值）。

    Returns:
        窗口起始 UTC datetime（aware）。
    """
    hh, mm = 0, 0
    try:
        parts = str(refresh_time).strip().split(":")
        hh = int(parts[0])
        mm = int(parts[1])
    except (ValueError, IndexError):
        hh, mm = 0, 0
    now_local = now_utc.astimezone(tz)
    start_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start_local > now_local:
        start_local -= timedelta(days=1)
    return start_local.astimezone(UTC)


def month_window_start(now_utc: datetime, tz: ZoneInfo) -> datetime:
    """计算当前「月预算窗口」的起始 UTC 时刻（本地时区本月 1 日 0 点）。"""
    now_local = now_utc.astimezone(tz)
    start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(UTC)


# 维度检查顺序：越局部的越先（单会话 → 单用户 → 单模型 → 全局日 → 全局月）。
_DIM_ORDER = (
    "per_session_daily",
    "per_user_daily",
    "per_model_daily",
    "global_daily",
    "global_monthly",
)


def check_dimensions(
    used: dict[str, int],
    limits: dict[str, int],
) -> dict[str, Any]:
    """逐维比较用量与阈值，返回首个超限维度（纯函数）。

    ``limit <= 0`` 视为「不限制」，跳过该维度。

    Args:
        used: 各维度当前周期已用 token 总数。
        limits: 各维度上限（来自 ``budgets`` 配置）。

    Returns:
        ``{"exceeded": bool, "dim": str | None, "limit": int, "used": int}``。
    """
    for dim in _DIM_ORDER:
        limit = int(limits.get(dim, 0) or 0)
        if limit <= 0:
            continue
        u = int(used.get(dim, 0) or 0)
        if u >= limit:
            return {"exceeded": True, "dim": dim, "limit": limit, "used": u}
    return {"exceeded": False, "dim": None, "limit": 0, "used": 0}


def total_tokens(usage: dict[str, Any]) -> int:
    """把三类 token 聚合为总数（纯函数）。"""
    return (
        int(usage.get("token_input_other", 0) or 0)
        + int(usage.get("token_input_cached", 0) or 0)
        + int(usage.get("token_output", 0) or 0)
    )


def truncate_contexts(contexts: Any, token_limit: int) -> list[Any]:
    """保留最近的对话历史，使估算 token 总数不超过 ``token_limit``（纯函数）。

    ``token_limit <= 0`` 时原样返回（转 list）；否则从末尾向前累计，保留尽可能多
    的近期上下文，确保不超额。估算用 :func:`attributor._str_tokens`（粗略）。
    """
    items = list(contexts or [])
    if token_limit <= 0:
        return items
    kept: list[Any] = []
    total = 0
    for c in reversed(items):
        est = _str_tokens(str(c))
        if kept and total + est > token_limit:
            break
        kept.append(c)
        total += est
    kept.reverse()
    return kept


class BudgetMixin:
    """四维预算阈值检查的 Mixin。

    依赖兄弟 ``UsageQueryMixin.query_usage``（由 ``Main`` 多继承提供）。
    """

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    # 兄弟 Mixin 提供，这里仅声明以通过 mypy。
    query_usage: Any
    save_supplement: Any

    def get_budgets(self) -> dict[str, int]:
        """返回生效的预算阈值 dict（合并用户配置与默认值）。"""
        budgets = get_config(getattr(self, "config", None), "budgets", {}) or {}
        defaults: dict[str, int] = dict(get_config(None, "budgets", {}) or {})
        merged: dict[str, int] = dict(defaults)
        if isinstance(budgets, dict):
            for dim in _DIM_ORDER:
                val = budgets.get(dim)
                if val is None:
                    continue
                try:
                    merged[dim] = int(val)
                except (TypeError, ValueError):
                    continue
        return merged

    def get_over_limit_strategies(self) -> list[dict[str, Any]]:
        """返回生效的超限策略链（规范化 ``list[dict]``）。

        优先用配置中的 ``over_limit_strategies``（list）；若配置未显式提供该 key
        但存在遗留的 ``over_limit_policy``（单对象），则迁移为 1 元素列表；
        都没有则返回 :data:`CONFIG_DEFAULTS.over_limit_strategies` 默认值。
        每条策略经 :func:`normalize_strategy` 规范化（字段齐全、action 合法）。
        """
        cfg = getattr(self, "config", None)
        if isinstance(cfg, dict) and "over_limit_strategies" in cfg:
            raw = cfg.get("over_limit_strategies")
            if isinstance(raw, list):
                return [normalize_strategy(s) for s in raw]
        if isinstance(cfg, dict) and "over_limit_policy" in cfg:
            migrated = migrate_legacy_policy(cfg.get("over_limit_policy"))
            if migrated:
                return migrated
        default = CONFIG_DEFAULTS.get("over_limit_strategies", [])
        return [normalize_strategy(s) for s in (default or [])]

    async def check_budget(self, umo: str, model: str | None) -> dict[str, Any]:
        """检查指定会话 + 模型当前是否超出任一预算维度。

        Args:
            umo: 会话标识（unified message origin）。
            model: 当前请求所用模型名（用于 ``per_model_daily``）。

        Returns:
            ``{"exceeded": bool, "dim": str | None, "limit": int, "used": int,
            "strategies": list}``。任何异常都降级为「未超限」，绝不阻断主流程。
        """
        strategies = self.get_over_limit_strategies()
        zero: dict[str, Any] = {
            "exceeded": False,
            "dim": None,
            "limit": 0,
            "used": 0,
            "strategies": strategies,
        }
        try:
            limits = self.get_budgets()
            if not any(int(limits.get(d, 0) or 0) > 0 for d in _DIM_ORDER):
                return zero  # 未配置任何预算，直接放行

            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "config", None), "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)

            # 单会话 / 单用户：阶段 2 均按 umo 聚合（ProviderStat 无独立 user_id）。
            session_usage = await self.query_usage(umo=umo, start=d_start)
            session_total = total_tokens(session_usage)

            model_total = session_total
            if model:
                model_usage = await self.query_usage(model=model, start=d_start)
                model_total = total_tokens(model_usage)

            global_day_total = total_tokens(await self.query_usage(start=d_start))
            global_month_total = total_tokens(await self.query_usage(start=m_start))

            used = {
                "per_session_daily": session_total,
                "per_user_daily": session_total,  # 阶段 2 退化为 umo 维度
                "per_model_daily": model_total,
                "global_daily": global_day_total,
                "global_monthly": global_month_total,
            }
            result = check_dimensions(used, limits)
            result["strategies"] = strategies
            return result
        except Exception:
            return zero

    # ===== 超限策略链执行（fallback ladder） =====
    # 超限时按 strategies 顺序求值：fallback_provider 逐个尝试备用 Provider，
    # 首个成功即返回其响应；stop_llm 硬拦截终止。链路耗尽兜底拦截。
    # 全程 try/except，绝不抛异常（on_llm_request_head 另有一层兜底）。

    async def apply_over_limit_chain(
        self,
        event: Any,
        req: Any,
        result: dict[str, Any],
        strategies: list[dict[str, Any]] | None,
    ) -> bool:
        """超限时按策略链顺序求值并执行，命中即处理。返回是否已处理。

        ``fallback_provider``：按 ``provider_ids`` 逐个尝试，首个成功返回响应即
        完成；该条全失败则继续下一条策略。``stop_llm``：硬拦截 + 文案，终止链路。
        链路无 ``stop_llm`` 且 fallback 全失败 → 兜底拦截。任何异常兜底拦截。
        """
        from astrbot import logger

        chain = enabled_strategies(strategies) if strategies else []
        if not chain:
            chain = [normalize_strategy({})]  # 兜底：单条 stop_llm
        try:
            for s in chain:
                action = s.get("action")
                if action == "fallback_provider":
                    if await self._try_fallback(event, req, s):
                        return True
                    # 该 fallback 全失败 → 继续下一条策略
                elif action == "stop_llm":
                    await self._do_stop(event, result, s)
                    return True
            # 链路无 stop_llm 且 fallback 全失败 → 兜底拦截
            await self._do_stop(event, result, {})
            return True
        except Exception as e:
            logger.warning("[cost_control] 策略链执行异常，兜底拦截: %s", e)
            try:
                await self._do_stop(event, result, {})
            except Exception:
                pass
            return True

    async def _try_fallback(
        self,
        event: Any,
        req: Any,
        strategy: dict[str, Any],
    ) -> bool:
        """尝试单条 ``fallback_provider`` 策略：遍历 ``provider_ids`` 逐个调用。

        首个成功（解析到 Provider 且 ``text_chat`` 返回非空）即 ``stop_event`` 并
        发送响应，返回 True；全部失败返回 False（调用方继续下一条策略）。
        """
        from astrbot import logger

        pids = [str(p) for p in (strategy.get("provider_ids") or []) if str(p).strip()]
        token_limit = int(strategy.get("token_limit", 0) or 0)
        for pid in pids:
            prov = None
            try:
                getter = getattr(self.context, "get_provider_by_id", None)
                prov = getter(pid) if getter else None
            except Exception:
                prov = None
            if prov is None:
                logger.warning("[cost_control] fallback provider 未找到: %s", pid)
                continue
            try:
                resp = await self._call_fallback(prov, req, token_limit)
            except Exception as e:
                logger.warning("[cost_control] fallback provider %s 调用失败: %s", pid, e)
                continue
            text = ""
            try:
                text = (getattr(resp, "completion_text", None) or "").strip()
            except Exception:
                text = ""
            if not text:
                continue
            # 记录 fallback usage（弥补绕过 on_llm_response 的统计缺口；失败不阻断响应）
            try:
                await self._record_fallback(event, prov, pid, resp)
            except Exception as e:
                logger.warning("[cost_control] fallback 记录失败: %s", e)
            try:
                event.stop_event()
            except Exception:
                pass
            try:
                from astrbot.api.event import MessageChain

                await event.send(MessageChain().message(text))
            except Exception as e:
                logger.warning("[cost_control] fallback 响应发送失败: %s", e)
            return True
        return False

    async def _call_fallback(self, prov: Any, req: Any, token_limit: int) -> Any:
        """用备用 Provider 执行本轮请求。

        先尝试带 ``contexts``（按 ``token_limit`` 截断历史）；若该 Provider 的
        ``text_chat`` 签名不接受 ``contexts``（抛 ``TypeError``），退化为仅
        ``prompt`` + ``system_prompt``。
        """
        prompt = getattr(req, "prompt", "") or ""
        system_prompt = getattr(req, "system_prompt", "") or ""
        contexts = truncate_contexts(getattr(req, "contexts", None), token_limit)
        try:
            return await prov.text_chat(
                prompt=prompt, system_prompt=system_prompt, contexts=contexts
            )
        except TypeError:
            return await prov.text_chat(prompt=prompt, system_prompt=system_prompt)

    async def _record_fallback(
        self,
        event: Any,
        prov: Any,
        pid: str,
        resp: Any,
    ) -> None:
        """把 fallback 调用的 usage 落补充表（保持成本统计完整）。

        token 三类取自 ``resp.usage``（TokenUsage），cache 字段经
        :func:`supplement._extract_cache` 解析；provider_id/model 取自 ``prov.meta()``
        （比配置串更权威）。失败由调用方 try/except 兜住，仅记日志。
        """
        from .supplement import _extract_cache

        usage = getattr(resp, "usage", None)
        token_input_other = int(getattr(usage, "input_other", 0) or 0)
        token_input_cached = int(getattr(usage, "input_cached", 0) or 0)
        token_output = int(getattr(usage, "output", 0) or 0)
        raw = getattr(resp, "raw_completion", None)
        cache_creation, cache_read, raw_usage = _extract_cache(raw)

        provider_id = str(pid)
        provider_model = ""
        try:
            meta = prov.meta()
            provider_id = str(getattr(meta, "id", pid) or pid)
            provider_model = str(getattr(meta, "model", "") or "")
        except Exception:
            pass

        umo = str(
            getattr(event, "unified_msg_origin", None) or getattr(event, "session_id", None) or ""
        )
        record = {
            "umo": umo,
            "provider_id": provider_id,
            "provider_model": provider_model,
            "conversation_id": getattr(event, "conversation_id", None),
            "token_input_other": token_input_other,
            "token_input_cached": token_input_cached,
            "token_output": token_output,
            "cache_creation": cache_creation,
            "cache_read": cache_read,
            "raw_usage": raw_usage,
            "response_id": getattr(resp, "id", None),
            "created_at": datetime.now(UTC),
        }
        await self.save_supplement(record)

    async def _do_stop(
        self,
        event: Any,
        result: dict[str, Any],
        strategy: dict[str, Any],
    ) -> None:
        """硬拦截：发送文案 + ``stop_event``。文案优先用策略自定义 ``message``。"""
        from astrbot import logger
        from astrbot.api.event import MessageChain

        msg = str(strategy.get("message", "") or "").strip()
        if not msg:
            msg = (
                f"⏸ 已超出预算（{result.get('dim')}）："
                f"用 {result.get('used')} / 限 {result.get('limit')} token"
            )
        try:
            event.stop_event()
        except Exception:
            pass
        try:
            await event.send(MessageChain().message(msg))
        except Exception as e:
            logger.warning("[cost_control] 超限提示发送失败: %s", e)
