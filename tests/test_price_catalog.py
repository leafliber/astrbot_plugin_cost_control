"""价格目录单测：候选评分排序、自动匹配、持久化与损坏回退。"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from cost_control.price_catalog import (
    AUTO_APPLY_MIN_SCORE,
    CANDIDATE_MIN_SCORE,
    CatalogPrice,
    PriceCatalog,
    SourceStatus,
    load_catalog,
    select_auto_candidate,
)


def _price(source: str, sid: str, **kw: object) -> CatalogPrice:
    return CatalogPrice(
        source=source,
        source_model_id=sid,
        mode=kw.pop("mode", "per_token"),
        prompt=1.0,
        completion=2.0,
        **kw,
    )


def _catalog_with(*prices: CatalogPrice) -> PriceCatalog:
    cat = PriceCatalog()
    for p in prices:
        cat.prices[p.price_key] = p
    return cat


# ===== 候选评分与排序 =====


def test_exact_match_scores_one():
    cat = _catalog_with(_price("litellm", "gpt-4o"))
    cands = cat.find_candidates("gpt-4o")
    assert len(cands) == 1
    assert cands[0].score == 1.0
    assert cands[0].reason == "exact"


def test_newapi_exact_pinned_first():
    cat = _catalog_with(
        _price("litellm", "gpt-4o"),
        _price("newapi:us", "gpt-4o"),
        _price("modelsdev", "openai/gpt-4o"),
    )
    cands = cat.find_candidates("gpt-4o")
    # 两个 exact（litellm + newapi），New API 置顶
    assert cands[0].source == "newapi:us"
    assert cands[0].reason == "exact"


def test_modelsdev_canonical_above_normalized():
    cat = _catalog_with(
        _price("openrouter", "openai/gpt-4o"),
        _price("modelsdev", "openai/gpt-4o"),
    )
    cands = cat.find_candidates("gpt-4o")
    by_source = {c.source: c for c in cands}
    assert by_source["modelsdev"].reason == "canonical"
    assert by_source["modelsdev"].score == pytest.approx(0.95)
    assert by_source["openrouter"].reason == "normalized"
    assert by_source["openrouter"].score == pytest.approx(0.90)


def test_prefix_beats_substring():
    cat = _catalog_with(_price("modelsdev", "anthropic/claude-sonnet-4"))
    cands = cat.find_candidates("claude-sonnet-4-20250929")
    assert len(cands) == 1
    assert cands[0].reason == "prefix"
    assert cands[0].score >= 0.70


def test_substring_minimum_threshold():
    cat = _catalog_with(_price("litellm", "gpt"))
    cands = cat.find_candidates("some-gpt-4o")
    assert cands  # 命中 substring，分数 ≥0.55
    assert cands[0].reason == "substring"
    assert cands[0].score >= CANDIDATE_MIN_SCORE


def test_no_match_returns_empty():
    cat = _catalog_with(_price("litellm", "gpt-4o"))
    assert cat.find_candidates("totally-unrelated-xyz") == []
    assert cat.find_candidates("") == []
    assert cat.find_candidates(None) == []


def test_limit_per_source_applied():
    cat = PriceCatalog()
    for i in range(20):
        p = _price("litellm", f"gpt-4o-{i}")
        cat.prices[p.price_key] = p
    cands = cat.find_candidates("gpt-4o-5", limit_per_source=3)
    # 同一源最多 3 条
    assert sum(1 for c in cands if c.source == "litellm") <= 3


def test_sources_filter():
    cat = _catalog_with(
        _price("litellm", "gpt-4o"),
        _price("openrouter", "gpt-4o"),
    )
    cands = cat.find_candidates("gpt-4o", sources={"openrouter"})
    assert {c.source for c in cands} == {"openrouter"}


def test_candidate_cache_key_includes_limit_and_none_vs_empty_sources():
    cat = _catalog_with(
        _price("litellm", "gpt-4o"),
        _price("litellm", "gpt-4o-mini"),
        _price("openrouter", "gpt-4o"),
    )

    limited = cat.find_candidates("gpt-4o-mini", limit_per_source=1)
    expanded = cat.find_candidates("gpt-4o-mini", limit_per_source=2)
    assert len([c for c in limited if c.source == "litellm"]) == 1
    assert len([c for c in expanded if c.source == "litellm"]) == 2

    assert cat.find_candidates("gpt-4o", sources=set()) == []
    all_sources = cat.find_candidates("gpt-4o", sources=None)
    assert {c.source for c in all_sources} == {"litellm", "openrouter"}


# ===== 自动匹配判定 =====


def test_select_auto_unique_high_confidence():
    cat = _catalog_with(_price("modelsdev", "openai/gpt-4o"))
    auto = select_auto_candidate(cat.find_candidates("gpt-4o"))
    assert auto is not None
    assert auto.source == "modelsdev"


def test_select_auto_none_when_multiple_high_confidence():
    cat = _catalog_with(
        _price("litellm", "gpt-4o"),
        _price("newapi:us", "gpt-4o"),
    )
    assert select_auto_candidate(cat.find_candidates("gpt-4o")) is None


def test_select_auto_none_below_threshold():
    cat = _catalog_with(_price("litellm", "gpt"))  # substring，分数 <0.85
    cands = cat.find_candidates("some-gpt-x")
    if cands:
        assert cands[0].score < AUTO_APPLY_MIN_SCORE
    assert select_auto_candidate(cands) is None


# ===== 持久化 =====


def test_save_and_load_roundtrip(tmp_path):
    cat = PriceCatalog()
    cat.upsert_source(
        SourceStatus(
            source="modelsdev",
            status="ok",
            models=3,
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    p = _price(
        "modelsdev",
        "openai/gpt-4o",
        mode="per_tier",
        context_tiers=[{"threshold_tokens": 200000, "prompt": 5.0}],
        raw={"x": 1},
    )
    cat.replace_source_prices("modelsdev", {p.price_key: p})

    cat.save(str(tmp_path))
    loaded = load_catalog(str(tmp_path))

    assert loaded.version == cat.version
    assert loaded.sources["modelsdev"].models == 3
    assert "modelsdev:openai/gpt-4o" in loaded.prices
    lp = loaded.prices["modelsdev:openai/gpt-4o"]
    assert lp.mode == "per_tier"
    assert lp.context_tiers[0]["threshold_tokens"] == 200000
    assert lp.raw == {"x": 1}


def test_load_missing_file_returns_empty(tmp_path):
    cat = load_catalog(str(tmp_path))
    assert isinstance(cat, PriceCatalog)
    assert cat.prices == {}


def test_load_corrupt_file_backs_up_and_returns_empty(tmp_path):
    path = tmp_path / "price_catalog.json"
    path.write_text("{not valid json", encoding="utf-8")
    cat = load_catalog(str(tmp_path))
    assert cat.prices == {}
    backups = [n for n in os.listdir(tmp_path) if n.startswith("price_catalog.json.corrupt.")]
    assert len(backups) == 1


def test_replace_source_prices_isolates_sources(tmp_path):
    cat = PriceCatalog()
    a = _price("modelsdev", "m1")
    b = _price("litellm", "m1")
    cat.replace_source_prices("modelsdev", {a.price_key: a})
    cat.replace_source_prices("litellm", {b.price_key: b})
    assert len(cat.prices) == 2
    # 重新同步 modelsdev 只替换该源
    a2 = _price("modelsdev", "m2")
    cat.replace_source_prices("modelsdev", {a2.price_key: a2})
    assert "modelsdev:m1" not in cat.prices
    assert "modelsdev:m2" in cat.prices
    assert "litellm:m1" in cat.prices


def test_save_atomic_no_tmp_left(tmp_path):
    cat = _catalog_with(_price("litellm", "gpt-4o"))
    cat.save(str(tmp_path))
    leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    assert leftovers == []
    # 文件是合法 JSON
    with open(tmp_path / "price_catalog.json", encoding="utf-8") as f:
        json.load(f)


def test_save_replace_failure_cleans_unique_tmp(monkeypatch, tmp_path):
    cat = _catalog_with(_price("litellm", "gpt-4o"))

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        cat.save(str(tmp_path))
    assert [name for name in os.listdir(tmp_path) if name.endswith(".tmp")] == []


def test_concurrent_saves_do_not_share_tmp_file(tmp_path):
    first = _catalog_with(_price("litellm", "first"))
    second = _catalog_with(_price("openrouter", "second"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first.save, str(tmp_path)), pool.submit(second.save, str(tmp_path))]
        for future in futures:
            future.result()

    loaded = load_catalog(str(tmp_path))
    assert set(loaded.prices) in ({"litellm:first"}, {"openrouter:second"})
    assert [name for name in os.listdir(tmp_path) if name.endswith(".tmp")] == []


def test_from_dict_skips_malformed_entries():
    data = {
        "version": 1,
        "prices": {
            "bad": "not-a-dict",
            "modelsdev:ok": {"source": "modelsdev", "source_model_id": "ok", "prompt": 1.0},
            "modelsdev:no-src": {"source_model_id": "x", "prompt": 1.0},
        },
    }
    cat = PriceCatalog.from_dict(data)
    assert list(cat.prices.keys()) == ["modelsdev:ok"]
