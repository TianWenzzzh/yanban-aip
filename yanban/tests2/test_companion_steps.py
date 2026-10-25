"""B7 composer 工具可见可拒（对标 undermind U11「Remove Deep search」）。

钉三件事（§4 B7 的验收 ① + 两条我自己最怕的）：
① 勾掉检索 ⇒ 外呼次数真的是 0，而且回复带着"本轮没有文献依据"（正向对照：同一句话不勾必须 ≥1 次）；
② 「你勾掉了」和「系统查不动」是两种状态，各有各的键与文案——把拒绝显示成故障，等于替用户编造一次事故；
③ composer 上那份「这一步会做什么」清单不许有第二份真相：文案与工具名都读后端，
   且每颗工具名都要在**真跑这一步的那个函数**里读得到（摆一颗没人调的控件就是骗用户"我能控制它"）。
"""
from __future__ import annotations

import inspect
import os
import pathlib

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import dialogue_manager as dm
from companion.literature import engine

SAY = "我们实验室在做光伏功率预测，我想找一个还没人做的点"
ROOT = pathlib.Path(__file__).resolve().parents[1]
TSX = ROOT / "webui" / "src" / "pages" / "Companion.tsx"
ROUTER = ROOT / "companion" / "api" / "router.py"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    yield
    db_module._CONN = None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


@pytest.fixture()
def counter(monkeypatch):
    """把引擎换成计数器：外呼次数只能由它说，不许由"看起来没查"推断。"""
    calls: list[str] = []

    def spy(intent, limit=8):
        calls.append(engine._query_from_intent(intent))
        return [{"id": "p1", "title": "Photovoltaic power forecasting baseline"}]

    monkeypatch.setattr(engine, "search_for_intent", spy)
    return calls


# ── ① 勾掉 = 真的一次都不查 ─────────────────────────────────────────────
def test_declining_search_issues_zero_provider_calls(isolated, counter):
    """正向对照在前：同一句话不勾时必须真的发起过检索，否则下面那条 0 是空转出来的。"""
    dm.prepare_turn(SAY, dm.create_project(name="不检索"))
    assert len(counter) >= 1, "这句话本来就没走到检索，勾掉不勾都同样是 0——这条用例什么都没测"

    counter.clear()
    turn = dm.prepare_turn(SAY, dm.create_project(name="不检索"), no_search=True)
    assert counter == [], f"勾掉了检索却仍然外呼 {len(counter)} 次：用户的拒绝没生效"
    assert turn["papers"] == []


def test_declined_reply_carries_the_no_evidence_note(client, counter):
    counter.clear()
    pid = dm.create_project(name="不检索")
    r = client.post("/companion/chat", json={"user_text": SAY, "project_id": pid,
                                             "no_search": True})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d.get("search_note"), "勾掉检索后的回复没带任何提示：界面会读成「这轮有依据」"
    assert "没有文献依据" in d["search_note"]
    assert d["papers"] == []
    assert "search_error" not in d, "用户自己勾掉了检索，不许同时报「检索挂了」"


# ── ② 三种状态彼此可分：正常 / 你勾掉了 / 查不动 ────────────────────────
def test_three_states_are_distinguishable(client, counter, monkeypatch):
    pid = dm.create_project(name="状态")
    ok = client.post("/companion/chat", json={"user_text": SAY, "project_id": pid}).json()
    assert "search_note" not in ok and "search_error" not in ok, \
        f"正常一轮不该出现任何一种提示：{ok.get('search_note')!r} / {ok.get('search_error')!r}"
    assert ok["papers"], "正向对照：这一轮真带着检索回来的论文，上面那条「没有提示」才不是空转"

    declined = client.post("/companion/chat", json={"user_text": SAY, "project_id": pid,
                                                    "no_search": True}).json()
    assert declined.get("search_note") and "search_error" not in declined

    def boom(intent, limit=8):
        raise RuntimeError("429 too many requests")

    monkeypatch.setattr(engine, "search_for_intent", boom)
    pid2 = dm.create_project(name="故障")
    failed = client.post("/companion/chat", json={"user_text": SAY, "project_id": pid2}).json()
    assert failed.get("search_error"), "检索挂了却不吭声"
    assert "search_note" not in failed, \
        "系统故障被写成了「你勾掉了检索」——那是替用户编造一次他没做过的决定"


def test_decline_is_this_turn_only(isolated, counter):
    """拒绝只活一轮。落库会让下一轮在用户没点头的情况下继续不查、继续报"无依据"。"""
    pid = dm.create_project(name="不检索")
    counter.clear()
    dm.prepare_turn(SAY, pid, no_search=True)
    intent = dm.load_intent(pid)
    assert "search_declined" not in (intent.extra or {}), \
        "当轮的拒绝被写进了 intent.extra：下一轮会替用户继续拒绝"

    counter.clear()
    dm.prepare_turn(SAY, pid)
    assert len(counter) >= 1, f"上一轮勾掉之后这一轮再也不查了（{counter}）：一次性标记变成了永久开关"


def test_decline_does_not_advance_the_state_machine(isolated, counter):
    """不查就不该有"把需求写成检索式 / 多源检索"这两步——状态历史是给审计看的，不能虚记。"""
    pid = dm.create_project(name="不检索")
    turn = dm.prepare_context(SAY, pid)
    before = dm.get_state(pid).value
    dm.run_search(turn, no_search=True)
    assert dm.get_state(pid).value == before, "勾掉检索仍把状态机推到了检索之后的阶段"


def test_stream_marks_the_declined_step_skipped(client, counter):
    """界面真正走的是 /chat/stream：这条把验收 ① 在**那条路径**上再测一遍。

    只测 `/chat` 会漏掉一整条产品路径——前端的「这一步」清单就是从这里的事件长出来的。
    """
    counter.clear()
    pid = dm.create_project(name="流式不检索")
    r = client.post("/companion/chat/stream",
                    json={"user_text": SAY, "project_id": pid, "no_search": True})
    assert r.status_code == 200, r.text[:200]
    search_steps = [ln for ln in r.text.splitlines()
                    if ln.startswith("data:") and '"id": "search"' in ln]
    assert len(search_steps) == 1, f"检索那一步在流里出现 {len(search_steps)} 条，应恰好一条"
    assert '"status": "skipped"' in search_steps[0] and '"reason": "declined"' in search_steps[0], \
        search_steps[0][:200]
    assert '"search_note"' in r.text, "流式的 done 没带「本轮无文献依据」"
    assert counter == [], f"流式路径勾掉了仍在查：{counter}"


# ── ③ 清单只有一份真相，且每颗工具名都对得上代码 ────────────────────────# 每个步骤**由哪个函数真的去调**那颗工具：清单写 engine.search_for_intent 而 run_search 里根本没有它，
# 就等于给用户看一块装饰板。
READER = {"read": "_mentor_kwargs", "search": "run_search", "write": "stream_reply"}


def test_steps_endpoint_names_a_tool_that_the_code_actually_calls(client):
    r = client.get("/companion/chat/steps")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    steps = body["steps"]
    assert [s["id"] for s in steps] == ["read", "search", "write"]
    for s in steps:
        assert s["label"], f"{s['id']} 没有文案"
        module_path, _, attr = s["tool"].rpartition(".")
        source = inspect.getsource(getattr(dm, READER[s["id"]]))
        assert attr in source, f"{s['id']} 声明调用 {s['tool']}，但 {READER[s['id']]} 里找不到这个调用"
        assert module_path and pathlib.Path(*module_path.split(".")).with_suffix(".py").exists(), \
            f"{s['id']} 的工具路径 {s['tool']} 落不到仓里真实存在的文件"

    assert body["declinable"] == ["search"], f"可拒的那一步必须唯一且确实是检索：{body['declinable']}"
    search = next(s for s in steps if s["id"] == "search")
    assert search.get("decline_label"), "可勾掉的那一步没有文案，前端只能自己编一句"


def test_stream_events_and_the_prewrite_list_share_one_table():
    src = ROUTER.read_text(encoding="utf-8")
    for sid in ("read", "search", "write"):
        assert f'step("{sid}")' in src, f"router 没有从 CHAT_STEPS 取 {sid}，它自己抄了一份"
    for label in ("读你这句话", "检索文献（Crossref", "组织回复"):
        assert label not in src, f"router 里还剩一份文案抄本「{label}」：两处会各自漂"


def test_frontend_has_no_copy_of_the_step_labels():
    tsx = TSX.read_text(encoding="utf-8")
    for label in ("读你这句话", "检索文献（Crossref", "组织回复"):
        assert label not in tsx, f"前端抄了一份步骤文案「{label}」：后端加一步它不会跟着变"
    assert "/chat/steps" in tsx, "前端没读那份清单，发送前可见就是空话"
    assert "no_search" in tsx and tsx.count("no_search") >= 2, \
        "勾掉的标记没随请求发出去（流式与非流式两条路都要带）"
    # 后端那句话必须**送进提示条**，光在源码里出现一次键名不算接上（植入 P15 就是这么被抓的）
    got = tsx.split("if (d.search_note !== undefined) {", 1)
    assert len(got) == 2, "前端根本没读后端那句「本轮无文献依据」"
    assert "searchErrRef.current = String(d.search_note" in got[1].split("}", 1)[0], \
        "读到了键却没把它送进那一条回复的提示条"


DECLINED_COPY = "这轮你勾掉了检索，下面的话没有文献依据"
FAILED_COPY = "这轮文献检索没成功，下面的话暂时没有文献依据"


def test_refusal_and_failure_render_differently():
    """两态各有 testid 与配色，且勾掉那态的文案不许出现"没成功"——它是用户的选择，不是系统的事故。"""
    tsx = TSX.read_text(encoding="utf-8")
    assert '"note-strip-declined"' in tsx and '"note-strip-failure"' in tsx
    assert DECLINED_COPY in tsx and FAILED_COPY in tsx and DECLINED_COPY != FAILED_COPY
    assert "没成功" not in DECLINED_COPY
    # 配色也分开：拒绝=中性灰，故障=琥珀色。共用一个色 = 两态在屏上长成一张脸
    assert '"bg-gray-100 text-gray-600"' in tsx and '"bg-amber-50 text-amber-600"' in tsx
    assert "skipped" in tsx, "进度条没有 skipped 这一档，勾掉的那步会被显示成已完成"
