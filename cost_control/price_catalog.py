"""多源价格目录。

持久化所有价格源拉取并标准化后的价格条目（``price_catalog.json``），并提供按模型名
的多源候选匹配（``find_candidates``）。目录是运行期缓存：启动与计费不依赖网络，同步
失败时保留旧数据（见 :mod:`cost_control.price_sources`）。

价格字段统一为 ``$ / 1M token``：``prompt``（输入）、``completion``（输出）、
``cache_read``（缓存读）、``cache_creation``（缓存写）；``cache`` 为旧版缓存读字段，
缺省回退 ``prompt``。``configured`` 标志区分「源未提供该字段」与「显式为 0」。

匹配复用 :func:`cost_control.cost._normalize_model_name`，评分阶梯：
exact(1.0) > modelsdev canonical(0.95) > normalized(0.90) > prefix(0.70~0.90) >
substring(0.55~0.85)；New API 源 exact 命中排序置顶（自有网关报价最可信）。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .cost import _normalize_model_name

CATALOG_FILENAME = "price_catalog.json"
CATALOG_VERSION = 1

# 候选评分阈值（对齐设计文档）。
AUTO_APPLY_MIN_SCORE = 0.85
CANDIDATE_MIN_SCORE = 0.55
DEFAULT_LIMIT_PER_SOURCE = 8
# 候选匹配缓存上限（超限整体清空重建，防止模型名维度无限增长）。
_CANDIDATE_CACHE_MAX = 1024

# 进程级目录缓存：data_dir → (mtime, size, PriceCatalog)。
# 计费热路径每次 get_pricing 都会 load_catalog，读盘 + json 解析代价高；
# 按 (mtime, size) 失效，save() 落盘后会主动刷新缓存戳。
_catalog_cache: dict[str, tuple[float, int, PriceCatalog]] = {}


@dataclass
class CatalogPrice:
    """单条标准化价格（来自某个源的某个模型）。"""

    source: str
    source_model_id: str
    mode: str = "per_token"  # per_token | per_turn | per_tier | tiered_expr
    prompt: float | None = None
    completion: float | None = None
    cache: float | None = None
    cache_read: float | None = None
    cache_creation: float | None = None
    price: float | None = None
    configured: dict[str, bool] = field(default_factory=dict)
    context_tiers: list[dict[str, Any]] = field(default_factory=list)
    service_tiers: list[dict[str, Any]] = field(default_factory=list)
    expr: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = ""

    @property
    def price_key(self) -> str:
        return f"{self.source}:{self.source_model_id}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatalogPrice:
        if not isinstance(data, dict):
            raise ValueError("price entry must be an object")
        return cls(
            source=str(data.get("source") or ""),
            source_model_id=str(data.get("source_model_id") or ""),
            mode=str(data.get("mode") or "per_token"),
            prompt=_opt_float(data.get("prompt")),
            completion=_opt_float(data.get("completion")),
            cache=_opt_float(data.get("cache")),
            cache_read=_opt_float(data.get("cache_read")),
            cache_creation=_opt_float(data.get("cache_creation")),
            price=_opt_float(data.get("price")),
            configured=_str_bool_dict(data.get("configured")),
            context_tiers=list(data.get("context_tiers") or []),
            service_tiers=list(data.get("service_tiers") or []),
            expr=(str(data["expr"]) if data.get("expr") is not None else None),
            raw=dict(data.get("raw") or {}),
            fetched_at=str(data.get("fetched_at") or ""),
        )


@dataclass
class SourceStatus:
    """单个价格源的同步状态。"""

    source: str
    enabled: bool = True
    status: str = "pending"  # ok | error | pending
    updated_at: str = ""
    models: int = 0
    skipped: int = 0
    error: str = ""
    etag: str = ""
    provider_id: str | None = None
    base_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, source: str, data: dict[str, Any]) -> SourceStatus:
        if not isinstance(data, dict):
            data = {}
        return cls(
            source=source,
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status") or "pending"),
            updated_at=str(data.get("updated_at") or ""),
            models=int(data.get("models", 0) or 0),
            skipped=int(data.get("skipped", 0) or 0),
            error=str(data.get("error") or ""),
            etag=str(data.get("etag") or ""),
            provider_id=(str(data["provider_id"]) if data.get("provider_id") else None),
            base_url=(str(data["base_url"]) if data.get("base_url") else None),
        )


@dataclass
class Candidate:
    """一个待选价格（带评分与匹配原因）。"""

    price_key: str
    source: str
    source_model_id: str
    score: float
    reason: str
    price: CatalogPrice


@dataclass
class PriceCatalog:
    """价格目录：源状态 + 价格条目。"""

    version: int = CATALOG_VERSION
    updated_at: str = ""
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    prices: dict[str, CatalogPrice] = field(default_factory=dict)
    # 候选匹配缓存：key=(model, sources 集合或 None)；prices 变更时必须清空
    _candidate_cache: dict[tuple[str, frozenset[str] | None], list[Candidate]] = field(
        default_factory=dict
    )
    # price_key → to_dict() 扁平缓存：热路径（get_pricing）避免每次全量重建 dict
    _flat_cache: dict[str, dict[str, Any]] | None = None

    # ---- 持久化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "sources": {sid: s.to_dict() for sid, s in self.sources.items()},
            "prices": {key: p.to_dict() for key, p in self.prices.items()},
        }

    @classmethod
    def from_dict(cls, data: Any) -> PriceCatalog:
        if not isinstance(data, dict):
            return cls()
        cat = cls(version=int(data.get("version", CATALOG_VERSION) or CATALOG_VERSION))
        cat.updated_at = str(data.get("updated_at") or "")
        sources = data.get("sources") or {}
        if isinstance(sources, dict):
            for sid, sdata in sources.items():
                key = str(sid or "").strip()
                if key:
                    sdict = sdata if isinstance(sdata, dict) else {}
                    cat.sources[key] = SourceStatus.from_dict(key, sdict)
        prices = data.get("prices") or {}
        if isinstance(prices, dict):
            for key, pdata in prices.items():
                if not isinstance(pdata, dict):
                    continue
                try:
                    price = CatalogPrice.from_dict(pdata)
                except Exception:
                    continue
                if price.source and price.source_model_id:
                    cat.prices[price.price_key] = price
        return cat

    def save(self, data_dir: str) -> None:
        """原子写：先写临时文件再 ``os.replace``，避免半写损坏。"""
        path = _catalog_path(data_dir)
        os.makedirs(data_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        # 落盘后刷新进程级缓存戳，避免同进程内 mtime 粒度不足导致读到旧目录。
        try:
            st = os.stat(path)
            _catalog_cache[str(data_dir)] = (st.st_mtime, st.st_size, self)
        except OSError:
            _catalog_cache.pop(str(data_dir), None)

    # ---- 源状态维护 ----
    def upsert_source(self, status: SourceStatus) -> None:
        self.sources[status.source] = status

    def get_source(self, source: str) -> SourceStatus | None:
        return self.sources.get(source)

    def replace_source_prices(self, source: str, prices: dict[str, CatalogPrice]) -> None:
        """用一次同步结果整体替换某源的价格条目（其它源条目保持不变）。

        无论入参 dict 用什么 key，条目一律按 ``price.price_key`` 归一存放，
        与 :meth:`from_dict` / 进程级缓存实例保持一致口径。
        """
        self.prices = {key: p for key, p in self.prices.items() if not _is_same_source(key, source)}
        for price in prices.values():
            self.prices[price.price_key] = price
        self.updated_at = _now_iso()
        self._candidate_cache.clear()
        self._flat_cache = None

    def flat_prices(self) -> dict[str, dict[str, Any]]:
        """``price_key → price.to_dict()`` 扁平视图（带缓存，prices 变更时失效）。

        计费热路径（``get_pricing``）使用，避免每次调用全量重建 dict。
        """
        if self._flat_cache is None:
            self._flat_cache = {key: p.to_dict() for key, p in self.prices.items()}
        return self._flat_cache

    def prices_for_source(self, source: str) -> dict[str, CatalogPrice]:
        return {key: p for key, p in self.prices.items() if _is_same_source(key, source)}

    # ---- 候选匹配 ----
    def find_candidates(
        self,
        model: str | None,
        *,
        limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE,
        sources: set[str] | None = None,
    ) -> list[Candidate]:
        """为模型名在所有（或指定）启用源中查找候选，按分数降序返回。

        每源最多保留 ``limit_per_source`` 条；New API 源 exact 命中置顶。
        """
        if not model:
            return []
        target_raw = model.strip()
        if not target_raw:
            return []
        target_lower = target_raw.lower()
        target_norm = _normalize_model_name(target_raw)
        if not target_norm:
            return []

        # None = 不过滤（全部源）；空集 = 全部禁用。两者语义不同，不能归并同一缓存键。
        cache_key = (target_raw, frozenset(sources) if sources is not None else None)
        cached = self._candidate_cache.get(cache_key)
        if cached is not None:
            return cached

        per_source: dict[str, list[Candidate]] = {}
        for price in self.prices.values():
            if sources is not None and price.source not in sources:
                continue
            score, reason = _score_match(
                price.source,
                price.source_model_id,
                target_raw,
                target_lower,
                target_norm,
            )
            if score < CANDIDATE_MIN_SCORE:
                continue
            cand = Candidate(
                price_key=price.price_key,
                source=price.source,
                source_model_id=price.source_model_id,
                score=score,
                reason=reason,
                price=price,
            )
            per_source.setdefault(price.source, []).append(cand)

        result: list[Candidate] = []
        for cands in per_source.values():
            cands.sort(key=_candidate_sort_key, reverse=True)
            result.extend(cands[:limit_per_source])
        result.sort(key=_candidate_sort_key, reverse=True)
        if len(self._candidate_cache) >= _CANDIDATE_CACHE_MAX:
            self._candidate_cache.clear()
        self._candidate_cache[cache_key] = result
        return result


def select_auto_candidate(candidates: list[Candidate]) -> Candidate | None:
    """返回唯一高置信候选（score >= AUTO_APPLY_MIN_SCORE），否则 None。"""
    top = [c for c in candidates if c.score >= AUTO_APPLY_MIN_SCORE]
    if len(top) == 1:
        return top[0]
    return None


def load_catalog(data_dir: str) -> PriceCatalog:
    """加载价格目录（按 mtime+size 进程级缓存）；文件不存在返回空目录，损坏则备份。

    返回**共享实例**：调用方对目录的修改（``replace_source_prices`` 等）对同进程
    后续 ``load_catalog`` 可见；跨进程写盘由 (mtime, size) 变化检测。热路径
    （预算检查 / 补充采集 / 聚合）因此不再每次读盘 + 全量 json 解析。
    """
    path = _catalog_path(data_dir)
    try:
        st = os.stat(path)
        stamp = (float(st.st_mtime), int(st.st_size))
    except OSError:
        _catalog_cache.pop(str(data_dir), None)
        return PriceCatalog()
    cached = _catalog_cache.get(str(data_dir))
    if cached is not None and (cached[0], cached[1]) == stamp:
        return cached[2]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        catalog = PriceCatalog.from_dict(data)
    except Exception:
        # 损坏不阻断：备份原文件，回退空目录（计费仍可用 DEFAULT_PRICING）。
        try:
            backup = f"{path}.corrupt.{int(datetime.now(tz=UTC).timestamp())}"
            shutil.copy2(path, backup)
        except Exception:
            pass
        catalog = PriceCatalog()
    _catalog_cache[str(data_dir)] = (stamp[0], stamp[1], catalog)
    return catalog


# ---- 内部工具 ----
def _catalog_path(data_dir: str) -> str:
    return os.path.join(data_dir, CATALOG_FILENAME)


def _is_same_source(price_key: str, source: str) -> bool:
    # price_key = "<source>:<source_model_id>"；source 自身可能含 ':'（newapi:<pid>），
    # 故用 startswith(source + ":") 而非 split。
    return price_key.startswith(source + ":")


def _score_match(
    source: str,
    source_model_id: str,
    target_raw: str,
    target_lower: str,
    target_norm: str,
) -> tuple[float, str]:
    """返回 (分数, 原因)。分数越高越匹配。"""
    sid = (source_model_id or "").strip()
    if not sid:
        return 0.0, ""
    # 1. 原样精确（大小写无关）
    if sid.lower() == target_lower:
        return 1.0, "exact"
    sid_norm = _normalize_model_name(sid)
    if not sid_norm:
        return 0.0, ""
    # 2. models.dev 规范化精确（canonical 目录略高权重）
    if sid_norm == target_norm and source == "modelsdev":
        return 0.95, "canonical"
    # 3. 规范化精确（去前缀/统一分隔符后相等）
    if sid_norm == target_norm:
        return 0.90, "normalized"
    coverage = len(sid_norm) / len(target_norm) if target_norm else 0.0
    coverage = min(coverage, 1.0)
    # 4. 规范化前缀
    if target_norm.startswith(sid_norm):
        return 0.70 + 0.20 * coverage, "prefix"
    # 5. 规范化学串
    if sid_norm in target_norm:
        return 0.55 + 0.30 * coverage, "substring"
    return 0.0, ""


def _candidate_sort_key(cand: Candidate) -> tuple[float, int, float, str]:
    # 分数 → New API 源置顶 → 源模型 ID 长度（越长越具体）→ 字典序。
    return (
        cand.score,
        1 if cand.source.startswith("newapi:") else 0,
        float(len(cand.source_model_id)),
        cand.source_model_id,
    )


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _str_bool_dict(v: Any) -> dict[str, bool]:
    if not isinstance(v, dict):
        return {}
    out: dict[str, bool] = {}
    for k, val in v.items():
        out[str(k)] = bool(val)
    return out


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
