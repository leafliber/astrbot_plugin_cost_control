"""``WebApiMixin._load_catalog`` 缓存回归测试。

目录可达数 MB：每个请求全量同步解析既阻塞事件循环又浪费 CPU。修复后按文件
stat（mtime_ns/size/inode）缓存已解析实例，``PriceCatalog.save`` 的
``os.replace`` 落盘必然改变 inode → 缓存自动失效。
"""

import asyncio

from cost_control.price_catalog import CatalogPrice, PriceCatalog
from cost_control.web_api import WebApiMixin


class _CatalogHost(WebApiMixin):
    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir

    def get_data_dir(self) -> str:  # noqa: D102 - 继承签名
        return self._data_dir


def _save_catalog(data_dir: str, model_id: str) -> None:
    cat = PriceCatalog()
    cat.prices[f"modelsdev:{model_id}"] = CatalogPrice(
        source="modelsdev", source_model_id=model_id, prompt=1.0
    )
    cat.save(data_dir)


def test_load_catalog_cached_until_file_changes(tmp_path):
    host = _CatalogHost(str(tmp_path))
    _save_catalog(str(tmp_path), "m-1")

    cat1 = asyncio.run(host._load_catalog())
    cat2 = asyncio.run(host._load_catalog())
    assert cat1 is cat2  # 命中缓存：同一实例，不再全量重解析
    assert "modelsdev:m-1" in cat1.prices

    # 落盘新目录（os.replace → 新 inode）→ 缓存失效，重新加载
    _save_catalog(str(tmp_path), "m-2")
    cat3 = asyncio.run(host._load_catalog())
    assert cat3 is not cat1
    assert "modelsdev:m-2" in cat3.prices
    assert "modelsdev:m-1" not in cat3.prices


def test_load_catalog_missing_file_returns_empty_and_cached(tmp_path):
    host = _CatalogHost(str(tmp_path))
    cat1 = asyncio.run(host._load_catalog())
    assert cat1.prices == {}
    cat2 = asyncio.run(host._load_catalog())
    assert cat1 is cat2
