"""``CacheDiagMixin`` 纯函数单测：命中率与四类破坏诊断。"""

from cost_control.cache_diag import _format_tools, _line_diff, diagnose_changes, hit_rate


def test_hit_rate_normal():
    assert hit_rate(50, 50, 0) == 50.0


def test_hit_rate_zero():
    assert hit_rate(0, 100, 0) == 0.0


def test_hit_rate_no_data():
    assert hit_rate(None, None, None) == -1.0
    assert hit_rate(0, 0, 0) == -1.0


def test_hit_rate_with_creation():
    # 50 read / (50 + 30 + 20) = 50%
    assert hit_rate(50, 30, 20) == 50.0


def test_line_diff_modify():
    # 第二行 b → c：保留上下文 a，删 b，增 c
    d = _line_diff("a\nb", "a\nc")
    assert d == [
        {"op": " ", "text": "a"},
        {"op": "-", "text": "b"},
        {"op": "+", "text": "c"},
    ]


def test_line_diff_add_and_remove():
    assert _line_diff("a", "a\nb") == [
        {"op": " ", "text": "a"},
        {"op": "+", "text": "b"},
    ]
    assert _line_diff("a\nb", "a") == [
        {"op": " ", "text": "a"},
        {"op": "-", "text": "b"},
    ]


def test_line_diff_empty_or_unchanged():
    # 任一为空 → None
    assert _line_diff("", "x") is None
    assert _line_diff("x", "") is None
    # 无增删 → None
    assert _line_diff("a\nb", "a\nb") is None


def test_line_diff_truncation():
    # old 1 行、new 201 行 → 200 行 + 1 上下文 = 201 条 diff，截断到 max_lines
    new_lines = "\n".join("line" + str(i) for i in range(201))
    d = _line_diff("base", new_lines, max_lines=10)
    assert d is not None
    assert len(d) == 11  # 10 行 + 1 截断提示
    assert "已截断" in d[-1]["text"]


def test_line_diff_long_line_truncation():
    # 单行超过 max_line_len → 截断并追加 …
    long_text = "x" * 300
    d = _line_diff("short", long_text, max_line_len=50)
    assert d is not None
    plus = [dl for dl in d if dl["op"] == "+"]
    assert len(plus) == 1
    assert plus[0]["text"].endswith("…")
    assert len(plus[0]["text"]) == 51  # 50 + …


# ===== _format_tools =====


class _MockTool:
    """模拟 AstrBot 工具对象（FuncTool 等）。"""

    def __init__(self, name, description, parameters):
        self.name = name
        self.description = description
        self.parameters = parameters


def test_format_tools_basic():
    tools = [
        _MockTool(
            "search",
            "搜索内容",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词"},
                    "limit": {"type": "number", "description": "数量"},
                },
                "required": ["query"],
            },
        ),
    ]
    text = _format_tools(tools)
    lines = text.split("\n")
    assert lines[0] == "[search] 搜索内容"
    assert "  query*(string): 关键词" in lines
    assert "  limit(number): 数量" in lines


def test_format_tools_empty():
    assert _format_tools(None) == ""
    assert _format_tools([]) == ""


def test_format_tools_dict_input():
    tools = [
        {
            "name": "t1",
            "description": "desc1",
            "parameters": {
                "type": "object",
                "properties": {"p": {"type": "string", "description": "pd"}},
            },
        }
    ]
    text = _format_tools(tools)
    assert "[t1] desc1" in text
    assert "p(string): pd" in text


def test_format_tools_long_line_truncated():
    long_desc = "x" * 300
    tools = [_MockTool("t", long_desc, {})]
    text = _format_tools(tools)
    line = text.split("\n")[0]
    assert line.endswith("…")
    assert len(line) == 201  # 200 chars + …


def test_format_tools_precise_diff():
    """两个版本的工具列表，仅一个参数描述变化，diff 应精确到该行。"""
    old_tools = [
        _MockTool(
            "get_setu",
            "获取图片",
            {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "标签关键词"},
                    "num": {"type": "number", "description": "获取数量"},
                },
            },
        ),
    ]
    new_tools = [
        _MockTool(
            "get_setu",
            "获取图片",
            {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "标签关键词。可选。"},
                    "num": {"type": "number", "description": "获取数量"},
                },
            },
        ),
    ]
    old_text = _format_tools(old_tools)
    new_text = _format_tools(new_tools)
    d = _line_diff(old_text, new_text)
    assert d is not None
    # diff 应包含 tag 的旧行(-)和新行(+)，但不包含 num 的行（未变）
    removed = [dl for dl in d if dl["op"] == "-"]
    added = [dl for dl in d if dl["op"] == "+"]
    assert len(removed) == 1
    assert len(added) == 1
    assert "标签关键词" in removed[0]["text"]
    assert "可选" in added[0]["text"]


def test_diagnose_system_change_with_diff():
    last = {
        "history_len": 2,
        "system_hash": "a",
        "tools_hash": "x",
        "system_text": "你是助手\n规则一",
        "tools_text": "",
        "contexts_hashes": ["a", "b"],
    }
    cur = {
        "history_len": 2,
        "system_hash": "b",
        "tools_hash": "x",
        "system_text": "你是助手\n规则二",
        "tools_text": "",
        "contexts_hashes": ["a", "b"],
    }
    ev = next(e for e in diagnose_changes(cur, last, {}) if e["type"] == "system_prompt_change")
    assert "system_diff" in ev["after"]
    ops = [d["op"] for d in ev["after"]["system_diff"]]
    assert "+" in ops and "-" in ops


def test_diagnose_tools_change_with_diff():
    last = {
        "history_len": 1,
        "system_hash": "a",
        "tools_hash": "x",
        "system_text": "",
        "tools_text": "toolA",
        "contexts_hashes": ["a"],
    }
    cur = {
        "history_len": 1,
        "system_hash": "a",
        "tools_hash": "y",
        "system_text": "",
        "tools_text": "toolA\ntoolB",
        "contexts_hashes": ["a"],
    }
    ev = next(e for e in diagnose_changes(cur, last, {}) if e["type"] == "tools_change")
    assert "tools_diff" in ev["after"]


def _sig(history_len, system_hash="a", tools_hash="x", hashes=None):
    return {
        "history_len": history_len,
        "system_hash": system_hash,
        "tools_hash": tools_hash,
        "contexts_hashes": hashes if hashes is not None else ["a"] * history_len,
    }


def test_diagnose_context_reset():
    last = _sig(10)
    cur = _sig(3)
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "context_reset" in types


def test_diagnose_system_prompt_change():
    last = _sig(5, system_hash="a")
    cur = _sig(5, system_hash="b")
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "system_prompt_change" in types


def test_diagnose_tools_change():
    last = _sig(5, tools_hash="x")
    cur = _sig(5, tools_hash="y")
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "tools_change" in types


def test_diagnose_order_drift():
    last = _sig(3, hashes=["a", "b", "c"])
    cur = _sig(4, hashes=["a", "b", "d", "e"])
    types = [e["type"] for e in diagnose_changes(cur, last, {})]
    assert "order_drift" in types


def test_diagnose_no_change():
    last = _sig(5)
    cur = _sig(6)  # 正常追加一条
    assert diagnose_changes(cur, last, {}) == []


def test_diagnose_flags_disable():
    last = _sig(10)
    cur = _sig(3)
    flags = {"detect_context_reset": False}
    types = [e["type"] for e in diagnose_changes(cur, last, flags)]
    assert "context_reset" not in types


def test_diagnose_includes_before_after():
    last = _sig(8, system_hash="a", tools_hash="x")
    cur = _sig(3, system_hash="a", tools_hash="x")  # 触发 context_reset
    evs = diagnose_changes(cur, last, {})
    reset = next(e for e in evs if e["type"] == "context_reset")
    # 裁剪后的前后签名落库，便于前端结构化对比
    assert reset["before"] == {
        "history_len": 8,
        "system_hash": "a",
        "tools_hash": "x",
        "contexts_count": 8,
    }
    assert reset["after"]["history_len"] == 3
    assert reset["after"]["contexts_count"] == 3


def test_diagnose_order_drift_has_diverge():
    last = _sig(3, hashes=["a", "b", "c"])
    cur = _sig(4, hashes=["a", "b", "d", "e"])  # 下标 2 开始分歧
    evs = diagnose_changes(cur, last, {})
    drift = next(e for e in evs if e["type"] == "order_drift")
    assert drift["after"]["first_diverge_at"] == 2


# ===== CacheEvent 落库（集成，内存 sqlite，不依赖 StarTools/真实 astrbot） =====


async def test_cache_event_save_query():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.store import CacheEvent, StoreMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[CacheEvent.__table__],
                checkfirst=True,  # type: ignore[attr-defined]
            )
        )
    store = StoreMixin()
    store._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await store.save_cache_event(
        {
            "umo": "s1",
            "type": "context_reset",
            "severity": "high",
            "detail": "重置",
            "before": {
                "history_len": 8,
                "system_hash": "a",
                "tools_hash": "x",
                "contexts_count": 8,
            },
            "after": {
                "history_len": 3,
                "system_hash": "a",
                "tools_hash": "x",
                "contexts_count": 3,
            },
        }
    )
    await store.save_cache_event(
        {"umo": "s1", "type": "tools_change", "severity": "medium", "detail": "工具变"}
    )
    await store.save_cache_event(
        {"umo": "s2", "type": "order_drift", "severity": "medium", "detail": "顺序漂移"}
    )

    by_s1 = await store.query_cache_events(umo="s1", limit=10)
    assert len(by_s1) == 2
    assert all(getattr(r, "umo") == "s1" for r in by_s1)
    # before/after 结构化字段正确落库并可查回
    reset_row = next(r for r in by_s1 if getattr(r, "type") == "context_reset")
    assert reset_row.before["history_len"] == 8
    assert reset_row.after["history_len"] == 3

    all_rows = await store.query_cache_events(limit=10)
    assert len(all_rows) == 3
    # 最新优先
    assert getattr(all_rows[0], "created_at") >= getattr(all_rows[-1], "created_at")


async def test_cache_event_query_empty():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from cost_control.store import CacheEvent, StoreMixin

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(
                c,
                tables=[CacheEvent.__table__],
                checkfirst=True,  # type: ignore[attr-defined]
            )
        )
    store = StoreMixin()
    store._session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    assert await store.query_cache_events(limit=10) == []
