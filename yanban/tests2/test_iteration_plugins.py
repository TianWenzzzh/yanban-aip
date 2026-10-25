"""迭代轮新增功能测试：工作流路由 / 关键词检索 / 翻译 / 思维导图 / 自生长知识库。"""
from __future__ import annotations

import os
import pathlib
import re
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
import companion.plugins as plugins
from companion.memory import paper_memory
from companion.workflow_router import classify, route_plan


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


# ── 自适应工作流路由 ──────────────────────────────────────────────────
def test_classify_types():
    assert classify("帮我设计一个对比实验方案") == "experiment"
    assert classify("帮我写一段相关工作") == "writing"
    assert classify("梳理一下这个领域的研究现状") == "survey"
    assert classify("帮我检索2篇钓鱼检测的论文") == "search"
    assert classify("什么是零样本学习") == "quick_answer"


def test_route_plan_interview_when_low_confidence(client):
    pid = client.post("/companion/project", json={"name": "新研究"}).json()["project_id"]
    plan = client.post("/companion/route", json={
        "message": "帮我找点论文", "project_id": pid}).json()
    assert plan["workflow"] == "interview"  # 新项目 intent 为空 → 先反问


# ── 关键词检索 ────────────────────────────────────────────────────────
def test_keyword_search_plugin(client, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    pid = client.post("/companion/project", json={"name": "钓鱼检测"}).json()["project_id"]
    client.post("/companion/paper/feedback", json={
        "project_id": pid, "paper": {"id": "k1", "title": "Visual Phishing Detection", "year": 2025},
        "status": "favorite", "reason": ""})
    knowledge.upsert_note(pid, "研究计划", "先看综述，再跑 CLIP 基线")
    r = client.get("/companion/search/keywords", params={"project_id": pid, "q": "phishing"}).json()
    assert any("Visual Phishing" in p["title"] for p in r["papers"])
    # 收藏论文会自生长出可检索的论文笔记（本次改造把 feedback → knowledge 接上了）
    assert any("Visual Phishing" in n["note"] for n in r["notes"])
    assert all("phishing" in (n["note"] + n["context"]).lower() for n in r["notes"])


# ── 翻译 ──────────────────────────────────────────────────────────────
def test_translate_mock(client):
    r = client.post("/companion/translate", json={"text": "Phishing websites imitate legitimate sites.", "target": "zh"}).json()
    assert r["translation"].startswith("[MOCK-翻译")


# ── 思维导图 ──────────────────────────────────────────────────────────
def test_mindmap_generation(client, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    pid = client.post("/companion/project", json={"name": "钓鱼检测"}).json()["project_id"]
    client.post("/companion/chat", json={
        "user_text": "我想研究钓鱼网站检测，做文献综述，深度学习学过一点", "project_id": pid})
    m = client.get(f"/companion/map/mindmap/{pid}").json()
    assert m["format"] == "mermaid-mindmap"
    assert m["mermaid"].startswith("mindmap")
    assert "钓鱼网站检测" in m["mermaid"]


def test_flowchart(client):
    m = client.get("/companion/map/flowchart").json()
    assert "flowchart" in m["mermaid"]


# ── 自生长知识库 ──────────────────────────────────────────────────────
def test_kb_grow_from_paper_and_backlinks(client, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    pid = client.post("/companion/project", json={"name": "钓鱼检测"}).json()["project_id"]
    paper = {"title": "Visual Phishing Detection with CLIP", "year": 2025, "doi": "10.1/x",
             "abstract": "We use CLIP for phishing detection.", "recommendation_reason": ["有代码"]}
    g = client.post(f"/companion/knowledge/{pid}/grow", json={"paper": paper}).json()["grown"]
    assert g.startswith("论文：")
    # 研究笔记引用论文（双链）
    client.post(f"/companion/knowledge/{pid}/note", json={
        "title": "研究计划", "body": "先精读 [[论文：Visual Phishing Detection with CLIP]] 再复现"})
    bl = client.get(f"/companion/knowledge/{pid}/note", params={"title": g}).json()
    assert bl["backlinks"] == ["研究计划"]
    # 全库清单
    notes = client.get(f"/companion/knowledge/{pid}").json()["notes"]
    assert any(n["note"].startswith("论文：") for n in notes)


def test_kb_search(client, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    pid = client.post("/companion/project", json={"name": "x"}).json()["project_id"]
    client.post(f"/companion/knowledge/{pid}/note", json={"title": "实验记录", "body": "CLIP 基线复现成功"})
    hits = client.get(f"/companion/knowledge/{pid}/search", params={"q": "CLIP"}).json()["hits"]
    assert hits and "CLIP" in hits[0]["context"]


def test_mindmap_orders_by_citations_and_labels_them(client):
    pid = client.post("/companion/project", json={"name": "导图权重"}).json()["project_id"]
    paper_memory.save_many(pid, [
        {"id": "a", "title": "Low Cited Paper", "citation_count": 3},
        {"id": "b", "title": "High Cited Paper", "citation_count": 412},
    ])
    m = client.get(f"/companion/map/mindmap/{pid}").json()
    text = m["mermaid"]
    assert text.index("High Cited Paper") < text.index("Low Cited Paper")   # 重的排前面
    assert "（被引412）" in text and "（被引3）" in text and "推荐·2篇" in text


def test_mindmap_filters_by_citations_and_status(client):
    pid = client.post("/companion/project", json={"name": "导图过滤"}).json()["project_id"]
    paper_memory.save_many(pid, [{"id": "a", "title": "Low Cited Paper", "citation_count": 3},
                                 {"id": "b", "title": "High Cited Paper", "citation_count": 412}])
    client.post("/companion/paper/feedback", json={
        "project_id": pid, "paper": {"id": "c", "title": "My Favorite", "citation_count": 5},
        "status": "favorite"})

    r = client.get(f"/companion/map/mindmap/{pid}", params={"min_citations": 10}).json()
    assert (r["shown"], r["total"], r["dropped"], r["top_cited"]) == (1, 3, 2, 412)
    assert "My Favorite" not in r["mermaid"]
    r2 = client.get(f"/companion/map/mindmap/{pid}", params={"only_status": "favorite"}).json()
    assert r2["shown"] == 1 and "My Favorite" in r2["mermaid"]
    assert client.get("/companion/map/mindmap/ghost").status_code == 404


def test_mindmap_never_passes_html_through_from_paper_titles(client):
    """标题是 Crossref 给的外部输入，进了 mermaid 标签又用 dangerouslySetInnerHTML 渲染。"""
    pid = client.post("/companion/project", json={"name": "导图消毒"}).json()["project_id"]
    paper_memory.save_many(pid, [{"id": "x", "title": "<img src=x onerror=alert(1)>真实标题",
                                  "citation_count": 7}])
    text = client.get(f"/companion/map/mindmap/{pid}").json()["mermaid"]
    assert "<" not in text and ">" not in text      # 尖括号进不去就拼不出任何标签
    assert "真实标题" in text


def test_citations_of_tolerates_junk():
    assert plugins.citations_of({"citation_count": None}) == 0
    assert plugins.citations_of({"citation_count": "12"}) == 12
    assert plugins.citations_of({"citation_count": "n/a"}) == 0
    assert plugins.filter_papers([{"title": "x"}], min_citations=5) == []


def test_citation_export_route_is_not_shadowed(client):
    """GET /companion/paper/{pid}/export 曾被 /paper/{pid}/{filename} 抢先匹配成 404「PDF 未缓存」。"""
    pid = client.post("/companion/project", json={"name": "导出路由"}).json()["project_id"]
    r = client.get(f"/companion/paper/{pid}/export", params={"format": "ris"})
    assert r.status_code == 200, r.text
    assert r.text.strip() == ""                      # 还没有论文 → 空导出，而不是 404
    bad = client.get(f"/companion/paper/{pid}/export", params={"format": "xlsx"})
    assert bad.status_code == 400


# ── 前端主题与作用域（轮 18 根因：暗色曾只挂在 Companion 一页）────────
def _webui_src(rel: str) -> str:
    return (pathlib.Path(__file__).resolve().parent.parent / "webui" / "src" / rel).read_text(encoding="utf-8")


def test_dark_mode_is_app_scoped_not_page_scoped():
    app = _webui_src("App.tsx")
    assert 'classList.toggle("dark"' in app and 'readQuery("dark")' in app
    comp = _webui_src("pages/Companion.tsx")
    assert "classList.toggle" not in comp and "yanban-dark" not in comp


# ── 记忆管理抽屉（轮 28）：显式笔记、全量清单、优先召回与删除边界 ──────────
def test_explicit_note_is_listed_and_preferred_by_retriever(client):
    from companion.memory import retriever
    pid = client.post("/companion/project", json={"name": "显式记忆"}).json()["project_id"]
    conn = db_module.get_conn()
    for i in range(3):
        db_module.write_event(conn, pid, "intent_updated", f"更新字段：topic 第{i}次", {})
    conn.commit()

    r = client.post(f"/companion/memory/{pid}/note", json={"text": "我导师要求只用中文文献"})
    assert r.status_code == 200 and r.json()["id"] > 0

    got = retriever.retrieve(pid, "钓鱼网站检测", k=1)
    assert got and got[0]["kind"] == "user_note"          # 学生显式让记住的事优先想起来
    assert got[0]["score"] > 0

    lst = client.get(f"/companion/memory/{pid}/all").json()
    notes = [m for m in lst["memories"] if m["kind"] == "user_note"]
    assert notes and notes[0]["deletable"] is True and notes[0]["blocked"] is False
    assert sum(1 for m in lst["memories"] if m["deletable"]) == 1


def test_note_deletion_boundary(client):
    pid = client.post("/companion/project", json={"name": "删记忆"}).json()["project_id"]
    nid = client.post(f"/companion/memory/{pid}/note", json={"text": "记住：我只用 Linux"}).json()["id"]
    assert client.delete(f"/companion/memory/{pid}/note/{nid}").status_code == 200
    assert all(m["id"] != nid for m in client.get(f"/companion/memory/{pid}/all").json()["memories"])
    assert client.delete(f"/companion/memory/{pid}/note/{nid}").status_code == 404

    conn = db_module.get_conn()
    eid = db_module.write_event(conn, pid, "paper_feedback", "论文[某篇] 状态→favorite", {})
    conn.commit()
    refused = client.delete(f"/companion/memory/{pid}/note/{eid}")
    assert refused.status_code == 400 and "不再引用" in refused.json()["detail"]


def test_memory_list_marks_blocked_and_validates_input(client):
    pid = client.post("/companion/project", json={"name": "屏蔽标记"}).json()["project_id"]
    client.post(f"/companion/memory/{pid}/note", json={"text": "我只有两块3080"})
    client.post(f"/companion/memory/{pid}/block", json={"text": "两块3080"})
    lst = client.get(f"/companion/memory/{pid}/all").json()
    hit = [m for m in lst["memories"] if m["kind"] == "user_note"]
    assert hit[0]["blocked"] is True and lst["blocked"] == ["两块3080"]
    assert client.post(f"/companion/memory/{pid}/note", json={"text": "  "}).status_code == 400
    assert client.get("/companion/memory/ghost/all").status_code == 404
    assert client.post("/companion/memory/ghost/note", json={"text": "x"}).status_code == 404


def test_report_view_exposes_stable_dom_hooks():
    """UI 回归门按 .report-view > div[data-section] 数节；Markdown 自带 report-prose，不能再套一层。"""
    src = _webui_src("components/ReportView.tsx")
    assert '"report-view mt-2"' in src and "data-section={s.head}" in src
    assert src.count('className="report-prose"') == 0


def test_page_deeplink_whitelist_covers_every_rendered_page():
    app = _webui_src("App.tsx")
    rendered = set(re.findall(r'page === "([a-z]+)"', app))
    whitelist = {k.strip() for k in re.search(r"const PAGES: PageKey\[\] = \[(.*?)\]", app, re.S)
                 .group(1).replace('"', "").split(",")}
    assert rendered and rendered <= whitelist
    assert 'readQuery("page")' in app
