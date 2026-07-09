"""AI 智能诊断 Mixin。

一键 AI 诊断功能：收集成本控制各维度数据（成本/用量/缓存/归因/预算/定价），
调用 AstrBot 默认 LLM Provider 进行综合分析，输出结构化的诊断结论与优化建议。

方案选择：数据注入上文（非 Agent+Tools）
- Pages 场景无真实 event，tool_loop_agent 不可用
- 一次性 llm_generate 调用，快速可靠
- LLM 扮演"成本分析师"角色，逐一分析各维度数据
"""

from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any


class AiDiagMixin:
    """AI 智能诊断功能。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    cfg: Any
    # 兄弟 Mixin 提供。
    build_report: Any
    get_budgets: Any
    get_budgets_cost: Any
    get_pricing: Any
    query_supplements: Any
    query_cache_events: Any
    query_usage: Any
    query_usage_grouped: Any

    # ===== Provider 获取 =====

    def _get_default_provider_id(self) -> str | None:
        """获取 AstrBot 默认聊天 Provider ID。"""
        try:
            from astrbot.core.provider.manager import ProviderType

            prov = self.context.provider_manager.get_using_provider(
                provider_type=ProviderType.CHAT_COMPLETION,
                umo=None,
            )
            if prov:
                return prov.meta().id
        except Exception:
            pass
        # 回退：取第一个可用的 chat provider
        try:
            for p in self.context.provider_manager.provider_insts:
                meta = p.meta()
                if meta.type == "chat_completion":
                    return meta.id
        except Exception:
            pass
        return None

    def _get_provider_display_name(self, provider_id: str | None) -> str:
        """获取 Provider 的显示名称（模型名 + ID）。"""
        if not provider_id:
            return "未配置"
        try:
            for p in self.context.provider_manager.provider_insts:
                meta = p.meta()
                if meta.id == provider_id:
                    model = getattr(meta, "model_name", None) or provider_id
                    return f"{model} ({provider_id})"
        except Exception:
            pass
        return provider_id or ""

    # ===== 数据收集 =====

    async def _collect_diag_data(self) -> dict[str, Any]:
        """收集所有维度的诊断数据快照。"""
        data: dict[str, Any] = {}

        # 1. 成本与用量概览（周窗口）
        try:
            report = await self.build_report(window="weekly")
            usage = report.get("usage", {}) or {}
            data["overview"] = {
                "window": "近7天",
                "cost": round(float(report.get("cost", 0) or 0), 4),
                "call_count": int(usage.get("count", 0) or 0),
                "token_input_other": int(usage.get("token_input_other", 0) or 0),
                "token_input_cached": int(usage.get("token_input_cached", 0) or 0),
                "token_output": int(usage.get("token_output", 0) or 0),
                "cache_hit_rate": round(float(report.get("cache_hit_rate", 0) or 0), 1),
                "cache_samples": int(report.get("cache_samples", 0) or 0),
                "avg_injection": int(report.get("avg_injection", 0) or 0),
                "injection_samples": int(report.get("injection_samples", 0) or 0),
                "cost_by_model": [
                    {
                        "model": m.get("model", ""),
                        "cost": round(float(m.get("cost", 0) or 0), 4),
                        "tokens": int(m.get("tokens", 0) or 0),
                        "count": int(m.get("count", 0) or 0),
                    }
                    for m in (report.get("cost_by_model") or [])[:8]
                ],
                "top_sessions_by_cost": [
                    {
                        "umo": s.get("umo", "")[:20],
                        "cost": round(float(s.get("cost", 0) or 0), 4),
                        "tokens": int(s.get("tokens", 0) or 0),
                    }
                    for s in (report.get("top_sessions_by_cost") or [])[:5]
                ],
            }
        except Exception as e:
            data["overview"] = {"error": str(e)}

        # 2. 缓存破坏事件
        try:
            events = await self.query_cache_events(limit=20)
            data["cache_events"] = {
                "total": len(events),
                "items": [
                    {
                        "umo": getattr(e, "umo", "")[:20] if getattr(e, "umo", None) else "",
                        "type": getattr(e, "type", "") or "",
                        "severity": getattr(e, "severity", "") or "",
                        "detail": (getattr(e, "detail", "") or "")[:120],
                    }
                    for e in events[:10]
                ],
            }
        except Exception as e:
            data["cache_events"] = {"error": str(e)}

        # 3. 上下文注入归因（最近样本）
        try:
            from datetime import UTC, datetime

            from .analytics import report_window_start
            from .budget import resolve_tz
            from .config import get_config

            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            start = report_window_start("weekly", now, tz, refresh)
            sups = await self.query_supplements(start=start, limit=500)
            # 提取归因数据
            attributions: list[dict[str, Any]] = []
            for s in sups[-20:]:  # 最近20条
                attr = getattr(s, "attribution", None)
                if attr and isinstance(attr, dict):
                    attributions.append({
                        "umo": str(getattr(s, "umo", "") or "")[:20],
                        "injection_total": getattr(s, "injection_total", None),
                        "system": attr.get("system"),
                        "tools": attr.get("tools"),
                        "history": attr.get("history"),
                        "user": attr.get("user"),
                        "extra": attr.get("extra"),
                    })
            data["attribution"] = {
                "samples": len(attributions),
                "items": attributions[:10],
            }
        except Exception as e:
            data["attribution"] = {"error": str(e)}

        # 4. 预算状态
        try:
            from datetime import UTC, datetime

            from .budget import (
                _DIM_ORDER,
                day_window_start,
                month_window_start,
                resolve_tz,
                total_tokens,
            )
            from .config import get_config

            now = datetime.now(UTC)
            tz = resolve_tz(self.context)
            refresh = str(get_config(getattr(self, "cfg", None), "refresh_time", "00:00"))
            d_start = day_window_start(refresh, now, tz)
            m_start = month_window_start(now, tz)
            day_total = total_tokens(await self.query_usage(start=d_start))
            month_total = total_tokens(await self.query_usage(start=m_start))

            limits = self.get_budgets()
            limits_cost = self.get_budgets_cost()

            dims: list[dict[str, Any]] = []
            dim_labels = {
                "global_daily": "每日全局",
                "global_monthly": "每月全局",
                "per_session_daily": "每会话·每日",
                "per_user_daily": "每用户·每日",
                "per_model_daily": "每模型·每日",
            }
            dim_used = {"global_daily": day_total, "global_monthly": month_total}
            for d in _DIM_ORDER:
                lt = int(limits.get(d, 0) or 0)
                lc = float(limits_cost.get(d, 0) or 0)
                used = dim_used.get(d, 0)
                if lt > 0 or lc > 0:
                    ratio = round(used * 100.0 / lt, 1) if lt > 0 else 0
                    dims.append({
                        "dimension": dim_labels.get(d, d),
                        "token_limit": lt,
                        "token_used": used,
                        "token_ratio": ratio,
                        "cost_limit": lc,
                        "exceeded": used >= lt if lt > 0 else False,
                    })
            data["budgets"] = {"dimensions": dims}
        except Exception as e:
            data["budgets"] = {"error": str(e)}

        # 5. 定价覆盖
        try:
            from .cost import resolve_pricing

            pricing = self.get_pricing()
            rows = await self.query_usage_grouped(by="provider_model")
            unpriced: list[dict[str, Any]] = []
            for r in rows:
                provider_id = r.get("provider_id") or ""
                model = r.get("provider_model") or ""
                if model and resolve_pricing(provider_id or None, model, pricing) is None:
                    unpriced.append({
                        "model": model,
                        "provider_id": provider_id,
                        "tokens": (
                            int(r.get("token_input_other", 0) or 0)
                            + int(r.get("token_input_cached", 0) or 0)
                            + int(r.get("token_output", 0) or 0)
                        ),
                        "count": int(r.get("count", 0) or 0),
                    })
            data["pricing"] = {
                "total_models": len(rows),
                "unpriced_count": len(unpriced),
                "unpriced": unpriced[:5],
            }
        except Exception as e:
            data["pricing"] = {"error": str(e)}

        return data

    # ===== Prompt 构建 =====

    def _build_diag_prompt(self, data: dict[str, Any]) -> tuple[str, str]:
        """构造 system prompt 和 user prompt。

        返回 (system_prompt, user_prompt)。
        """
        system_prompt = (
            "你是一位专业的 LLM 成本分析师，负责对 AstrBot 成本控制插件的运行数据进行全面诊断。\n"
            "你将收到5个维度的成本数据，请逐一分析并给出专业结论。\n\n"
            "输出要求（严格 JSON 格式，不要包裹在 markdown 代码块中）：\n"
            "{\n"
            '  "overall": "整体评价（1-2句话概括成本健康状况）",\n'
            '  "overall_score": 85,\n'
            '  "highlights": ["做得好的方面1", "做得好的方面2"],\n'
            '  "risks": [\n'
            '    {"module": "模块名", "level": "high/medium/low/info", '
            '"issue": "问题描述", "advice": "改进建议"}\n'
            "  ],\n"
            '  "summary": "总结性建议（1-2句话）"\n'
            "}\n\n"
            "评分标准（基于成本效率）：\n"
            "90+优秀（成本控制极佳），75-89良好，60-74需关注，<60需修复。\n"
            "等级说明：high=高危（成本浪费严重，需立即处理）、medium=中危（建议处理）、"
            "low=低危（可优化）、info=提示（仅提示，无需处理）。\n"
            "重点关注：缓存命中率低导致成本浪费、未定价模型导致成本统计缺失、"
            "预算接近超限、上下文注入过多导致 token 浪费。\n"
            "请基于实际数据分析，不要凭空臆测。如果数据不足，如实说明。"
        )

        sections: list[str] = []

        # 1. 成本与用量概览
        ov = data.get("overview", {})
        if "error" not in ov:
            hit_rate = ov.get("cache_hit_rate", 0)
            cache_samples = ov.get("cache_samples", 0)
            avg_inj = ov.get("avg_injection", 0)
            inj_samples = ov.get("injection_samples", 0)
            cost_models = json.dumps(
                ov.get("cost_by_model", []), ensure_ascii=False
            )[:600]
            top_sessions = json.dumps(
                ov.get("top_sessions_by_cost", []), ensure_ascii=False
            )[:400]
            sections.append(
                f"## 成本与用量（{ov.get('window', '近7天')}）\n"
                f"总成本：{ov.get('cost', 0)}，"
                f"调用次数：{ov.get('call_count', 0)}\n"
                f"Token 分布：输入(非缓存) {ov.get('token_input_other', 0)} / "
                f"缓存命中 {ov.get('token_input_cached', 0)} / "
                f"输出 {ov.get('token_output', 0)}\n"
                f"平均缓存命中率：{hit_rate}%（{cache_samples} 样本）\n"
                f"平均上下文注入：{avg_inj} token"
                f"（{inj_samples} 样本）\n"
                f"按模型成本：{cost_models}\n"
                f"高成本会话：{top_sessions}"
            )
        else:
            sections.append(f"## 成本与用量\n状态：数据获取失败（{ov.get('error')}）")

        # 2. 缓存破坏事件
        ce = data.get("cache_events", {})
        if "error" not in ce:
            sections.append(
                f"## 缓存破坏诊断\n"
                f"最近缓存事件数：{ce.get('total', 0)}\n"
                f"事件样例：{json.dumps(ce.get('items', []), ensure_ascii=False)[:500]}"
            )
        else:
            sections.append(f"## 缓存破坏诊断\n状态：数据获取失败（{ce.get('error')}）")

        # 3. 上下文注入归因
        at = data.get("attribution", {})
        if "error" not in at:
            sections.append(
                f"## 上下文注入归因\n"
                f"归因样本数：{at.get('samples', 0)}\n"
                f"归因详情（system/tools/history/user/extra 各维度 token）：\n"
                f"{json.dumps(at.get('items', []), ensure_ascii=False)[:600]}"
            )
        else:
            sections.append(f"## 上下文注入归因\n状态：数据获取失败（{at.get('error')}）")

        # 4. 预算状态
        bd = data.get("budgets", {})
        if "error" not in bd:
            bd_dims = json.dumps(
                bd.get("dimensions", []), ensure_ascii=False
            )[:600]
            sections.append(
                f"## 预算状态\n"
                f"已配置的预算维度：{bd_dims}"
            )
        else:
            sections.append(f"## 预算状态\n状态：数据获取失败（{bd.get('error')}）")

        # 5. 定价覆盖
        pr = data.get("pricing", {})
        if "error" not in pr:
            sections.append(
                f"## 定价覆盖\n"
                f"总模型数：{pr.get('total_models', 0)}，"
                f"未定价模型数：{pr.get('unpriced_count', 0)}\n"
                f"未定价模型：{json.dumps(pr.get('unpriced', []), ensure_ascii=False)[:400]}"
            )
        else:
            sections.append(f"## 定价覆盖\n状态：数据获取失败（{pr.get('error')}）")

        user_prompt = (
            "请对以下 AstrBot 成本控制数据进行全面诊断分析，输出 JSON 结论：\n\n"
            + "\n\n".join(sections)
        )
        return system_prompt, user_prompt

    # ===== 结论解析 =====

    @staticmethod
    def _parse_conclusion(text: str) -> dict | None:
        """从 LLM 返回文本中解析 JSON 结论。

        兼容三种情况：纯 JSON、```json ...``` 代码块、混杂文本中提取。
        """
        text = text.strip()
        # 方案1：直接解析
        try:
            return json.loads(text)
        except Exception:
            pass
        # 方案2：提取 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # 方案3：提取第一个 { ... } 块
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    # ===== 主流程 =====

    async def run_ai_diag(self) -> dict[str, Any]:
        """执行 AI 智能诊断。

        流程：
        1. 获取默认 Provider
        2. 收集所有维度数据
        3. 构造 prompt，调用 LLM
        4. 解析结论，返回结构化结果
        """
        from astrbot import logger

        result: dict[str, Any] = {
            "timestamp": int(time.time()),
            "provider_id": None,
            "provider_name": None,
            "conclusion": None,
            "raw_text": None,
            "error": None,
        }

        # 1. 获取 Provider
        provider_id = self._get_default_provider_id()
        result["provider_id"] = provider_id
        result["provider_name"] = self._get_provider_display_name(provider_id)
        if not provider_id:
            result["error"] = (
                "未找到可用的聊天模型 Provider，请在 AstrBot 配置中添加 LLM 提供商。"
            )
            return result

        # 2. 收集维度数据
        logger.info("[cost_control] AI诊断：开始收集数据...")
        try:
            data = await self._collect_diag_data()
        except Exception as e:
            result["error"] = f"数据收集失败: {type(e).__name__}: {e}"
            return result

        # 3. 构造 prompt 并调用 LLM
        system_prompt, user_prompt = self._build_diag_prompt(data)
        logger.info("[cost_control] AI诊断：调用 LLM (%s)...", provider_id)
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
            raw_text = llm_resp.completion_text or ""
            result["raw_text"] = raw_text

            # 4. 解析 JSON 结论
            conclusion = self._parse_conclusion(raw_text)
            result["conclusion"] = conclusion
            if conclusion:
                logger.info(
                    "[cost_control] AI诊断完成：总分 %s",
                    conclusion.get("overall_score", "?"),
                )
            else:
                logger.warning("[cost_control] AI诊断：LLM 返回内容无法解析为 JSON")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            result["error"] = err_msg
            logger.error(
                "[cost_control] AI诊断失败：%s\n%s", err_msg, traceback.format_exc()
            )
        return result
