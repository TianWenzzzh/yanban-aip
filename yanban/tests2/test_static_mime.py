"""静态托管的 MIME 口径（轮 167）。

真因：Windows 的 `mimetypes` 走注册表，`.js` 常被判成 `text/plain`；`StaticFiles` 照它发响应头，
而浏览器对 `<script type="module">` 强制 MIME 检查 ⇒ **本机起服务整页白屏**，
云端 Linux 上同一份代码却是好的——所以这个缺陷从没在部署里暴露过，只在"我想截图看看"时撞上。
判据不许靠"我看了一眼页面"：直接量响应头。
"""
import os

os.environ["LLM_MOCK"] = "1"

import pathlib

import pytest

import companion.db as db_module

DIST = pathlib.Path(__file__).resolve().parents[1] / "webui" / "dist"

pytestmark = pytest.mark.skipif(not (DIST / "assets").is_dir(),
                                reason="没有 webui/dist（先 npm run build 才谈静态口径）")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "mime.db")
    db_module._CONN = None
    from fastapi.testclient import TestClient
    from leader import app
    return TestClient(app)


def _first_asset(suffix: str) -> str:
    hits = sorted(p.name for p in (DIST / "assets").glob(f"*{suffix}"))
    assert hits, f"dist/assets 里没有 {suffix} 文件"
    return hits[0]


def test_js_assets_are_not_served_as_text_plain(client):
    r = client.get(f"/assets/{_first_asset('.js')}")
    assert r.status_code == 200
    ctype = r.headers.get("content-type", "")
    assert "javascript" in ctype, f"模块脚本被发成 {ctype!r} ⇒ 浏览器会拒绝执行（白屏）"
    assert "text/plain" not in ctype


def test_css_asset_keeps_a_standalone_type(client):
    ctype = client.get(f"/assets/{_first_asset('.css')}").headers.get("content-type", "")
    assert "text/css" in ctype


def test_index_html_still_serves_the_app(client):
    r = client.get("/?page=companion")
    assert r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
    assert 'type="module"' in r.text          # 白屏的那条路径确实依赖 module script
