"""价格源 adapter：把各源原始 JSON 转换为标准化 :class:`CatalogPrice`。

支持的源：

- ``modelsdev``：https://models.dev/catalog.json（价格已是 $/1M，含 context/service tier）
- ``litellm``：LiteLLM ``model_prices_and_context_window.json``（per-token，×1e6）
- ``openrouter``：https://openrouter.ai/api/v1/models（per-token 字符串，×1e6）
- ``newapi:<provider_id>``：自建 New API 的 ``/api/pricing``（倍率换算）

所有 adapter 均为纯函数，输入原始 JSON dict，输出 ``dict[source_model_id, CatalogPrice]``。
单条解析异常 try/except 计数 ``skipped``，不丢弃整源。换算公式与 New API 上游保持一致：
``1 ratio = $2 / 1M tokens``（``prompt = model_ratio × 2``）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .expr_eval import validate_tiered_expr
from .price_catalog import CatalogPrice, SourceStatus, load_catalog

SOURCE_MODELS_DEV = "modelsdev"
SOURCE_LITELLM = "litellm"
SOURCE_OPENROUTER = "openrouter"

PUBLIC_SOURCES: tuple[str, ...] = (SOURCE_MODELS_DEV, SOURCE_LITELLM, SOURCE_OPENROUTER)

SOURCE_URLS: dict[str, str] = {
    SOURCE_MODELS_DEV: "https://models.dev/catalog.json",
    SOURCE_LITELLM: (
        "https://raw.githubusercontent.com/BerriAI/litellm/"
        "main/model_prices_and_context_window.json"
    ),
    SOURCE_OPENROUTER: "https://openrouter.ai/api/v1/models",
}

# New API 倍率到 $/1M 的换算常数：1 ratio = $0.002/1K = $2/1M。
NEWAPI_RATIO_TO_PRICE = 2.0
# New API 缓存写缺省倍率（上游默认 1.25 × 输入价）。
NEWAPI_DEFAULT_CREATE_CACHE_RATIO = 1.25

PER_TOKEN_MULTIPLIER = 1_000_000


# ---- 公共工具 ----


def _to_float(v: Any) -> float | None:
    """安全转非负 float；None/空串/非法返回 None。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _first_float(d: dict, *keys: str) -> tuple[float | None, bool]:
    """按顺序返回第一个存在且可转 float 的值；返回 (值, 是否命中)。"""
    for k in keys:
        if k in d:
            f = _to_float(d.get(k))
            if f is not None:
                return f, True
    return None, False


def _per_million(v: float | None) -> float | None:
    """per-token 单价 × 1e6 转 $/1M；None 透传。"""
    return v * PER_TOKEN_MULTIPLIER if v is not None else None


def _has_any_price(configured: dict[str, bool]) -> bool:
    return any(configured.values())


# ---- models.dev ----


def adapt_modelsdev(raw: dict) -> dict[str, CatalogPrice]:
    """解析 models.dev catalog.json。

    顶层可能是 ``{"providers": {pid: {"models": {mid: msg}}}}``（新格式）或直接
    ``{pid: {"models": {...}}}``（旧格式）。cost 字段 ``input/output/cache_read/cache_write``
    已是 $/1M；``tiers`` 映射为 context_tiers，``experimental.modes.fast`` 映射为 service_tier。
    """
    providers = _modelsdev_providers(raw)
    out: dict[str, CatalogPrice] = {}
    for provider_id, provider in providers.items():
        pid = str(provider_id or "").strip()
        if not pid or not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, msg in models.items():
            mid = str(model_id or "").strip()
            if not mid or not isinstance(msg, dict):
                continue
            try:
                price = _modelsdev_price(pid, mid, msg)
            except Exception:
                continue
            if price is not None:
                out[price.source_model_id] = price
    return out


def _modelsdev_providers(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    providers = raw.get("providers")
    if isinstance(providers, dict):
        return providers
    # 旧格式：顶层即 provider map（值含 models 字段）
    return {k: v for k, v in raw.items() if isinstance(v, dict) and "models" in v}


def _modelsdev_price(pid: str, mid: str, msg: dict) -> CatalogPrice | None:
    cost = msg.get("cost")
    if not isinstance(cost, dict):
        return None
    prompt, hp = _to_float(cost.get("input")), "input" in cost
    completion, hc = _to_float(cost.get("output")), "output" in cost
    cache_read, hcr = _to_float(cost.get("cache_read")), "cache_read" in cost
    cache_creation, hcc = _to_float(cost.get("cache_write")), "cache_write" in cost
    configured = {
        "prompt": hp and prompt is not None,
        "completion": hc and completion is not None,
        "cache_read": hcr and cache_read is not None,
        "cache_creation": hcc and cache_creation is not None,
    }
    if not _has_any_price(configured):
        return None
    source_model_id = f"{pid}/{mid}"
    context_tiers = _modelsdev_context_tiers(cost)
    service_tiers = _modelsdev_service_tiers(msg)
    mode = "per_tier" if context_tiers or service_tiers else "per_token"
    return CatalogPrice(
        source=SOURCE_MODELS_DEV,
        source_model_id=source_model_id,
        mode=mode,
        prompt=prompt,
        completion=completion,
        cache=cache_read,
        cache_read=cache_read,
        cache_creation=cache_creation,
        configured=configured,
        context_tiers=context_tiers,
        service_tiers=service_tiers,
        raw=msg,
    )


def _modelsdev_context_tiers(cost: dict) -> list[dict]:
    raw_tiers = cost.get("tiers")
    if not isinstance(raw_tiers, list):
        return []
    tiers: list[dict] = []
    for item in raw_tiers:
        if not isinstance(item, dict):
            continue
        descriptor = item.get("tier")
        if not isinstance(descriptor, dict) or str(descriptor.get("type", "")).lower() != "context":
            continue
        threshold = _to_float(descriptor.get("size"))
        if threshold is None:
            return []
        prompt, hp = _to_float(item.get("input")), "input" in item
        completion, hc = _to_float(item.get("output")), "output" in item
        cache_read, hcr = _to_float(item.get("cache_read")), "cache_read" in item
        cache_creation, hcc = _to_float(item.get("cache_write")), "cache_write" in item
        if not (hp or hc or hcr or hcc):
            return []
        tiers.append(
            {
                "threshold_tokens": int(threshold),
                "prompt": prompt,
                "completion": completion,
                "cache_read": cache_read,
                "cache_creation": cache_creation,
                "configured": {
                    "prompt": hp,
                    "completion": hc,
                    "cache_read": hcr,
                    "cache_creation": hcc,
                },
            }
        )
    return tiers


def _modelsdev_service_tiers(msg: dict) -> list[dict]:
    experimental = msg.get("experimental")
    if not isinstance(experimental, dict):
        return []
    modes = experimental.get("modes")
    if not isinstance(modes, dict):
        return []
    fast = modes.get("fast")
    if not isinstance(fast, dict):
        return []
    cost = fast.get("cost")
    if not isinstance(cost, dict):
        return []
    prompt, hp = _to_float(cost.get("input")), "input" in cost
    completion, hc = _to_float(cost.get("output")), "output" in cost
    cache_read, hcr = _to_float(cost.get("cache_read")), "cache_read" in cost
    cache_creation, hcc = _to_float(cost.get("cache_write")), "cache_write" in cost
    if not (hp or hc or hcr or hcc):
        return []
    return [
        {
            "mode": "fast",
            "service_tier": "priority",
            "match": "priority",
            "prompt": prompt,
            "completion": completion,
            "cache_read": cache_read,
            "cache_creation": cache_creation,
            "configured": {
                "prompt": hp,
                "completion": hc,
                "cache_read": hcr,
                "cache_creation": hcc,
            },
        }
    ]


# ---- LiteLLM ----


def adapt_litellm(raw: dict) -> dict[str, CatalogPrice]:
    """解析 LiteLLM model_prices_and_context_window.json（per-token → ×1e6）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, CatalogPrice] = {}
    for model_id, entry in raw.items():
        mid = str(model_id or "").strip()
        if not mid or not isinstance(entry, dict):
            continue
        try:
            price = _litellm_price(mid, entry)
        except Exception:
            continue
        if price is not None:
            out[mid] = price
    return out


def _litellm_price(mid: str, entry: dict) -> CatalogPrice | None:
    prompt, hp = _first_float(entry, "input_cost_per_token")
    completion, hc = _first_float(entry, "output_cost_per_token")
    cache_read, hcr = _first_float(entry, "cache_read_input_token_cost", "input_cache_read")
    cache_creation, hcc = _first_float(
        entry,
        "cache_creation_input_token_cost",
        "cache_write_input_token_cost",
        "input_cache_write",
        "input_cache_creation",
    )
    configured = {
        "prompt": hp,
        "completion": hc,
        "cache_read": hcr,
        "cache_creation": hcc,
    }
    if not _has_any_price(configured):
        return None
    return CatalogPrice(
        source=SOURCE_LITELLM,
        source_model_id=mid,
        mode="per_token",
        prompt=_per_million(prompt),
        completion=_per_million(completion),
        cache=_per_million(cache_read),
        cache_read=_per_million(cache_read),
        cache_creation=_per_million(cache_creation),
        configured=configured,
        raw=entry,
    )


# ---- OpenRouter ----


def adapt_openrouter(raw: dict) -> dict[str, CatalogPrice]:
    """解析 OpenRouter /api/v1/models（pricing 为 per-token 字符串 → ×1e6）。"""
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if not isinstance(data, list):
        return {}
    out: dict[str, CatalogPrice] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        pricing = item.get("pricing")
        if not mid or not isinstance(pricing, dict):
            continue
        try:
            price = _openrouter_price(mid, pricing, item)
        except Exception:
            continue
        if price is not None:
            out[mid] = price
    return out


def _openrouter_price(mid: str, pricing: dict, item: dict) -> CatalogPrice | None:
    prompt, hp = _to_float(pricing.get("prompt")), "prompt" in pricing
    completion, hc = _to_float(pricing.get("completion")), "completion" in pricing
    cache_read, hcr = _first_float(pricing, "input_cache_read", "cache_read_input_token_cost")
    cache_creation, hcc = _first_float(
        pricing,
        "input_cache_write",
        "input_cache_creation",
        "cache_creation_input_token_cost",
        "cache_write_input_token_cost",
    )
    configured = {
        "prompt": hp and prompt is not None,
        "completion": hc and completion is not None,
        "cache_read": hcr,
        "cache_creation": hcc,
    }
    if not _has_any_price(configured):
        return None
    return CatalogPrice(
        source=SOURCE_OPENROUTER,
        source_model_id=mid,
        mode="per_token",
        prompt=_per_million(prompt),
        completion=_per_million(completion),
        cache=_per_million(cache_read),
        cache_read=_per_million(cache_read),
        cache_creation=_per_million(cache_creation),
        configured=configured,
        raw=item,
    )


# ---- New API ----


def is_newapi_source(source: str) -> bool:
    return source.startswith("newapi:")


def newapi_provider_id(source: str) -> str | None:
    """``newapi:<provider_id>`` → provider_id；非 New API 源返回 None。"""
    if not is_newapi_source(source):
        return None
    pid = source.split(":", 1)[1].strip()
    return pid or None


def derive_newapi_root(api_base: str) -> str:
    """从 provider 的 api_base 推导 New API 实例根地址。

    - 去尾部 ``/``；
    - 以 ``/v1`` 结尾则去掉（``https://na/x/v1`` → ``https://na/x``）；
    - 非 http(s) 字符串原样返回（调用方负责校验）。
    """
    base = str(api_base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.lower().endswith("/v1"):
        base = base[: -len("/v1")]
    return base.rstrip("/")


def adapt_newapi(raw: dict, source: str) -> dict[str, CatalogPrice]:
    """解析 New API ``/api/pricing`` 响应。

    - ``quota_type=0``（倍率）：``prompt=model_ratio×2``，其余按 ratio 派生；
    - ``quota_type=1``（固定价）：mode=per_turn，price=model_price；
    - ``billing_mode=tiered_expr`` + ``billing_expr``：mode=tiered_expr，表达式原文
      保留（含上游 ``v1:`` 版本前缀，编译时由 expr_eval 剥离），导入前先做白名单
      验证，非法条目整条跳过；
    - 忽略 ``group_ratio`` / ``enable_groups``（分组加价是售卖层而非上游成本）。
    """
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if not isinstance(data, list):
        return {}
    out: dict[str, CatalogPrice] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("model_name") or "").strip()
        if not mid:
            continue
        try:
            price = _newapi_price(mid, item, source)
        except Exception:
            continue
        if price is not None:
            out[mid] = price
    return out


def _newapi_price(mid: str, item: dict, source: str) -> CatalogPrice | None:
    billing_mode = str(item.get("billing_mode") or "").strip().lower()
    expr = str(item.get("billing_expr") or "").strip()
    if billing_mode == "tiered_expr":
        if not expr:
            _logger.debug(
                "[cost_control] newapi tiered_expr 导入拒绝 source=%s model=%s class=missing_expr",
                source,
                mid,
            )
            return None
        # 导入前验证（_compile 统一剥 v1: 前缀后编译求值）：非法表达式不进目录，
        # 避免成为候选后运行时才失败；原文仍原样入目录供展示。
        err = validate_tiered_expr(expr)
        if err is not None:
            _logger.debug(
                "[cost_control] newapi tiered_expr 导入拒绝 source=%s model=%s class=%s",
                source,
                mid,
                err.split("：", 1)[0],
            )
            return None
        return CatalogPrice(
            source=source,
            source_model_id=mid,
            mode="tiered_expr",
            expr=expr,
            configured={
                "prompt": False,
                "completion": False,
                "cache_read": False,
                "cache_creation": False,
            },
            raw=item,
        )

    quota_type = item.get("quota_type")
    try:
        quota_type = int(quota_type) if quota_type is not None else 0
    except (TypeError, ValueError):
        quota_type = 0

    if quota_type == 1:
        price = _to_float(item.get("model_price"))
        if price is None:
            return None
        return CatalogPrice(
            source=source,
            source_model_id=mid,
            mode="per_turn",
            price=price,
            configured={
                "prompt": False,
                "completion": False,
                "cache_read": False,
                "cache_creation": False,
            },
            raw=item,
        )

    # quota_type=0：倍率模式
    model_ratio = _to_float(item.get("model_ratio"))
    if model_ratio is None:
        return None
    prompt = model_ratio * NEWAPI_RATIO_TO_PRICE

    completion_ratio = _to_float(item.get("completion_ratio"))
    completion = prompt * completion_ratio if completion_ratio is not None else prompt

    cache_ratio = _to_float(item.get("cache_ratio"))
    cache_read = prompt * cache_ratio if cache_ratio is not None else prompt

    create_cache_ratio = _to_float(item.get("create_cache_ratio"))
    cache_creation = (
        prompt * create_cache_ratio
        if create_cache_ratio is not None
        else prompt * NEWAPI_DEFAULT_CREATE_CACHE_RATIO
    )

    return CatalogPrice(
        source=source,
        source_model_id=mid,
        mode="per_token",
        prompt=prompt,
        completion=completion,
        cache=cache_read,
        cache_read=cache_read,
        cache_creation=cache_creation,
        configured={
            "prompt": True,
            "completion": completion_ratio is not None,
            "cache_read": cache_ratio is not None,
            "cache_creation": create_cache_ratio is not None,
        },
        raw=item,
    )


# ---- 源调度 ----


def adapt_source(source: str, raw: dict) -> dict[str, CatalogPrice]:
    """按 source ID 分发到对应 adapter。未知源返回空 dict。"""
    if source == SOURCE_MODELS_DEV:
        return adapt_modelsdev(raw)
    if source == SOURCE_LITELLM:
        return adapt_litellm(raw)
    if source == SOURCE_OPENROUTER:
        return adapt_openrouter(raw)
    if is_newapi_source(source):
        return adapt_newapi(raw, source)
    return {}


def newapi_pricing_url(root: str) -> str:
    """返回 New API 实例的 ``/api/pricing`` 完整 URL。"""
    base = str(root or "").strip().rstrip("/")
    if not base:
        return "/api/pricing"
    return f"{base}/api/pricing"


def is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# ---- 同步执行器 ----

# 提供者配置回调：provider_id -> {"api_base": str, "key": [str] | str, ...}。
# 由 web_api 层注入（从 AstrBot provider 配置读取），避免本模块依赖 Star 上下文。
ProviderCfgFn = Callable[[str], dict[str, Any] | None]

_logger = logging.getLogger(__name__)

_UA = {"Accept": "application/json", "User-Agent": "cost-control/1.0"}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

# Locks are loop-local because asyncio primitives cannot be shared safely across loops.
_SYNC_LOCKS: weakref.WeakKeyDictionary[Any, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


@dataclass
class SourceResult:
    """单源同步结果。``prices`` 为瞬态字段，不参与 to_dict。"""

    source: str
    status: str = "ok"  # ok | error
    models: int = 0
    skipped: int = 0
    error: str = ""
    etag: str = ""
    not_modified: bool = False
    base_url: str = ""
    provider_id: str = ""
    prices: dict[str, CatalogPrice] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "models": self.models,
            "skipped": self.skipped,
            "error": self.error,
            "not_modified": self.not_modified,
        }

    def to_status(self, prev: SourceStatus | None, *, enabled: bool = True) -> SourceStatus:
        """合并成本次要写入目录的 :class:`SourceStatus`。失败保留旧的 models/etag。"""
        prev_models = prev.models if prev else 0
        prev_etag = prev.etag if prev else ""
        keep = self.status != "ok" or self.not_modified
        return SourceStatus(
            source=self.source,
            enabled=enabled,
            status=self.status,
            updated_at=_now_iso() if self.status == "ok" else (prev.updated_at if prev else ""),
            models=prev_models if keep and prev_models else self.models,
            skipped=self.skipped,
            error=self.error,
            etag=self.etag or (prev_etag if keep else ""),
            provider_id=self.provider_id or (prev.provider_id if prev else None),
            base_url=self.base_url or (prev.base_url if prev else None),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _source_settings(cfg: dict[str, Any] | None, source: str) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    raw = cfg.get("price_sources")
    if not isinstance(raw, dict):
        return {}
    s = raw.get(source)
    return dict(s) if isinstance(s, dict) else {}


def merged_provider_config(
    config: dict[str, Any] | None,
    provider_id: str,
) -> dict[str, Any] | None:
    """按 id 定位 AstrBot provider，并合并其 ``provider_sources`` 来源配置。

    与 AstrBot ``ProviderManager.get_merged_provider_config`` 同口径：
    ``{**provider_source, **provider}``（provider 字段优先级更高），合并后保留
    provider 自身的 ``id``。>=4.25.5 时 api_base/key 往往只在 ``provider_sources[]``
    条目上，必须经此回填才能用于价格源请求头；平铺旧结构无
    ``provider_source_id``，浅拷贝原样返回。
    """
    pid = str(provider_id or "").strip()
    if not pid or not isinstance(config, dict):
        return None
    prov_list = config.get("provider")
    if not isinstance(prov_list, list):
        return None
    target: dict[str, Any] | None = None
    for p in prov_list:
        if isinstance(p, dict) and str(p.get("id") or "").strip() == pid:
            target = p
            break
    if target is None:
        return None
    sid = str(target.get("provider_source_id") or "").strip()
    if not sid:
        return dict(target)
    source_list = config.get("provider_sources")
    source: dict[str, Any] | None = None
    if isinstance(source_list, list):
        for s in source_list:
            if isinstance(s, dict) and str(s.get("id") or "").strip() == sid:
                source = s
                break
    if source is None:
        return dict(target)
    merged = {**source, **target}
    merged["id"] = pid
    return merged


def _log_origin(url: str) -> str:
    """日志专用归一化 origin：仅保留 scheme://host[:port]，剥掉 userinfo 与 path。"""
    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    port_text = f":{port}" if port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port_text}"


def _origin_tuple(url: str) -> tuple[str, str, int] | None:
    """返回可比较的 HTTP origin；默认端口归一化，非法 URL 返回 None。"""
    try:
        parsed = urlparse(str(url or ""))
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if scheme not in ("http", "https") or not host:
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def _resolve_source_request(
    source: str,
    cfg: dict[str, Any] | None,
    provider_cfg: ProviderCfgFn | None,
) -> tuple[str, dict[str, str], str, str, str]:
    """解析源的请求 URL 与头。返回 ``(url, headers, error, base_url, provider_id)``。"""
    if source in SOURCE_URLS:
        return SOURCE_URLS[source], {}, "", "", ""
    if is_newapi_source(source):
        settings = _source_settings(cfg, source)
        # 源设置里的 provider_id 是权威指向；缺省才回退旧版「源 ID 后缀即 provider」
        pid = str(settings.get("provider_id") or "").strip() or newapi_provider_id(source) or ""
        base_url = derive_newapi_root(str(settings.get("base_url") or ""))
        provider = provider_cfg(pid) if (provider_cfg and pid) else None
        provider_base_url = ""
        if provider:
            provider_base_url = derive_newapi_root(str(provider.get("api_base") or ""))
        if not base_url and provider:
            # settings 未配时从 provider 推导；api_base 可能带 /v1，统一剥掉
            base_url = provider_base_url
        token = ""
        if settings.get("use_provider_key", True) and provider:
            token = _provider_token(provider)
        # 已绑定 provider 且两侧都提供了可比较 origin 时，地址必须保持一致；
        # 即使当前没有 key 也不能借绑定关系访问另一处配置的端点。
        request_origin = _origin_tuple(base_url)
        provider_origin = _origin_tuple(provider_base_url)
        if provider and provider_origin is not None and request_origin != provider_origin:
            mismatch_class = "credential_origin_mismatch" if token else "provider_origin_mismatch"
            _logger.debug(
                "[cost_control] 价格源端点拒绝: source=%s provider_id=%s "
                "request_origin=%s provider_origin=%s class=%s",
                source,
                pid,
                _log_origin(base_url) or "-",
                _log_origin(provider_base_url) or "-",
                mismatch_class,
            )
            return (
                "",
                {},
                f"New API 源 {source} 的 base_url 与 provider api_base 不同源，已拒绝请求",
                base_url,
                pid,
            )
        if token and request_origin is not None and request_origin[0] == "http":
            # 内网自建 New API 可能只能使用 HTTP；保留兼容性，但明确提示 Bearer
            # 会以明文传输。日志只记录归一化 origin，不记录密钥本体。
            _logger.warning(
                "[cost_control] 价格源使用明文 HTTP Bearer 凭据: source=%s "
                "provider_id=%s origin=%s class=insecure_http_bearer; "
                "建议改用 HTTPS",
                source,
                pid,
                _log_origin(base_url) or "-",
            )
        headers: dict[str, str] = {}
        if token:
            if (
                request_origin is None
                or provider_origin is None
                or request_origin != provider_origin
            ):
                _logger.debug(
                    "[cost_control] 价格源凭据拒绝: source=%s provider_id=%s "
                    "request_origin=%s provider_origin=%s class=credential_origin_mismatch",
                    source,
                    pid,
                    _log_origin(base_url) or "-",
                    _log_origin(provider_base_url) or "-",
                )
                return (
                    "",
                    {},
                    f"New API 源 {source} 的 base_url 与 provider 凭据不同源，已拒绝发送密钥",
                    base_url,
                    pid,
                )
            headers["Authorization"] = f"Bearer {token}"
        # 诊断只含结构化字段与归一化 origin（剥离 userinfo/path），绝不记录密钥本体
        _logger.debug(
            "[cost_control] 价格源同步解析: source=%s provider_id=%s origin=%s has_key=%s",
            source,
            pid,
            _log_origin(base_url) or "-",
            bool(token),
        )
        if not base_url:
            return "", {}, f"New API 源 {source} 缺少 base_url 且无法从 provider 推导", "", pid
        if not is_http_url(base_url):
            return "", {}, f"New API 源 {source} base_url 非法: {base_url!r}", base_url, pid
        return newapi_pricing_url(base_url), headers, "", base_url, pid
    return "", {}, f"未知价格源: {source}", "", ""


def _provider_token(provider: dict[str, Any]) -> str:
    key = provider.get("key")
    candidates = key if isinstance(key, list) else [key]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        value = candidate.strip()
        env_name = ""
        if value.startswith("${") and value.endswith("}"):
            env_name = value[2:-1].strip()
        elif value.startswith("$"):
            env_name = value[1:].strip()
        if env_name:
            value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


class _CredentialRedirectHandler(urllib.request.HTTPRedirectHandler):
    """带 Authorization 的同步不跟随重定向，防止凭据被转发。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        parsed = urlparse(req.full_url)
        if (
            req.get_header("Authorization")
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "credentialed redirect blocked",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[bytes | None, int, dict[str, str], str]:
    """同步 HTTP GET。返回 ``(body, status, resp_headers, error)``；error 非空即失败。"""
    req = urllib.request.Request(url, headers={**_UA, **headers})
    try:
        parsed_url = urlparse(url)
        has_url_credentials = parsed_url.username is not None or parsed_url.password is not None
        if headers.get("Authorization") or has_url_credentials:
            opener = urllib.request.build_opener(_CredentialRedirectHandler())
            response = opener.open(req, timeout=timeout)  # noqa: S310
        else:
            response = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        with response as resp:
            content_length = resp.headers.get("Content-Length")
            try:
                declared_size = int(content_length) if content_length is not None else 0
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > MAX_RESPONSE_BYTES:
                _logger.debug(
                    "[cost_control] 价格源响应拒绝 origin=%s class=content_length_exceeded "
                    "declared_bytes=%s limit_bytes=%s",
                    _log_origin(url) or "-",
                    declared_size,
                    MAX_RESPONSE_BYTES,
                )
                return None, resp.status, dict(resp.headers), "响应体超过大小限制"
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                _logger.debug(
                    "[cost_control] 价格源响应拒绝 origin=%s class=response_body_exceeded "
                    "limit_bytes=%s",
                    _log_origin(url) or "-",
                    MAX_RESPONSE_BYTES,
                )
                return None, resp.status, dict(resp.headers), "响应体超过大小限制"
            _logger.debug(
                "[cost_control] 价格源 HTTP 完成 origin=%s status=%s bytes=%s",
                _log_origin(url) or "-",
                resp.status,
                len(body),
            )
            return body, resp.status, dict(resp.headers), ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            _logger.debug(
                "[cost_control] 价格源 HTTP 未变更 origin=%s status=304",
                _log_origin(url) or "-",
            )
            return None, 304, dict(e.headers or {}), ""
        _logger.debug(
            "[cost_control] 价格源 HTTP 失败 origin=%s class=HTTPError status=%s",
            _log_origin(url) or "-",
            e.code,
        )
        return None, e.code, {}, f"HTTP {e.code}"
    except Exception as e:
        _logger.debug(
            "[cost_control] 价格源 HTTP 失败 origin=%s class=%s",
            _log_origin(url) or "-",
            type(e).__name__,
        )
        return None, 0, {}, str(e)


def _raw_entry_count(source: str, raw: Any) -> int:
    """统计原始响应中的条目数（用于 skipped = raw - adapted）。"""
    if source == SOURCE_MODELS_DEV:
        providers = _modelsdev_providers(raw if isinstance(raw, dict) else {})
        total = 0
        for p in providers.values():
            if isinstance(p, dict):
                models = p.get("models")
                total += len(models) if isinstance(models, dict) else 0
        return total
    if source == SOURCE_LITELLM:
        return len(raw) if isinstance(raw, dict) else 0
    if source == SOURCE_OPENROUTER or is_newapi_source(source):
        data = raw.get("data") if isinstance(raw, dict) else None
        return len(data) if isinstance(data, list) else 0
    return 0


def sync_source(
    source: str,
    prev: SourceStatus | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    timeout: float = 10.0,
    provider_cfg: ProviderCfgFn | None = None,
) -> SourceResult:
    """同步单个源（纯函数，不改 catalog）。成功时 ``result.prices`` 为新条目。"""
    result = SourceResult(source=source)
    url, headers, err, base_url, pid = _resolve_source_request(source, cfg, provider_cfg)
    result.base_url = base_url
    result.provider_id = pid
    if err:
        result.status = "error"
        result.error = err
        _logger.debug(
            "[cost_control] 价格源同步拒绝 source=%s class=resolve_error",
            source,
        )
        return result
    if source == SOURCE_MODELS_DEV and prev and prev.etag:
        headers["If-None-Match"] = prev.etag
    body, status, resp_headers, err = http_get(url, headers, timeout)
    if status == 304:
        result.status = "ok"
        result.not_modified = True
        result.models = prev.models if prev else 0
        result.etag = prev.etag if prev else ""
        _logger.debug(
            "[cost_control] 价格源同步未变更 source=%s models=%s",
            source,
            result.models,
        )
        return result
    if err:
        result.status = "error"
        result.error = err
        _logger.debug(
            "[cost_control] 价格源同步失败 source=%s class=http_error status=%s",
            source,
            status,
        )
        return result
    try:
        raw = json.loads(body.decode("utf-8")) if body is not None else {}
    except Exception as e:
        result.status = "error"
        result.error = f"JSON 解析失败: {e}"
        _logger.debug(
            "[cost_control] 价格源响应拒绝 source=%s class=json_decode_error",
            source,
        )
        return result
    prices = adapt_source(source, raw if isinstance(raw, dict) else {})
    # HTTP 200 但无法生成任何有效价格时一律拒绝，避免首次空同步被记为成功，
    # 以及后续同步清空已持久化的有效目录。
    if not prices:
        result.status = "error"
        suffix = "（保留旧数据）" if prev is not None and int(prev.models or 0) > 0 else ""
        result.error = f"价格源 {source} 响应为空或无法解析出有效价格{suffix}"
        _logger.debug(
            "[cost_control] 价格源响应拒绝 source=%s class=empty_or_invalid_schema had_previous=%s",
            source,
            bool(prev is not None and int(prev.models or 0) > 0),
        )
        return result
    result.prices = prices
    result.models = len(prices)
    result.skipped = max(0, _raw_entry_count(source, raw) - len(prices))
    etag = resp_headers.get("ETag") or resp_headers.get("etag") or ""
    result.etag = etag.strip()
    _logger.debug(
        "[cost_control] 价格源同步完成 source=%s models=%s skipped=%s etag=%s",
        source,
        result.models,
        result.skipped,
        bool(result.etag),
    )
    return result


def _sync_lock(data_dir: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _SYNC_LOCKS.setdefault(loop, {})
    key = os.path.abspath(data_dir)
    return locks.setdefault(key, asyncio.Lock())


async def sync_all(
    cfg: dict[str, Any] | None,
    data_dir: str,
    *,
    sources: list[str] | None = None,
    timeout: float = 10.0,
    concurrency: int = 8,
    provider_cfg: ProviderCfgFn | None = None,
) -> dict[str, Any]:
    """并发同步所有启用源（或指定源），串行落盘并原子保存目录。

    同步纯在线程中执行（不改共享 catalog），结果回到事件循环串行应用，避免并发写竞争。
    某源失败不阻断其它源；失败源保留旧价格。

    Returns:
        ``{"ok": bool, "updated_at": str, "results": [SourceResult.to_dict(), ...]}``。
    """
    lock = _sync_lock(data_dir)
    if lock.locked():
        _logger.debug(
            "[cost_control] 价格目录同步等待锁 data_dir=%s class=sync_serialized",
            os.path.abspath(data_dir),
        )
    async with lock:
        return await _sync_all_locked(
            cfg,
            data_dir,
            sources=sources,
            timeout=timeout,
            concurrency=concurrency,
            provider_cfg=provider_cfg,
        )


async def _sync_all_locked(
    cfg: dict[str, Any] | None,
    data_dir: str,
    *,
    sources: list[str] | None,
    timeout: float,
    concurrency: int,
    provider_cfg: ProviderCfgFn | None,
) -> dict[str, Any]:
    catalog = load_catalog(data_dir)
    from .config import get_enabled_price_sources, get_price_sources

    source_cfg = get_price_sources(cfg)
    if sources is None:
        targets = get_enabled_price_sources(cfg)
    else:
        targets = [s for s in sources if isinstance(s, str) and s.strip()]
    # 去重保持顺序
    seen: set[str] = set()
    targets = [s for s in targets if not (s in seen or seen.add(s))]
    if not targets:
        return {"ok": True, "updated_at": catalog.updated_at, "results": []}

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(source: str) -> SourceResult:
        async with sem:
            prev = catalog.get_source(source)
            return await asyncio.to_thread(
                sync_source,
                source,
                prev,
                cfg=cfg,
                timeout=timeout,
                provider_cfg=provider_cfg,
            )

    results = await asyncio.gather(*(_one(s) for s in targets))

    for res in results:
        prev = catalog.get_source(res.source)
        if res.status == "ok" and not res.not_modified:
            catalog.replace_source_prices(res.source, {p.price_key: p for p in res.prices.values()})
        enabled = bool(source_cfg.get(res.source, {}).get("enabled", True))
        catalog.upsert_source(res.to_status(prev, enabled=enabled))
    catalog.updated_at = _now_iso()
    catalog.save(data_dir)

    return {
        "ok": all(r.status == "ok" for r in results),
        "updated_at": catalog.updated_at,
        "results": [r.to_dict() for r in results],
    }


__all__ = [
    "PUBLIC_SOURCES",
    "SOURCE_LITELLM",
    "SOURCE_MODELS_DEV",
    "SOURCE_OPENROUTER",
    "SOURCE_URLS",
    "ProviderCfgFn",
    "SourceResult",
    "adapt_litellm",
    "adapt_modelsdev",
    "adapt_newapi",
    "adapt_openrouter",
    "adapt_source",
    "derive_newapi_root",
    "http_get",
    "is_http_url",
    "is_newapi_source",
    "merged_provider_config",
    "newapi_pricing_url",
    "newapi_provider_id",
    "sync_all",
    "sync_source",
]
