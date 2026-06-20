"""缓存破坏诊断 Mixin。

识别四类导致 prompt cache 失效的原因，并在命中率低于阈值时触发告警：

1. **上下文重置**（``context_reset``）：本轮历史长度远小于上一轮，历史缓存全失效。
2. **system prompt 变更**（``system_prompt_change``）：多轮间 system prompt 内容变化，前缀不匹配。
3. **工具定义变更**（``tools_change``）：``func_tool`` 集合在多轮间变化，破坏缓存键。
4. **上下文顺序漂移**（``order_drift``）：消息顺序变化导致前缀不匹配。

源码约束（已核对）：``Conversation`` 仅持久化 ``history`` 字符串，**无
``system_prompt`` / ``tools`` / ``contexts`` 字段**。故本 Mixin 自行在内存
``_last_ctx[umo]`` 缓存上一轮上下文签名（system_prompt / func_tool / 各 message
content 的确定性 hash + 历史长度），用于跨轮对比。重启丢失可接受（诊断是实时的）。

命中率口径：``cache_read / (cache_read + input_other + cache_creation)``。
Anthropic 的 ``cache_creation`` 在 ``raw_completion`` 不可得（源未设置），
``supplement`` 已降级为 None，此时分母退化为 ``cache_read + input_other``。

可测性设计：``hit_rate`` 与 ``diagnose_changes`` 抽成模块级纯函数，不依赖
astrbot，可单测。

阶段 3 实现。
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any

from astrbot.api.provider import ProviderRequest

from .config import get_config


def _hash_text(s: Any) -> str:
    """对文本取确定性短 hash（md5 前 8 位），用于跨轮对比（纯函数）。"""
    try:
        return hashlib.md5(str(s).encode("utf-8", errors="ignore")).hexdigest()[:8]
    except Exception:
        return ""


def _summarize(sig: dict[str, Any]) -> dict[str, Any]:
    """把上下文签名裁剪为紧凑、可对比的结构（纯函数）。

    不保留完整 ``contexts_hashes`` 列表（可能很长且对展示无价值），只取长度。
    输出供 ``CacheEvent.before/after`` 落库，前端做结构化前后对比展示。
    """
    hashes = list(sig.get("contexts_hashes", []) or [])
    return {
        "history_len": int(sig.get("history_len", 0) or 0),
        "system_hash": str(sig.get("system_hash", "") or ""),
        "tools_hash": str(sig.get("tools_hash", "") or ""),
        "contexts_count": len(hashes),
    }


def _line_diff(
    old: Any,
    new: Any,
    max_lines: int = 150,
) -> list[dict[str, str]] | None:
    """对两段文本做行级 diff，返回 git 风格行列表（纯函数）。

    返回 ``[{"op": "+" | "-" | " ", "text": line}, ...]``：``+`` 新增、``-``
    删除、``" "`` 上下文。任一输入为空、无增删行或出错时返回 ``None``（前端据此
    不渲染 diff 块）。超过 ``max_lines`` 截断并追加一条提示行，避免落库 payload 失控。
    """
    try:
        o = str(old or "")
        n = str(new or "")
        if not o or not n:
            return None
        out: list[dict[str, str]] = []
        for line in difflib.ndiff(o.splitlines(), n.splitlines()):
            raw = line.rstrip("\n")
            if not raw or raw.startswith("?"):  # ? 是 ndiff 行内提示行，跳过
                continue
            op = raw[0]
            text = raw[2:] if len(raw) >= 2 else ""
            if op == "+":
                out.append({"op": "+", "text": text})
            elif op == "-":
                out.append({"op": "-", "text": text})
            else:
                out.append({"op": " ", "text": text})
        if not any(d["op"] != " " for d in out):
            return None
        total = len(out)
        if total > max_lines:
            out = out[:max_lines]
            out.append({"op": " ", "text": f"…（已截断，共 {total} 行 diff）"})
        return out
    except Exception:
        return None


def hit_rate(
    cache_read: int | None,
    input_other: int | None,
    cache_creation: int | None,
) -> float:
    """计算缓存命中率百分比（纯函数）。

    口径：``cache_read / (cache_read + input_other + cache_creation) * 100``。
    输入全为 None 或分母为 0 时返回 -1（表示「无数据」，不计入告警）。
    """
    cr = int(cache_read or 0)
    io = int(input_other or 0)
    cc = int(cache_creation or 0)
    denom = cr + io + cc
    if denom <= 0:
        return -1.0
    return cr * 100.0 / denom


def diagnose_changes(
    current: dict[str, Any],
    last: dict[str, Any],
    flags: dict[str, bool],
) -> list[dict[str, Any]]:
    """对比当前与上一轮上下文签名，返回四类破坏事件（纯函数）。

    Args:
        current: 当前轮签名（``_context_signature`` 产出）。
        last: 上一轮签名（同结构）。
        flags: 各类检测开关，键 ``detect_context_reset`` /
            ``detect_system_prompt_change`` / ``detect_tools_change`` /
            ``detect_order_drift``，缺省视为 True。

    Returns:
        事件列表，每项 ``{"type", "severity", "detail", "before", "after"}``；
        ``before``/``after`` 为裁剪后的上一轮 / 本轮上下文签名（见
        :func:`_summarize`），供前端做结构化前后对比展示；``order_drift`` 的
        ``after`` 额外含 ``first_diverge_at``（首个不一致下标）。无问题返回空。
    """
    events: list[dict[str, Any]] = []
    before = _summarize(last)
    after = _summarize(current)

    def on(name: str) -> bool:
        return bool(flags.get(name, True))

    if on("detect_context_reset"):
        cur_len = int(current.get("history_len", 0) or 0)
        last_len = int(last.get("history_len", 0) or 0)
        if last_len >= 4 and cur_len < last_len * 0.5:
            events.append(
                {
                    "type": "context_reset",
                    "severity": "high",
                    "detail": f"历史长度骤降 {last_len} → {cur_len}，缓存大概率全失效",
                    "before": before,
                    "after": after,
                }
            )

    if on("detect_system_prompt_change"):
        if current.get("system_hash") and last.get("system_hash"):
            if current["system_hash"] != last["system_hash"]:
                ev_after = dict(after)
                sd = _line_diff(last.get("system_text"), current.get("system_text"))
                if sd is not None:
                    ev_after["system_diff"] = sd
                events.append(
                    {
                        "type": "system_prompt_change",
                        "severity": "high",
                        "detail": "system prompt 内容变化，前缀缓存失效",
                        "before": before,
                        "after": ev_after,
                    }
                )

    if on("detect_tools_change"):
        if current.get("tools_hash") != last.get("tools_hash"):
            # 仅当至少一轮有 tools 才报（避免空对空误报）
            if current.get("tools_hash") or last.get("tools_hash"):
                ev_after = dict(after)
                td = _line_diff(last.get("tools_text"), current.get("tools_text"))
                if td is not None:
                    ev_after["tools_diff"] = td
                events.append(
                    {
                        "type": "tools_change",
                        "severity": "medium",
                        "detail": "工具定义变化，破坏缓存键",
                        "before": before,
                        "after": ev_after,
                    }
                )

    if on("detect_order_drift"):
        cur_hashes = list(current.get("contexts_hashes", []) or [])
        last_hashes = list(last.get("contexts_hashes", []) or [])
        if last_hashes and len(cur_hashes) >= len(last_hashes):
            # 正常追加：本轮前 len(last) 条应与上一轮完全一致
            if cur_hashes[: len(last_hashes)] != last_hashes:
                # 找首个不一致下标，供前端精确定位漂移位置
                diverge = 0
                for i, h in enumerate(last_hashes):
                    if i >= len(cur_hashes) or cur_hashes[i] != h:
                        diverge = i
                        break
                events.append(
                    {
                        "type": "order_drift",
                        "severity": "medium",
                        "detail": "历史消息顺序或内容漂移，前缀缓存失效",
                        "before": before,
                        "after": {**after, "first_diverge_at": diverge},
                    }
                )

    return events


class CacheDiagMixin:
    """缓存破坏四类诊断的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any
    # 兄弟 StoreMixin 提供（事件落库）。
    save_cache_event: Any
    query_cache_events: Any

    # 上一轮上下文签名：key=umo（用于跨轮对比；事件本身落库 CacheEvent）。
    _last_ctx: dict[str, dict[str, Any]]

    def __init_cache_diag__(self) -> None:
        """惰性初始化上下文签名字典（多 Mixin 安全，幂等）。"""
        if getattr(self, "_last_ctx", None) is None:
            self._last_ctx = {}

    def _cache_diag_flags(self) -> dict[str, bool]:
        cfg = get_config(getattr(self, "cfg", None), "cache_diag", {}) or {}
        return cfg if isinstance(cfg, dict) else {}

    def _hit_rate_threshold(self) -> float:
        cfg = self._cache_diag_flags()
        try:
            return float(cfg.get("cache_hit_rate_alert_threshold", 30) or 0)
        except (TypeError, ValueError):
            return 30.0

    def _context_signature(self, req: ProviderRequest) -> dict[str, Any]:
        """从 req 提取上下文签名（确定性 hash + 历史长度）。

        除 hash 外，额外保留 ``system_text`` / ``tools_text`` 原始文本——这两者只进
        内存 ``_last_ctx[umo]``（供下一轮 :func:`_line_diff` 计算内容 diff），**不**经
        :func:`_summarize` 落库，避免 DB 行膨胀。
        """
        try:
            system = getattr(req, "system_prompt", "") or ""
            func_tool = getattr(req, "func_tool", None)
            contexts = list(getattr(req, "contexts", None) or [])
            contexts_hashes = [
                _hash_text(m.get("content") if isinstance(m, dict) else m) for m in contexts
            ]
            return {
                "system_hash": _hash_text(system) if system else "",
                "tools_hash": _hash_text(str(func_tool)) if func_tool is not None else "",
                "contexts_hashes": contexts_hashes,
                "history_len": len(contexts),
                "system_text": system,
                "tools_text": str(func_tool) if func_tool is not None else "",
            }
        except Exception:
            return {
                "system_hash": "",
                "tools_hash": "",
                "contexts_hashes": [],
                "history_len": 0,
                "system_text": "",
                "tools_text": "",
            }

    async def run_cache_diag(self, req: ProviderRequest, umo: str) -> list[dict[str, Any]]:
        """对比上一轮上下文签名做四类诊断，事件落库。

        在 ``on_llm_request_tail`` 调用（此时 req 为最终态）。返回事件列表，同时
        把每个事件写入 ``CacheEvent`` 表（重载不丢，可查询）。任何异常降级为空。
        """
        self.__init_cache_diag__()
        try:
            current = self._context_signature(req)
            last = self._last_ctx.get(umo, {}) if umo else {}
            events = diagnose_changes(current, last, self._cache_diag_flags()) if last else []
            for ev in events:
                ev["umo"] = umo
                try:
                    await self.save_cache_event(
                        {
                            "umo": umo,
                            "type": ev.get("type", ""),
                            "severity": ev.get("severity", "medium"),
                            "detail": ev.get("detail", ""),
                            "before": ev.get("before"),
                            "after": ev.get("after"),
                        }
                    )
                except Exception:
                    pass
            if umo:
                self._last_ctx[umo] = current
            return events
        except Exception:
            return []

    def check_hit_rate(self, record: dict[str, Any]) -> tuple[float, bool]:
        """基于补充记录的 cache 字段计算命中率，判断是否低于阈值需告警。

        Returns:
            ``(rate, should_alert)``。``rate`` 为百分比；无数据时 -1 且不告警。
        """
        try:
            rate = hit_rate(
                record.get("cache_read") or record.get("token_input_cached"),
                record.get("token_input_other"),
                record.get("cache_creation"),
            )
            if rate < 0:
                return rate, False
            threshold = self._hit_rate_threshold()
            return rate, threshold > 0 and rate < threshold
        except Exception:
            return -1.0, False

    async def recent_events(self, umo: str, limit: int = 10) -> list[dict[str, Any]]:
        """返回指定会话最近的缓存诊断事件（供 ``/cache`` 命令展示，读 DB）。"""
        try:
            rows = await self.query_cache_events(umo=umo, limit=limit)
            return [
                {
                    "umo": getattr(r, "umo", "") or "",
                    "type": getattr(r, "type", "") or "",
                    "severity": getattr(r, "severity", "medium") or "medium",
                    "detail": getattr(r, "detail", "") or "",
                    "created_at": getattr(r, "created_at", None),
                }
                for r in rows
            ]
        except Exception:
            return []
