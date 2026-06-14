"""``SupplementMixin`` 单测：验证 ``_extract_cache`` 对各 provider 的解析（纯函数）。"""

from types import SimpleNamespace

from cost_control.supplement import _extract_cache


def test_extract_cache_none_raw():
    cc, cr, raw = _extract_cache(None)
    assert cc is None
    assert cr is None
    assert raw is None


def test_extract_cache_anthropic():
    usage = SimpleNamespace(
        cache_creation_input_tokens=100,
        cache_read_input_tokens=200,
        input_tokens=1000,
        output_tokens=500,
    )
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cc == 100
    assert cr == 200


def test_extract_cache_openai_prompt_tokens_details():
    ptd = SimpleNamespace(cached_tokens=150)
    usage = SimpleNamespace(prompt_tokens_details=ptd, prompt_tokens=1000)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cc is None
    assert cr == 150


def test_extract_cache_deepseek_extension_fields():
    usage = SimpleNamespace(prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage=usage))
    assert cr == 80
    assert cc == 20


def test_extract_cache_google_usage_metadata():
    um = SimpleNamespace(cached_content_token_count=300)
    cc, cr, _ = _extract_cache(SimpleNamespace(usage_metadata=um))
    assert cc is None
    assert cr == 300


def test_extract_cache_no_usage():
    cc, cr, raw = _extract_cache(SimpleNamespace())
    assert cc is None
    assert cr is None
    assert raw is None
