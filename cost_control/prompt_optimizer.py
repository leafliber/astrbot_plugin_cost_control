"""提示词优化 Mixin。

对 system prompt 做静态分析（长度 / token 估算 / 冗余检测 / 重复模式 / 可缓存性
评估），并支持经 LLM 改写以降低 token 消耗、提升缓存命中率。

LLM 改写（已核对 ``astrbot/core/provider/``）：
- 通过 ``context.get_provider_by_id(id)``（指定 provider）或
  ``context.get_using_provider(umo)``（当前会话 provider）取得 ``Provider``。
- 调 ``provider.text_chat(prompt=..., system_prompt=...)`` → ``LLMResponse``，
  取 ``.completion_text``。

可测性设计：``analyze_prompt`` 为纯函数（经模块级 ``_analyze`` 实现），不依赖
astrbot，可单测；LLM 调用在 ``PromptOptimizerMixin.rewrite_prompt`` 内进行。

阶段 3 实现。
"""

from __future__ import annotations

import re
from typing import Any

from .attributor import _str_tokens
from .config import get_config

# 改写器人设：指导 LLM 如何压缩与稳定 system prompt。
_REWRITER_PERSONA = (
    "你是 prompt 工程专家。你的任务是把给定的 system prompt 改写得更精简、稳定：\n"
    "1. 删除冗余、重复表述与不必要的客套话；\n"
    "2. 把会变动的动态信息（日期、用户名、计数等）替换为稳定占位符或移到末尾；\n"
    "3. 保持核心规则与行为约束完全不变；\n"
    "4. 保留中文，输出纯文本，不要解释、不要加 markdown 代码块。"
)

_REWRITE_INSTRUCTION = (
    "请改写以下 system prompt，使其 token 更少且前缀更稳定（更利于 prompt cache）：\n\n"
    "---\n{prompt}\n---\n\n直接输出改写后的 system prompt。"
)

# 明显的动态占位符模式：日期、模板变量、时间戳等（用于可缓存性扣分）。
_DYNAMIC_PATTERNS = [
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),  # 日期
    re.compile(r"\d{2}:\d{2}(:\d{2})?"),  # 时间
    re.compile(r"\$\{[^}]+\}"),  # ${var}
    re.compile(r"\{[a-zA-Z_]\w*\}"),  # {var}
    re.compile(r"今天|当前时间|当前日期|现在是"),
]


def _split_blocks(text: str) -> list[str]:
    """把 prompt 按行 / 句子切成块（纯函数）。"""
    # 先按行切，行内再按句末标点切。
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blocks: list[str] = []
    for line in raw_lines:
        parts = re.split(r"[。！？!?；;]", line)
        for p in parts:
            p = p.strip()
            if p:
                blocks.append(p)
    return blocks


def _analyze(system_prompt: str) -> dict[str, Any]:
    """对 system prompt 做静态分析（纯函数）。

    Returns:
        含 ``length`` / ``tokens_est`` / ``redundancy_score`` / ``repeated_blocks`` /
        ``cacheability_score`` / ``suggestions`` 的 dict。
    """
    text = system_prompt or ""
    length = len(text)
    tokens_est = _str_tokens(text)

    blocks = _split_blocks(text)
    seen: dict[str, int] = {}
    for b in blocks:
        seen[b] = seen.get(b, 0) + 1
    repeated = [b for b, n in seen.items() if n > 1]
    repeated_chars = sum(len(b) * (n - 1) for b, n in seen.items() if n > 1)
    redundancy_score = round(repeated_chars * 100.0 / length, 1) if length else 0.0

    # 可缓存性：基础 90，动态模式每命中一个扣 8，冗余每 10% 扣 5。
    dynamic_hits = sum(len(p.findall(text)) for p in _DYNAMIC_PATTERNS)
    cacheability = max(
        0,
        min(100, 90 - dynamic_hits * 8 - int(redundancy_score / 10) * 5),
    )

    suggestions: list[str] = []
    if redundancy_score >= 10:
        suggestions.append(
            f"存在 {len(repeated)} 处重复表述（冗余 {redundancy_score}%），建议合并去重"
        )
    if dynamic_hits:
        suggestions.append(f"检测到 {dynamic_hits} 处动态内容（日期/变量），建议后移以稳定缓存前缀")
    if tokens_est > 2000:
        suggestions.append(f"system prompt 约 {tokens_est} token 偏长，建议精简")
    if not suggestions:
        suggestions.append("未发现明显问题，结构良好")

    return {
        "length": length,
        "tokens_est": tokens_est,
        "redundancy_score": redundancy_score,
        "repeated_blocks": repeated[:10],
        "cacheability_score": cacheability,
        "suggestions": suggestions,
    }


class PromptOptimizerMixin:
    """静态分析 + LLM 改写 system prompt 的 Mixin。"""

    # 由 ``Main`` 宿主提供（Mixin 不定义 ``__init__``）。
    context: Any
    config: Any

    def _optimizer_cfg(self) -> dict[str, Any]:
        cfg = get_config(getattr(self, "config", None), "prompt_optimizer", {}) or {}
        return cfg if isinstance(cfg, dict) else {}

    def analyze_prompt(self, system_prompt: str) -> dict[str, Any]:
        """对 system prompt 做静态分析（转调纯函数）。"""
        return _analyze(system_prompt)

    def _resolve_provider(self, umo: str | None) -> Any:
        """按配置 / 当前会话解析改写用 provider。失败返回 None。"""
        try:
            cfg = self._optimizer_cfg()
            provider_id = str(cfg.get("provider_id", "") or "")
            if provider_id:
                getter = getattr(self.context, "get_provider_by_id", None)
                if getter:
                    return getter(provider_id)
            return self.context.get_using_provider(umo)
        except Exception:
            return None

    async def rewrite_prompt(self, system_prompt: str, umo: str | None = None) -> str:
        """经 LLM 改写 system prompt，返回改写后文本。

        Args:
            system_prompt: 原始 system prompt。
            umo: 会话标识（未指定 provider 时用于取当前会话 provider）。

        Returns:
            改写后的 system prompt 文本。

        Raises:
            RuntimeError: 优化被禁用、找不到 provider、或 LLM 返回空。
            Exception: provider 调用本身的异常透传给调用方（命令层捕获降级）。
        """
        cfg = self._optimizer_cfg()
        if not cfg.get("enabled", True):
            raise RuntimeError("提示词优化已禁用")
        if not (system_prompt or "").strip():
            raise RuntimeError("system prompt 为空")

        prov = self._resolve_provider(umo)
        if prov is None:
            raise RuntimeError("未找到可用的 LLM provider")

        max_len = int(cfg.get("max_static_analysis_length", 8000) or 8000)
        prompt_text = system_prompt[:max_len]
        instruction = _REWRITE_INSTRUCTION.format(prompt=prompt_text)

        resp = await prov.text_chat(prompt=instruction, system_prompt=_REWRITER_PERSONA)
        result = (getattr(resp, "completion_text", None) or "").strip()
        if not result:
            raise RuntimeError("LLM 返回为空")
        # 去除可能的 markdown 代码块包裹。
        if result.startswith("```"):
            result = re.sub(r"^```[a-zA-Z]*\n?", "", result)
            result = re.sub(r"\n?```$", "", result).strip()
        return result
