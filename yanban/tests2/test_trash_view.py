"""U14 读侧回收站：'不感兴趣'必须是撤得回的，且"已排除多少篇"这个数不许前端自己编。

写侧（`paper_memory.record_feedback` 支持 rejected、`list_papers(status=)` 能按状态取）上一轮就有，
缺的是读侧：界面上只有单向按钮，没有"看已排除的那批 / 恢复"的入口。这一轮补入口，
判据分两层——后端"这批料真取得到/恢复得回"，前端"数与名单只能问后端"。

六条判据各自的来由：
① 按状态取数只返回该状态的行（回收站不能混进没排除的）。
② 恢复 = 改状态，不是删除：改回 recommended 后它必须重新出现在总列表里，回收站清空。
③ 回收站按项目隔离（跨项目串成一片的回收站会让人误恢复）。
④ 前端"已排除 N 篇"的 N 必须是后端 `?status=rejected` 返回的长度，
   不许 `papers.filter(...)` 自己数：papers 里那批换一轮 done 事件就可能不再回来，自己数会数出 0。
⑤ 两个恢复入口（回收站行内 + 论文卡片）都必须发 status="recommended"，
   且卡片上的按钮形态由 `p.status === "rejected"` 派生（排除过必须看得见）。
⑥ 每次反馈成功后必须重读回收站（只在挂载时读一次 ⇒ 刚排除完数字还是旧的，就是个会撒谎的计数）。
"""
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.memory import paper_memory

TSX = pathlib.Path(__file__).resolve().parents[1] / "webui/src/pages/Companion.tsx"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def _paper(pid: str, title: str) -> dict:
    return {"id": pid, "title": title, "year": 2024, "citation_count": 10,
            "venue": "V", "doi": f"10.1/{pid}", "url": f"http://x/{pid}",
            "authors": ["A"], "type": "journal-article"}


def _reject(client, project: str, paper: dict, reason: str = "不相关"):
    r = client.post("/companion/paper/feedback",
                    json={"project_id": project, "paper": paper, "status": "rejected", "reason": reason})
    assert r.status_code == 200, r.text
    return r


# ── 后端：这批料真取得到、恢复得回 ────────────────────────────────────
def test_rejected_listing_only_returns_rejected(client):
    pid = "projA"
    papers = [_paper("a1", "A1"), _paper("a2", "A2"), _paper("a3", "A3")]
    paper_memory.save_many(pid, papers)
    _reject(client, pid, papers[1])

    only = client.get(f"/companion/paper/{pid}", params={"status": "rejected"}).json()["papers"]
    assert [p["id"] for p in only] == ["a2"], only
    assert only[0]["user_feedback"] == "不相关", "回收站里要能看到当时写的排除理由"
    assert len(client.get(f"/companion/paper/{pid}").json()["papers"]) == 3, "排除不等于删掉"


def test_restore_returns_the_paper_to_the_pool(client):
    pid = "projB"
    papers = [_paper("b1", "B1"), _paper("b2", "B2")]
    paper_memory.save_many(pid, papers)
    _reject(client, pid, papers[0])
    assert client.get(f"/companion/paper/{pid}", params={"status": "rejected"}).json()["papers"]

    r = client.post("/companion/paper/feedback",
                    json={"project_id": pid, "paper": papers[0], "status": "recommended", "reason": ""})
    assert r.status_code == 200, r.text
    assert client.get(f"/companion/paper/{pid}", params={"status": "rejected"}).json()["papers"] == []
    back = [p["id"] for p in client.get(f"/companion/paper/{pid}").json()["papers"]]
    assert back == ["b1", "b2"], "恢复后必须回到总列表（这里断言的是'还在'，不是顺序）"


def test_trash_is_scoped_per_project(client):
    paper_memory.save_many("projC", [_paper("c1", "C1")])
    paper_memory.save_many("projD", [_paper("d1", "D1")])
    _reject(client, "projC", {"id": "c1", "title": "C1"})
    _reject(client, "projD", {"id": "d1", "title": "D1"})
    for proj, want in (("projC", ["c1"]), ("projD", ["d1"])):
        got = [p["id"] for p in
               client.get(f"/companion/paper/{proj}", params={"status": "rejected"}).json()["papers"]]
        assert got == want, f"{proj} 的回收站串了：{got}"


# ── 前端：数与名单只能问后端，恢复必须双向可达 ─────────────────────────
def src() -> str:
    return TSX.read_text(encoding="utf-8")


def code_only() -> str:
    """注释里也会出现 `?status=rejected` 这类字样：判据只数代码行，否则"数了几次"就是假的。"""
    return "\n".join(ln for ln in src().split("\n") if not ln.strip().startswith("//"))


def line_of(text: str, needle: str) -> int:
    hits = [i for i, ln in enumerate(text.split("\n")) if needle in ln]
    assert len(hits) == 1, f"{needle!r} 应恰好出现 1 次，实际 {len(hits)} 次"
    return hits[0]


def test_trash_count_comes_from_backend_not_from_papers():
    s = code_only()
    assert s.count("?status=rejected") == 1, "取回收站的路径应当只有一处；多处各写一遍必漂"
    assert "setExcluded(r.ok ? (((await r.json()).papers ?? []) as Paper[]) : [])" in s, \
        "名单必须整个取自后端响应"
    assert "papers.filter(" not in s, "不许从 papers 自己数已排除的（那批换轮检索就不在里面了）"
    assert re.search(r"已排除 \{excluded\.length\} 篇", s), "计数那一句要真的渲染 excluded.length"


def test_restore_entries_post_recommended_and_card_shows_state():
    s = code_only()
    assert s.count('feedback(p, "recommended")') == 2, \
        "回收站行内 + 论文卡片两处恢复入口，少一处就是单向按钮"
    assert 'data-testid="trash-restore"' in s and 'data-testid="card-restore"' in s
    assert 'p.status === "rejected"' in s, "卡片必须看得出这篇已被排除，否则'不感兴趣'点了没反馈"
    assert 'onClick={() => feedback(p, "rejected")}' in s


def test_trash_refreshes_after_every_feedback():
    s = src()
    body = s[s.index("const feedback = useCallback"):s.index("const drawerQaAsk")]
    assert "void loadExcluded()" in body, "反馈成功后要重读回收站，否则刚排除完计数还是旧的"
    assert "void loadExcluded();" in body.split("if (r.ok)")[1][:200], "重读必须在成功分支里"
    assert "[projectId, loadExcluded]" in body, "loadExcluded 进了依赖表，closure 才不会抓旧 projectId"


def test_trash_entry_is_reachable_with_no_papers_this_session():
    """本轮没检索过（papers 为空）时也要能捞回历史排除的那批：入口包的保护式必须含 excluded。"""
    s = code_only()
    guard = "(papers.length > 0 || excluded.length > 0) && ("
    line_of(s, guard)
    assert abs(line_of(s, guard) - line_of(s, 'data-testid="trash-toggle"')) <= 4, \
        "入口不在那层保护式里面（或那层保护式被复制成了两处）"
    assert line_of(s, '{papers.length > 0 && (') < line_of(s, 'data-testid="trash-toggle"')
