"""归因分析 Mixin。

在 ``on_llm_request`` 的 head / tail 两个钩子里快照上下文，估算 system / tools /
history / user / extra 各组件的 token 占比，以及 head→tail 期间所有插件累计注入的
token 总量（「注入归因」）。

``user`` 维度仅含当前轮 ``prompt`` 文本 + ``image_urls`` / ``audio_urls`` 媒体块；
``extra`` 维度单独估算 ``extra_user_content_parts``（插件注入的额外内容块，含文本
与图片 / 音频等非文本块），二者不再合并。

源码约束（已核对 ``astrbot/core/``）：
- ``ProviderRequest.system_prompt`` 是纯 str；``contexts`` 是 OpenAI messages
  格式的 ``list[dict]``（``content`` 可为 str 或多模态 ``list[dict]``）；工具由
  ``func_tool``（ToolSet）承载，**无独立 ``tools`` 字段**。
- hook 执行器（``pipeline/context_utils.call_event_hook``）遍历 handlers 时
  **不暴露当前 handler / star**，故无法精确归因到单个插件——只能做「所有插件
  注入总量」与四组件占比。head（``priority=100000``）快照初始上下文，
  tail（``priority=-100000``）快照最终，差值即本次请求所有插件累计注入。

注入量通过实例 dict ``_attr_snapshots``（key=``id(req)``）在 head/tail 间传递：
head 存、tail 取删。asyncio 单线程且 head→tail 在同一 ``call_event_hook`` 调用链
内，``id(req)`` 在请求期间唯一，无竞态。

可测性设计：token 估算（``_str_tokens`` / ``_content_tokens`` /
``estimate_tokens``）抽成模块级纯函数，不依赖 astrbot，可单测。

阶段 3 实现。
"""

from __future__ import annotations

from typing import Any

from astrbot.api.provider import ProviderRequest

from .config import get_config

# 非文本块的固定 token 估值（与 ``_content_tokens`` 中多模态块的估值保持一致）。
IMAGE_TOKEN_EST = 85
AUDIO_TOKEN_EST = 200

# 估算说明：展示端（命令 / Web API / 前端）统一引用，确保算法口径一致。
ESTIMATION_NOTE = (
    "token 估算（CJK ≈ 0.6/字、ASCII ≈ 0.25/字；图片 ≈ 85、音频 ≈ 200），"
    "基于归因样本，仅供组件占比参考。"
)


def _str_tokens(s: str) -> int:
    """混合启发式估算字符串 token 数（纯函数）。

    CJK 字符约 0.6 token/char，ASCII 等约 0.25 token/char（≈4 char/token）。
    仅用于归因对比，非精确计费。
    """
    if not s:
        return 0
    cjk = 0
    for c in s:
        if "一" <= c <= "鿿" or "　" <= c <= "〿":
            cjk += 1
    other = len(s) - cjk
    return max(1, int(cjk * 0.6 + other * 0.25))


def _content_tokens(content: Any) -> int:
    """估算一条 message 的 ``content`` token（纯函数）。

    ``content`` 可为 str 或多模态 ``list[dict]``（每项含 ``type``）：
    - ``text``：按文本估算
    - ``think``：按思考文本估算
    - ``image_url`` / ``audio_url``：给固定估值（图像 ~85，音频 ~200）
    其它类型按其 repr 文本粗估。
    """
    if content is None:
        return 0
    if isinstance(content, str):
        return _str_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                total += _str_tokens(str(part))
                continue
            ptype = part.get("type")
            if ptype == "text":
                total += _str_tokens(str(part.get("text", "")))
            elif ptype == "think":
                total += _str_tokens(str(part.get("think", "")))
            elif ptype in ("image_url", "input_audio", "audio_url"):
                total += IMAGE_TOKEN_EST if ptype == "image_url" else AUDIO_TOKEN_EST
            else:
                total += _str_tokens(str(part))
        return total
    return _str_tokens(str(content))


def _content_part_tokens(part: Any) -> int:
    """估算一个 ``ContentPart`` 对象的 token（纯函数，duck-typing）。

    覆盖所有块类型，包括非文本块：
    - ``text``：按 ``.text`` 文本估算
    - ``think``：按 ``.think`` 文本估算
    - ``image_url``：固定估值 ``IMAGE_TOKEN_EST``
    - ``audio_url``：固定估值 ``AUDIO_TOKEN_EST``
    - dict：回退到 ``_content_tokens``（已处理多模态 dict）
    - 其它：按 repr 文本粗估（截断 2000 字符防膨胀）
    """
    if isinstance(part, dict):
        return _content_tokens([part])
    ptype = getattr(part, "type", None)
    if ptype == "text":
        return _str_tokens(getattr(part, "text", "") or "")
    if ptype == "think":
        return _str_tokens(getattr(part, "think", "") or "")
    if ptype == "image_url":
        return IMAGE_TOKEN_EST
    if ptype == "audio_url":
        return AUDIO_TOKEN_EST
    return _str_tokens(str(part)[:2000])


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """估算 OpenAI messages 列表的总 token（纯函数，供测试）。"""
    total = 0
    for m in messages or []:
        if isinstance(m, dict):
            total += _content_tokens(m.get("content"))
            # role 开销忽略不计
    return total


def _tool_tokens(func_tool: Any) -> int:
    """估算 ToolSet 的 token（纯函数，duck-typing + 回退）。

    优先按 ``.tools`` 列表的 name+description 估算；拿不到结构时回退到
    repr 文本（截断 2000 字符防膨胀）。
    """
    if func_tool is None:
        return 0
    try:
        tools = getattr(func_tool, "tools", None)
        if tools:
            buf: list[str] = []
            for t in tools:
                name = getattr(t, "name", "") or ""
                desc = getattr(t, "description", "") or ""
                buf.append(f"{name}: {desc}")
            return _str_tokens("\n".join(buf))
    except Exception:
        pass
    return _str_tokens(str(func_tool)[:2000])


class AttributorMixin:
    """上下文 token 估算与注入归因的 Mixin。

    依赖 ``Main`` 宿主提供 ``context`` / ``config``；head/tail 钩子由 ``Main``
    持有（因为 ``@filter.on_llm_request`` 装饰器只能挂在 ``Star`` 子类的方法上），
    本 Mixin 只暴露被钩子调用的辅助方法。
    """

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any

    # head/tail 间传递的初始快照：key=id(req)。
    _attr_snapshots: dict[int, dict[str, int]]

    # 最近一次（按 umo）注入归因，供 ``/attribution`` 命令与 supplement 采集读取。
    _attr_last: dict[str, dict[str, Any]]

    # 最近一次（按 umo）system prompt 文本，供 ``/optimize`` 静态分析复用。
    _last_sp: dict[str, str]

    def __init_attribution__(self) -> None:
        """惰性初始化归因状态字典（多 Mixin 安全，幂等）。"""
        if getattr(self, "_attr_snapshots", None) is None:
            self._attr_snapshots = {}
        if getattr(self, "_attr_last", None) is None:
            self._attr_last = {}
        if getattr(self, "_last_sp", None) is None:
            self._last_sp = {}

    def _attribution_enabled(self) -> bool:
        cfg = get_config(getattr(self, "cfg", None), "attribution", {}) or {}
        return bool(cfg.get("enabled", True)) if isinstance(cfg, dict) else True

    def _attribution_sampled(self, req: Any) -> bool:
        """按 ``sample_rate``（百分比）决定本次是否采样。100 = 全采样。

        无随机源（确定性采样）：用 ``id(req) % 100 < rate`` 近似，可复现。
        """
        cfg = get_config(getattr(self, "cfg", None), "attribution", {}) or {}
        rate = int(cfg.get("sample_rate", 100) or 0) if isinstance(cfg, dict) else 100
        if rate >= 100:
            return True
        if rate <= 0:
            return False
        return (id(req) % 100) < rate

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表 token（Mixin 暴露口，转调纯函数）。"""
        return estimate_tokens(messages)

    def snapshot_context(self, req: ProviderRequest) -> dict[str, int]:
        """对 ``ProviderRequest`` 分组件估算 token 快照。

        Returns:
            ``{"system": int, "tools": int, "history": int, "user": int,
            "extra": int, "total": int}``。

            - ``user``：当前轮 ``prompt`` 文本 + ``image_urls`` / ``audio_urls``
              媒体块（用户原始发言，不含插件注入）。
            - ``extra``：``extra_user_content_parts`` 全部块（文本 / 图片 / 音频
              等，插件注入的额外内容），与 ``user`` 分开统计。
            - ``total``：五维之和，即发给 LLM 的完整请求估算规模。
            任何异常降级为全零。
        """
        try:
            system = _str_tokens(getattr(req, "system_prompt", "") or "")
            tools = _tool_tokens(getattr(req, "func_tool", None))
            contexts = list(getattr(req, "contexts", None) or [])
            history = estimate_tokens(contexts)
            # user = prompt 文本 + 媒体块（图片 / 音频），均为用户原始发言
            user = _str_tokens(getattr(req, "prompt", "") or "")
            user += len(getattr(req, "image_urls", None) or []) * IMAGE_TOKEN_EST
            user += len(getattr(req, "audio_urls", None) or []) * AUDIO_TOKEN_EST
            # extra = 插件注入的额外内容块（所有类型，含非文本）
            extra = 0
            for p in getattr(req, "extra_user_content_parts", None) or []:
                extra += _content_part_tokens(p)
            return {
                "system": system,
                "tools": tools,
                "history": history,
                "user": user,
                "extra": extra,
                "total": system + tools + history + user + extra,
            }
        except Exception:
            return {
                "system": 0,
                "tools": 0,
                "history": 0,
                "user": 0,
                "extra": 0,
                "total": 0,
            }

    def record_initial_context(self, req: ProviderRequest) -> None:
        """head 钩子调用：采样后存入初始快照（key=id(req)）。

        任何异常静默（归因是诊断功能，不阻断主流程）。
        """
        self.__init_attribution__()
        try:
            if not self._attribution_enabled() or not self._attribution_sampled(req):
                return
            self._attr_snapshots[id(req)] = self.snapshot_context(req)
        except Exception:
            pass

    def pop_injection(self, req: ProviderRequest, umo: str) -> dict[str, Any] | None:
        """tail 钩子调用：取出初始快照，与最终快照对比，返回注入归因。

        Args:
            req: 最终的 ProviderRequest（所有高优先级钩子已执行完毕）。
            umo: 会话标识，用于把结果挂到 ``_attr_last`` 供 ``/attribution``
                与 ``collect_response`` 读取。

        Returns:
            ``{"initial": {...}, "final": {...}, "injected": {component: delta},
            "injected_total": int}``；无初始快照（head 未采）则返回 None。
            ``injected`` 跟踪 ``system`` / ``tools`` / ``history`` / ``extra``
            四维的 head→tail 增量（``user`` 为用户原始发言，不参与注入差）。
            结果同时写入 ``_attr_last[umo]``。
        """
        self.__init_attribution__()
        try:
            initial = self._attr_snapshots.pop(id(req), None)
            if initial is None:
                return None
            final = self.snapshot_context(req)
            injected = {
                k: max(0, int(final.get(k, 0)) - int(initial.get(k, 0)))
                for k in ("system", "tools", "history", "extra")
            }
            injected_total = sum(injected.values())
            result: dict[str, Any] = {
                "initial": initial,
                "final": final,
                "injected": injected,
                "injected_total": injected_total,
            }
            if umo:
                self._attr_last[umo] = result
                self._last_sp[umo] = getattr(req, "system_prompt", "") or ""
            return result
        except Exception:
            return None

    def consume_last_injection(self, umo: str) -> dict[str, Any] | None:
        """读取（不删除）指定会话最近一次注入归因。

        供 ``SupplementMixin.collect_response`` 把 ``injection_total`` /
        ``attribution`` 写入补充记录，以及 ``/attribution`` 命令展示。
        """
        self.__init_attribution__()
        try:
            return self._attr_last.get(umo)
        except Exception:
            return None

    def last_system_prompt(self, umo: str) -> str:
        """读取指定会话最近一次 system prompt 文本（供 ``/optimize``）。"""
        self.__init_attribution__()
        try:
            return self._last_sp.get(umo, "")
        except Exception:
            return ""
