"""B8 认知层暴露成命名工具表（对标 undermind U12：整个产品对外是「一组有名字的工具」）。

钉四件事（§4 B8 的验收 ①②+ 两条自家教训）：
① 工具清单与路由表**双向**对账——多一个（点了没有的端点）或少一个（新增端点没起名）都必须红；
② 路径与方法只能从 router 现取，不许在这份表里抄第二遍（抄本必漂）；
③ 对账不许是空对空：认知层没挂上时路由是 0 条，"全都对得上"会假绿，所以先量住下限；
④ 研伴是要被梧桐平台**调用**的智能体 ⇒ 表里不许出现账户/额度/消费这类概念。
"""
from __future__ import annotations

import inspect
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from companion.api import router as router_module
from companion.api import tools

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_PY = ROOT / "companion" / "api" / "tools.py"
TOOLS_MD = ROOT / "docs" / "TOOLS.md"

# 目标里点名的六个词必须在表里（缺一个就是"起了一份稳定词表"没做到）
OBJECTIVE_VOCAB = ["search_papers", "read_pdf_span", "write_report", "star_papers",
                   "inspect_project", "list_commitments"]

BANNED = ["账户", "额度", "消费", "充值", "付费", "计费", "价格", "订单",
          "billing", "quota", "credit", "payment", "pricing", "subscription fee"]


@pytest.fixture()
def routes():
    return [r for r in router_module.router.routes if hasattr(r, "methods")]


@pytest.fixture()
def table(routes):
    return tools.build(routes)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def test_table_covers_every_route_and_nothing_extra(routes, table):
    """双向：每条路由恰好被点名一次，每个工具名恰好对应一条路由。"""
    assert len(table) == len(routes)
    paths = sorted(r.path for r in routes)
    assert sorted(t["path"] for t in table) == paths


def test_reconciliation_is_not_vacuous(routes, table):
    """先证明对面不是空的：leader 那边的 companion 导入是 try/except，挂不上时路由为 0，
    而"0 条全对得上"照样会绿——这条把下限钉住。"""
    assert len(routes) >= 50, f"路由只有 {len(routes)} 条，对账没有意义"
    assert len(table) >= 50


def test_tool_names_unique_and_snake_case(table):
    names = [t["name"] for t in table]
    assert len(names) == len(set(names))
    bad = [n for n in names if not re.fullmatch(r"[a-z][a-z0-9_]*", n)]
    assert not bad, f"工具名不合词表形状：{bad}"


def test_paths_come_from_the_router_not_a_copy(table):
    """tools.py 里出现任何一条真实路径字符串，就说明第二份真相开始成形。"""
    src = TOOLS_PY.read_text(encoding="utf-8")
    copied = [t["path"] for t in table if t["path"] in src]
    assert not copied, f"这些路径被抄进了工具表：{copied}"


def test_named_endpoints_belong_to_the_companion_router(routes):
    """点名只认函数名：万一别处也有同名函数，对账会对着错的东西点头。"""
    for r in routes:
        assert r.endpoint.__module__ == "companion.api.router", r.path


def test_internal_tools_are_exactly_the_half_auth_profile_routes(table):
    """/users/{uid}/profile 的鉴权是半截的（整套 API 还没有身份层）——先点名但不对外 advertised。
    这条判据的作用：谁把别的端点也塞进 INTERNAL，或把这两个偷偷放出去，都会红。"""
    internal = {t["name"] for t in table if t["internal"]}
    assert internal == {"inspect_user_profile", "forget_user_profile"}
    for t in table:
        if t["internal"]:
            assert "/users/" in t["path"], t
    assert not any(t["internal"] and t["name"] in OBJECTIVE_VOCAB for t in table)


def test_no_account_or_billing_wording_anywhere(table):
    hits = [(t["name"], w) for t in table for w in BANNED
            if w in t["name"].lower() or w in t["summary"].lower()]
    assert not hits, f"工具表里出现了账户/消费类概念：{hits}"


def test_objective_vocabulary_present(table):
    names = {t["name"] for t in table}
    missing = [n for n in OBJECTIVE_VOCAB if n not in names]
    assert not missing, f"目标点名的工具名没进表：{missing}"


def test_tools_endpoint_returns_the_live_table(client, table):
    body = client.get("/companion/tools").json()
    assert body["tools"] == table, "端点给的不是现算的那份——它抄了自己的副本"


def test_docs_tools_md_points_at_source_without_copying_params():
    """"不抄参数细节，只指源码"：文档里出现类型标注/Body( 就是在抄第二份参数表。"""
    md = TOOLS_MD.read_text(encoding="utf-8")
    assert "companion/api/tools.py" in md
    assert "/companion/tools" in md
    for marker in [": str", "Body(", "-> dict"]:
        assert marker not in md, f"TOOLS.md 抄了参数细节：{marker}"
    assert not any(w in md for w in BANNED)


# ── 文档里点名的 URL 必须在跑起来的服务上真的存在（轮 198，完成审计抓到的一条自相矛盾）────
# 为什么不用 `app.routes` 数：本机实测 fastapi 0.141.1 / starlette 1.6.0 把 include 进来的子路由
# 收进一个 `_IncludedRouter`（顶层 18 条里 `/companion` 计数为 0），而 TestClient 打 `/companion/tools`
# 是 200 ⇒ 拿"顶层枚举"当全集会把正在服务的端点判成不存在。HTTP 行为不骗人。
# 实测种群：TOOLS.md 里路由形状的串 3 条（/companion/tools、/companion/users/{uid}/profile、/rpc）。
DOC_URL_FLOOR = 3
DOC_URL_RE = re.compile(r"(?<![\w/])(/(?:companion|rpc)(?:/[A-Za-z0-9_{}.\-]+)*)(?![/*\w])")
PLACEHOLDER = re.compile(r"\{[^}]*\}")


def doc_route_paths(md: str) -> list[str]:
    """只认单条路由形状的串；`/companion/*` 那种散文里的通配由结尾的 lookahead 挡掉（植入验过）。"""
    return sorted(set(DOC_URL_RE.findall(md)))


def served(client, doc_path: str) -> tuple[bool, int]:
    probe = PLACEHOLDER.sub("r198probe", doc_path)
    status = client.get(probe).status_code
    return status != 404, status


@pytest.fixture(scope="module")
def live_app_client():
    import leader
    return TestClient(leader.app)


def test_every_url_named_in_tools_md_is_served_by_the_live_app(live_app_client):
    """文档写出的每条 URL 都得真的在服务；`/rpc` 只收 POST（实测 GET 它是 404 不是 405），
    所以那一条按"表里有 POST /rpc"判，不靠 HTTP 猜。"""
    paths = doc_route_paths(TOOLS_MD.read_text(encoding="utf-8"))
    assert len(paths) >= DOC_URL_FLOOR, (
        f"只从 TOOLS.md 提取到 {len(paths)} 条 URL（地板 {DOC_URL_FLOOR}）⇒ 提取器或文档格式漂了，先修仪器")
    top_paths = {r.path: sorted(r.methods) for r in live_app_client.app.routes if hasattr(r, "methods")}
    bad = []
    for p in paths:
        if p == "/rpc":
            if "POST" not in top_paths.get("/rpc", []):
                bad.append("/rpc（顶层路由表里没有 POST 的那条入口）")
            continue
        ok, status = served(live_app_client, p)
        if not ok:
            bad.append(f"{p} ⇒ GET {status}")
    assert not bad, "TOOLS.md 点了服务里不存在的路径（文档比端点活得久就是假工具）：" + "；".join(bad)


def test_the_url_gate_bites_on_a_moved_endpoint_and_stays_quiet_on_a_live_one(live_app_client):
    """负向 + 正向对照放同一条：只喂合成串，不碰真文档。
    没有它，`served` 可能恒为真，上一条跟着假绿。"""
    live = "/companion/tools"
    assert served(live_app_client, live)[0], f"{live} 明明在服务却判成不存在 ⇒ 判据用错了，不是文档的错"
    moved = "/companion/toolz"
    ok, status = served(live_app_client, moved)
    assert not ok and status == 404, f"改个名都没抓到（{moved} ⇒ {status}）⇒ 这条闸门是装饰"
    assert doc_route_paths("从哪儿进：`GET /companion/tools`、`POST /rpc`") == ["/companion/tools", "/rpc"], "提取形状漂了"
    assert doc_route_paths("整条 `/companion/*` 前缀都是裸路由") == [], "散文里的通配被当成单条路由提进来了"

