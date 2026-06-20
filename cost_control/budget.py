"""预算 Mixin。

按会话 / 用户 / 模型 / 全局四维，日 / 月两个时间窗，检查 token 用量 / 花费是否
超出 ``budgets`` 与 ``budgets_cost`` 配置的阈值。

**评估顺序（重要）**：

1. **局部阈值**（``budget_overrides``）：按配置顺序扫每条启用的规则，第一条匹配
   当前请求（umo / provider / user）的规则生效。若其 token_limit 或 cost_limit
   任一超限 → 立即返回 ``{exceeded: True, dim: "override:<idx>", ...}``。
2. **全局 5 维**（``budgets`` / ``budgets_cost``）：未匹配 override 或 override 未
   超限时按 ``_DIM_ORDER`` 顺序逐维比较。
3. **默认处理**：全局超限时使用 ``default_on_exceeded``（``"stop" | "fallback" |
   "warn"``）；override 超限时直接用 override 自身的 ``on_exceeded``。

执行动作（``apply_over_limit_chain``）：
- ``stop`` → 发送文案 + stop_event
- ``fallback`` → 遍历 override 自身的 ``fallback_provider_ids`` 逐个尝试
- ``warn`` → 仅发警告，不 stop_event（请求继续走原 Provider）

降级：所有路径 try/except 兜底，绝不阻断主流程。

时区：``ProviderStat.created_at`` 是 UTC aware；预算窗口按 astrbot 主配置
``timezone``（默认 ``Asia/Shanghai``）解释，详见 :func:`resolve_tz`。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .attributor import _str_tokens
from .config import (
    enabled_overrides,
    get_config,
    get_pricing,
)
from .cost import compute_cost_value


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
    """逐维比较用量与阈值，返回首个超限维度（纯函数）。"""
    for dim in _DIM_ORDER:
        limit = int(limits.get(dim, 0) or 0)
        if limit <= 0:
            continue
        u = int(used.get(dim, 0) or 0)
        if u >= limit:
            return {"exceeded": True, "dim": dim, "limit": limit, "used": u}
    return {"exceeded": False, "dim": None, "limit": 0, "used": 0}


def check_dimensions_dual(
    used_t: Mapping[str, float],
    used_c: Mapping[str, float],
    limits_t: Mapping[str, float],
    limits_c: Mapping[str, float],
) -> dict[str, Any]:
    """逐维比较 token / cost 用量与阈值，返回首个超限维度 + 指标（纯函数）。

    按 :data:`_DIM_ORDER` 逐维；某维 token 或 cost 任一超限即返回。同维两者都超
    优先报 token（更直观）。``limit <= 0`` 视为该指标不限，跳过。
    """
    for dim in _DIM_ORDER:
        lt = float(limits_t.get(dim, 0) or 0)
        lc = float(limits_c.get(dim, 0) or 0)
        if lt <= 0 and lc <= 0:
            continue
        ut = float(used_t.get(dim, 0) or 0)
        uc = float(used_c.get(dim, 0) or 0)
        if lt > 0 and ut >= lt:
            return {"exceeded": True, "dim": dim, "metric": "token", "limit": lt, "used": ut}
        if lc > 0 and uc >= lc:
            return {"exceeded": True, "dim": dim, "metric": "cost", "limit": lc, "used": uc}
    return {"exceeded": False, "dim": None, "metric": None, "limit": 0.0, "used": 0.0}


def _groups_cost(
    groups: list[dict[str, Any]],
    pricing: dict[str, dict[str, float]],
) -> float:
    """把 ``query_usage_grouped(by="model")`` 结果按模型求和花费（纯函数）。"""
    total = 0.0
    for g in groups or []:
        try:
            total += compute_cost_value(g, g.get("key"), pricing)
        except Exception:
            continue
    return round(total, 6)


def total_tokens(usage: dict[str, Any]) -> int:
    """把三类 token 聚合为总数（纯函数）。"""
    return (
        int(usage.get("token_input_other", 0) or 0)
        + int(usage.get("token_input_cached", 0) or 0)
        + int(usage.get("token_output", 0) or 0)
    )


def truncate_contexts(contexts: Any, token_limit: int) -> list[Any]:
    """保留最近的对话历史，使估算 token 总数不超过 ``token_limit``（纯函数）。"""
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


# ===== override 纯函数 =====


def match_override(
    ov: dict[str, Any],
    umo: str,
    user_id: str | None,
    provider_id: str | None,
) -> str | None:
    """判断 override 是否匹配当前请求上下文，返回命中的目标值；不匹配返回 ``None``。

    ``target_type``：
    - ``"umo"`` —— 匹配 ``umo == ov.target_value``
    - ``"provider"`` —— 匹配 ``provider_id == ov.target_value``
    - ``"user"`` —— 匹配 ``user_id == ov.target_value``（``user_id`` 为空时永远不命中）
    """
    if not isinstance(ov, dict):
        return None
    tt = str(ov.get("target_type") or "")
    tv = str(ov.get("target_value") or "")
    if not tv:
        return None
    if tt == "umo":
        return tv if umo == tv else None
    if tt == "provider":
        if not provider_id:
            return None
        return tv if provider_id == tv else None
    if tt == "user":
        if not user_id:
            return None
        return tv if user_id == tv else None
    return None


def default_on_exceeded(cfg: Any) -> str:
    """读取 ``default_on_exceeded``（默认 ``"stop"``；非 stop/fallback/warn 兜底 stop）。"""
    if isinstance(cfg, dict):
        v = str(cfg.get("default_on_exceeded") or "").strip().lower()
        if v in ("stop", "fallback", "warn"):
            return v
    return "stop"


def get_fallback_providers(cfg: Any) -> list[dict[str, Any]]:
    """读取 ``fallback_providers`` 列表（仅返回启用的，规范化字段）。"""
    from .config import enabled_fallback_providers

    raw = None
    if isinstance(cfg, dict):
        raw = cfg.get("fallback_providers")
    return enabled_fallback_providers(raw)


def get_budget_overrides(cfg: Any) -> list[dict[str, Any]]:
    """读取 ``budget_overrides``（仅返回启用的，规范化字段；保持原顺序）。"""
    raw = None
    if isinstance(cfg, dict):
        raw = cfg.get("budget_overrides")
    return enabled_overrides(raw)


class BudgetMixin:
    """四维预算阈值检查的 Mixin。

    依赖兄弟 ``UsageQueryMixin.query_usage`` / ``StoreMixin.query_user_token_total``
    / ``StoreMixin.query_user_cost_total``（由 ``Main`` 多继承提供）。
    """

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    # 兄弟 Mixin 提供。
    query_usage: Any
    query_usage_grouped: Any
    query_supplements: Any
    query_user_token_total: Any
    query_user_cost_total: Any
    get_pricing: Any
    save_supplement: Any

    def get_budgets(self) -> dict[str, int]:
        """返回生效的预算阈值 dict（合并用户配置与默认值）。"""
        budgets = get_config(getattr(self, "cfg", None), "budgets", {}) or {}
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

    def get_budgets_cost(self) -> dict[str, float]:
        """返回生效的花费预算阈值 dict（USD float，合并用户配置与默认值）。"""
        budgets_cost = get_config(getattr(self, "cfg", None), "budgets_cost", {}) or {}
        defaults: dict[str, float] = dict(get_config(None, "budgets_cost", {}) or {})
        merged: dict[str, float] = dict(defaults)
        if isinstance(budgets_cost, dict):
            for dim in _DIM_ORDER:
                val = budgets_cost.get(dim)
                if val is None:
                    continue
                try:
                    merged[dim] = float(val)
                except (TypeError, ValueError):
                    continue
        return merged

    async def _provider_id_for_request(
        self,
        event: Any,
        umo: str,
    ) -> str | None:
        """尽力从 event / context 中获取当前请求所用 provider_id（用于 override 匹配）。

        失败兜底 ``None``（target_type=provider 的 override 不会命中，不阻断主流程）。
        """
        try:
            getter = getattr(self.context, "get_using_provider", None)
            if not callable(getter):
                return None
            prov = getter(umo)
            if prov is None:
                return None
            meta = getattr(prov, "meta", None)
            if not callable(meta):
                return None
            return str(getattr(meta(), "id", "") or "") or None
        except Exception:
            return None

    async def _user_id_for_request(self, event: Any) -> str | None:
        """从 event 读取 user_id（封装 :func:`supplement._safe_sender_id`）。"""
        from .supplement import _safe_sender_id

        return _safe_sender_id(event)

    async def _override_used(
        self,
        ov: dict[str, Any],
        metric: str,
        umo: str,
        model: str | None,
        user_id: str | None,
        provider_id: str | None,
        d_start: datetime,
        pricing: dict[str, dict[str, float]],
    ) -> float:
        """按 override target 聚合当前周期的 ``metric`` 用量（token 数 / USD 花费）。"""
        tt = str(ov.get("target_type") or "")
        tv = str(ov.get("target_value") or "")
        if metric == "token":
            if tt == "umo":
                return float(total_tokens(await self.query_usage(umo=tv, start=d_start)))
            if tt == "provider":
                return float(
                    total_tokens(
                        await self.query_usage(provider=tv, start=d_start)
                    )
                )
            if tt == "user":
                if not user_id:
                    return 0.0
                return float(await self.query_user_token_total(tv, d_start))
            return 0.0
        # cost
        if tt == "umo":
            return _groups_cost(
                await self.query_usage_grouped(by="model", umo=tv, start=d_start),
                pricing,
            )
        if tt == "provider":
            rows = await self.query_usage_grouped(
                by="model", provider=tv, start=d_start
            )
            return _groups_cost(rows, pricing)
        if tt == "user":
            if not user_id:
                return 0.0
            return float(await self.query_user_cost_total(tv, d_start, pricing))
        return 0.0

    async def check_budget(
        self,
        umo: str,
        model: str | None,
        event: Any | None = None,
    ) -> dict[str, Any]:
        """检查指定请求是否超出任一预算（override 优先于全局 5 维）。

        Args:
            umo: 会话标识。
            model: 当前请求所用模型名（用于 ``per_model_daily``）。
            event: 原始 ``AstrMessageEvent``（可选；用于读 user_id / provider_id，
                供 override 匹配）。不传则按 provider/user 类型的 override 退化为
                不命中（umo 仍可命中）。

        Returns:
            ``{"exceeded": bool, "dim": str|None, "metric": "token"|"cost"|None,
            "limit": float, "used": float, "on_exceeded": str,
            "fallback_provider_ids": list[str], "fallback_token_limit": int,
            "stop_message": str}``。任何异常都降级为「未超限」，绝不阻断主流程。
        """
        cfg = getattr(self, "cfg", None)
        zero: dict[str, Any] = {
            "exceeded": False,
            "dim": None,
            "metric": None,
            "limit": 0.0,
            "used": 0.0,
            "on_exceeded": default_on_exceeded(cfg),
            "fallback_provider_ids": [],
            "fallback_token_limit": 0,
            "stop_message": "",
            "rule_idx": -1,
        }
        try:
            limits_t = self.get_budgets()
            limits_c = self.get_budgets_cost()
            overrides = get_budget_overrides(cfg)
            has_global_token = any(float(limits_t.get(d, 0) or 0) > 0 for d in _DIM_ORDER)
            has_global_cost = any(float(limits_c.get(d, 0) or 0) > 0 for d in _DIM_ORDER)
            if (
                not has_global_token
                and not has_global_cost
                and not overrides
            ):
                return zero

            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(cfg, "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)

            user_id = await self._user_id_for_request(event) if event is not None else None
            provider_id = (
                await self._provider_id_for_request(event, umo)
                if event is not None
                else None
            )

            # ===== 1. 局部阈值（override）=====
            if overrides:
                pricing = self.get_pricing() if has_global_cost else get_pricing(cfg)
                for idx, ov in enumerate(overrides):
                    matched = match_override(ov, umo, user_id, provider_id)
                    if matched is None:
                        continue
                    # 命中一条规则 → 评估其 token / cost
                    lt = float(ov.get("token_limit") or 0)
                    lc = float(ov.get("cost_limit") or 0)
                    if lt <= 0 and lc <= 0:
                        # 规则未设上限，视为不限制；继续下一条 / 全局
                        break
                    used_t = 0.0
                    used_c = 0.0
                    if lt > 0:
                        used_t = await self._override_used(
                            ov, "token", umo, model, user_id, provider_id, d_start, pricing
                        )
                    if lc > 0:
                        used_c = await self._override_used(
                            ov, "cost", umo, model, user_id, provider_id, d_start, pricing
                        )
                    if lt > 0 and used_t >= lt:
                        return {
                            "exceeded": True,
                            "dim": f"override:{idx}",
                            "metric": "token",
                            "limit": lt,
                            "used": used_t,
                            "on_exceeded": str(ov.get("on_exceeded") or "stop"),
                            "fallback_provider_ids": list(ov.get("fallback_provider_ids") or []),
                            "fallback_token_limit": int(ov.get("fallback_token_limit") or 0),
                            "stop_message": str(ov.get("stop_message") or ""),
                            "rule_idx": idx,
                        }
                    if lc > 0 and used_c >= lc:
                        return {
                            "exceeded": True,
                            "dim": f"override:{idx}",
                            "metric": "cost",
                            "limit": lc,
                            "used": used_c,
                            "on_exceeded": str(ov.get("on_exceeded") or "stop"),
                            "fallback_provider_ids": list(ov.get("fallback_provider_ids") or []),
                            "fallback_token_limit": int(ov.get("fallback_token_limit") or 0),
                            "stop_message": str(ov.get("stop_message") or ""),
                            "rule_idx": idx,
                        }
                    # 命中且未超限 → 不再继续扫其他 override，也不评估全局
                    return {
                        **zero,
                        "on_exceeded": default_on_exceeded(cfg),
                    }

            # ===== 2. 全局 5 维 =====
            if not has_global_token and not has_global_cost:
                return zero
            used_t_map: dict[str, float] = {d: 0.0 for d in _DIM_ORDER}
            used_c_map: dict[str, float] = {d: 0.0 for d in _DIM_ORDER}
            m_start = month_window_start(now, tz)
            if has_global_token:
                session_usage = await self.query_usage(umo=umo, start=d_start)
                session_total = total_tokens(session_usage)
                model_total = session_total
                if model:
                    model_total = total_tokens(
                        await self.query_usage(model=model, start=d_start)
                    )
                used_t_map = {
                    "per_session_daily": session_total,
                    "per_user_daily": session_total,
                    "per_model_daily": model_total,
                    "global_daily": total_tokens(await self.query_usage(start=d_start)),
                    "global_monthly": total_tokens(await self.query_usage(start=m_start)),
                }
            if has_global_cost:
                pricing = self.get_pricing()
                ses_cost = _groups_cost(
                    await self.query_usage_grouped(by="model", umo=umo, start=d_start),
                    pricing,
                )
                mod_cost = ses_cost
                if model:
                    mod_cost = compute_cost_value(
                        await self.query_usage(model=model, start=d_start),
                        model,
                        pricing,
                    )
                used_c_map = {
                    "per_session_daily": ses_cost,
                    "per_user_daily": ses_cost,
                    "per_model_daily": mod_cost,
                    "global_daily": _groups_cost(
                        await self.query_usage_grouped(by="model", start=d_start),
                        pricing,
                    ),
                    "global_monthly": _groups_cost(
                        await self.query_usage_grouped(by="model", start=m_start),
                        pricing,
                    ),
                }
            result = check_dimensions_dual(used_t_map, used_c_map, limits_t, limits_c)
            result["on_exceeded"] = default_on_exceeded(cfg)
            result["fallback_provider_ids"] = []
            result["fallback_token_limit"] = 0
            result["stop_message"] = ""
            result["rule_idx"] = -1
            return result
        except Exception:
            return zero

    # ===== 执行动作派发 =====

    async def apply_over_limit_chain(
        self,
        event: Any,
        req: Any,
        result: dict[str, Any],
        _strategies: list[dict[str, Any]] | None = None,  # 兼容旧签名
    ) -> bool:
        """根据 ``result["on_exceeded"]`` 派发处理动作，返回是否已处理。

        - ``"stop"`` → 发文案 + stop_event
        - ``"fallback"`` → 按 ``result.fallback_provider_ids`` 逐个尝试
        - ``"warn"`` → 仅发警告（不 stop_event，请求继续）
        - 其他 / 异常 → 兜底 stop
        """
        from astrbot import logger
        from astrbot.api.event import MessageChain

        try:
            action = str(result.get("on_exceeded") or "stop").lower()
            if action == "fallback":
                pids = [
                    str(p)
                    for p in (result.get("fallback_provider_ids") or [])
                    if str(p).strip()
                ]
                if pids:
                    return await self._try_fallback(
                        event,
                        req,
                        pids,
                        int(result.get("fallback_token_limit") or 0),
                    )
                # fallback 但没配 provider 列表 → 兜底 stop
                logger.warning("[cost_control] fallback 未配置 provider，降级 stop")
                await self._do_stop(event, result, str(result.get("stop_message") or ""))
                return True
            if action == "warn":
                msg = self._format_message(result)
                try:
                    await event.send(MessageChain().message(f"⚠ {msg}（仅警告，继续执行）"))
                except Exception as e:
                    logger.warning("[cost_control] warn 发送失败: %s", e)
                return False  # 不 stop_event
            # stop
            msg = str(result.get("stop_message") or "").strip() or self._format_message(result)
            await self._do_stop(event, result, msg)
            return True
        except Exception as e:
            logger.warning("[cost_control] 超限派发异常，兜底 stop: %s", e)
            try:
                await self._do_stop(event, result, "")
            except Exception:
                pass
            return True

    def _format_message(self, result: dict[str, Any]) -> str:
        dim = result.get("dim") or ""
        used = result.get("used")
        limit = result.get("limit")
        if result.get("metric") == "cost":
            return (
                f"⏸ 已超出花费预算（{dim}）："
                f"${float(used or 0):.4f} / ${float(limit or 0):.2f}"
            )
        return f"⏸ 已超出预算（{dim}）：用 {used} / 限 {limit} token"

    async def _try_fallback(
        self,
        event: Any,
        req: Any,
        pids: list[str],
        token_limit: int,
    ) -> bool:
        """遍历 ``pids`` 逐个调用，首个成功即返回 True；全失败返回 False。"""
        from astrbot import logger

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
        """用备用 Provider 执行本轮请求。"""
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
        """把 fallback 调用的 usage 落补充表。"""
        from .supplement import _extract_cache, _safe_sender_id

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
            getattr(event, "unified_msg_origin", None)
            or getattr(event, "session_id", None)
            or ""
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
            "user_id": _safe_sender_id(event),
            "created_at": datetime.now(UTC),
        }
        await self.save_supplement(record)

    async def _do_stop(
        self,
        event: Any,
        result: dict[str, Any],
        message: str,
    ) -> None:
        """硬拦截：发送文案 + stop_event。"""
        from astrbot import logger
        from astrbot.api.event import MessageChain

        msg = (message or "").strip() or self._format_message(result)
        try:
            event.stop_event()
        except Exception:
            pass
        try:
            await event.send(MessageChain().message(msg))
        except Exception as e:
            logger.warning("[cost_control] 超限提示发送失败: %s", e)


__all__ = [
    "resolve_tz",
    "day_window_start",
    "month_window_start",
    "check_dimensions",
    "check_dimensions_dual",
    "total_tokens",
    "truncate_contexts",
    "match_override",
    "default_on_exceeded",
    "get_budget_overrides",
    "get_fallback_providers",
    "BudgetMixin",
]
