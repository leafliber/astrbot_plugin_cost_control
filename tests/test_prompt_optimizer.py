"""``PromptOptimizerMixin`` 静态分析纯函数单测。"""

from cost_control.prompt_optimizer import _analyze


def test_analyze_basic():
    r = _analyze("你是一个乐于助人的助手。")
    assert r["tokens_est"] > 0
    assert r["length"] > 0


def test_analyze_redundancy_detected():
    text = "你是助手。你是助手。请回答问题。请回答问题。"
    r = _analyze(text)
    assert r["redundancy_score"] > 0
    assert len(r["repeated_blocks"]) > 0


def test_analyze_dynamic_lowers_cacheability():
    text = "当前日期是 2026-01-01，请基于此回答。"
    r = _analyze(text)
    assert r["cacheability_score"] < 90


def test_analyze_clean_prompt_high_cacheability():
    r = _analyze("你是一个乐于助人的助手。")
    assert r["cacheability_score"] >= 70
    assert any("未发现明显问题" in s for s in r["suggestions"])


def test_analyze_long_prompt_suggestion():
    # 600 句 * 6 字符 = 3600 字符 ≈ 2160 token（CJK 0.6/char），超过 2000 阈值
    r = _analyze("请回答问题。" * 600)
    assert any("偏长" in s for s in r["suggestions"])
