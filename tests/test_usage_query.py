"""``UsageQueryMixin`` 单测：验证 ``aggregate_rows`` 聚合逻辑（纯函数）。"""

from cost_control.usage_query import aggregate_rows


class _Row:
    """模拟 ``ProviderStat`` 行的 duck-typed 对象。"""

    def __init__(self, token_input_other: int, token_input_cached: int, token_output: int) -> None:
        self.token_input_other = token_input_other
        self.token_input_cached = token_input_cached
        self.token_output = token_output


def test_aggregate_empty():
    result = aggregate_rows([])
    assert result == {
        "token_input_other": 0,
        "token_input_cached": 0,
        "token_output": 0,
        "count": 0,
    }


def test_aggregate_single():
    result = aggregate_rows([_Row(100, 50, 200)])
    assert result["token_input_other"] == 100
    assert result["token_input_cached"] == 50
    assert result["token_output"] == 200
    assert result["count"] == 1


def test_aggregate_multiple():
    rows = [_Row(100, 50, 200), _Row(300, 0, 100), _Row(0, 0, 0)]
    result = aggregate_rows(rows)
    assert result["token_input_other"] == 400
    assert result["token_input_cached"] == 50
    assert result["token_output"] == 300
    assert result["count"] == 3


def test_aggregate_tolerates_none_attrs():
    # 缺少属性或为 None 时按 0 处理
    class _Partial:
        token_input_other = 5
        # 缺 token_input_cached / token_output

    result = aggregate_rows([_Partial()])  # type: ignore[list-item]
    assert result["token_input_other"] == 5
    assert result["token_input_cached"] == 0
    assert result["token_output"] == 0
    assert result["count"] == 1
