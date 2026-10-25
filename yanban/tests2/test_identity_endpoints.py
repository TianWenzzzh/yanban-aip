"""`/identity` 的两列端点必须是两份不同的值（宿主机 vs 容器网络）。

为什么单开一个文件：设置页那张表画了「宿主机端点」与「容器网络端点」两列，而
`orchestrator.load_partners` 在 `YANBAN_NET=compose` 下**就地改写** `endpoint`，
`leader.identity()` 又把改写后的值同时填进两列 ⇒ 六行两列一模一样。
测试全绿、tsc 全绿、vite 全绿，只有把页面截下来看才发现（轮 141）。
"""
from __future__ import annotations

import asyncio
import os
import textwrap
import types

import leader
import orchestrator

TOML = textwrap.dedent("""
    leader = { aic = "yanban-leader" }
    [[partner]]
    name = "literature"
    endpoint = "http://localhost:8031/rpc"
    compose_endpoint = "http://literature:8031/rpc"
    [[partner]]
    name = "legacy"
    endpoint = "http://localhost:9099/rpc"
""")


def _load(tmp_path, monkeypatch, net: str):
    f = tmp_path / "partners.toml"
    f.write_text(TOML, encoding="utf-8")
    monkeypatch.setenv("YANBAN_NET", net)
    partners, _aic = orchestrator.load_partners(f)
    return {p["name"]: p for p in partners}


def _identity_payload(partners: dict):
    leader.orch = types.SimpleNamespace(partners=list(partners.values()), leader_aic="yanban-leader")
    return asyncio.run(leader.identity())["partners"]


def test_compose_mode_routes_through_compose_but_reports_the_host(tmp_path, monkeypatch):
    p = _load(tmp_path, monkeypatch, "compose")
    assert p["literature"]["endpoint"] == "http://literature:8031/rpc", "路由必须走容器网络"
    rows = {r["name"]: r for r in _identity_payload(p)}
    assert rows["literature"]["endpoint"] == "http://localhost:8031/rpc", "报告面要的是宿主机那份"
    assert rows["literature"]["compose_endpoint"] == "http://literature:8031/rpc"


def test_the_two_columns_actually_differ_on_the_page(tmp_path, monkeypatch):
    """这一条才是"看图抓到的那个缺陷"的闸门：两列不许一模一样。"""
    rows = {r["name"]: r for r in _identity_payload(_load(tmp_path, monkeypatch, "compose"))}
    lit = rows["literature"]
    assert lit["endpoint"] != lit["compose_endpoint"], \
        f"两列相同（都是 {lit['endpoint']}）⇒ 设置页那两列有一列是假的"


def test_partner_without_compose_endpoint_still_shows_a_real_host(tmp_path, monkeypatch):
    rows = {r["name"]: r for r in _identity_payload(_load(tmp_path, monkeypatch, "compose"))}
    assert rows["legacy"]["endpoint"] == "http://localhost:9099/rpc"
    assert rows["legacy"]["compose_endpoint"] is None, "没有 compose 就明说没有，不许拿宿主机冒充"


def test_the_old_expression_would_have_been_caught(tmp_path, monkeypatch):
    """负对照：把 leader 那份表达式退回旧写法（直接读被改写后的 endpoint），两列必然相等。

    没有这一条，上面的"两列不同"可能只是恰好两份输入相同而没人能证它会红。
    """
    p = _load(tmp_path, monkeypatch, "compose")
    old = {r["name"]: r for r in [{"name": x["name"], "endpoint": x["endpoint"],
                                   "compose_endpoint": x.get("compose_endpoint")} for x in p.values()]}
    assert old["literature"]["endpoint"] == old["literature"]["compose_endpoint"], \
        "旧写法居然两列不同 ⇒ 这台机器不是 compose 模式，这条负对照失去意义，改成显式断言前提"


def test_host_endpoint_only_appears_in_compose_mode(tmp_path, monkeypatch):
    """非 compose 模式不改写、也不该凭空造出 host_endpoint（否则两模式语义分叉）。"""
    p = _load(tmp_path, monkeypatch, "host")
    assert "host_endpoint" not in p["literature"]
    assert p["literature"]["endpoint"] == "http://localhost:8031/rpc"
    assert os.environ.get("YANBAN_NET") == "host"
