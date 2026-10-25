"""轮 191：记忆清单的逐条出处必须来自 events.detail_json，查不到就明说"无出处记录"。

未决第 4 条（U9）说的是"面板对画像印出处、对消息列表只印文本"。库里其实有：
`events.detail_json` 由每个生产写入点填（router 的笔记、paper_memory 的反馈、
dialogue_manager 的状态机与画像、commitments 的台账与点击），只是 `/memory/{pid}/all`
当初 SELECT 没带这一列。接线之后，"这条凭什么算数"就有了程序可查的答案；
而 detail 为空的那些行（实测：user_note/decision/project_created 三类全空）**不许编一句出处**，
只能显示"无出处记录"——这是本文件存在的意义。

八条判据：
① 笔记行的出处=你显式让我记住（detail 里 explicit 派生）。
② 论文反馈行的出处带**完整标题**：标题在 summary 里被截到 40 字，这里断言的是 40 字之后的尾巴，
   所以它不可能从 summary 抄来，只能来自 detail_json。
③ 「不对」点击行的出处带学生补的话：summary 只有"纠正了研伴的理解"，话在 detail 里。
④ detail 为空的行 ⇒ source 必须是空串（不许拿 kind 编一句、不许缺键）。
⑤ 状态切换行的出处=研伴状态机记的阶段切换（谁写的：系统，不是你）。
⑥ 每一行都必须带 source 键（缺键时前端渲染 undefined，看上去像"没出处"其实是有 bug）。
⑦ 面板把 source 印出来，且空串时印"无出处记录"。
⑧ 谁-文案这张表只许在后端有一份：webui/src 里出现任何一条串就算红
   （前端自己映射一遍 = 两处必漂，且界面上会出现库里没有的说法）。
"""
import os
import pathlib

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import commitments, dialogue_manager
from companion.memory import paper_memory

WEBUI = pathlib.Path(__file__).resolve().parents[1] / "webui/src"
PANEL = WEBUI / "pages/MemoryPanel.tsx"
ROUTER = pathlib.Path(__file__).resolve().parents[1] / "companion/api/router.py"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def _pid(client, name="出处"):
    return client.post("/companion/project", json={"name": name}).json()["project_id"]


def _rows(client, pid):
    r = client.get(f"/companion/memory/{pid}/all")
    assert r.status_code == 200, r.text
    return {m["kind"]: m for m in r.json()["memories"]}


# ── 后端：出处逐条来自库里已有的东西 ──────────────────────────────────
def test_note_row_source_names_who_wrote_it(client):
    pid = _pid(client)
    client.post(f"/companion/memory/{pid}/note", json={"text": "导师只认中文核心期刊"})
    assert _rows(client, pid)["user_note"]["source"] == "你显式让我记住"


def test_paper_feedback_source_carries_the_full_title(client):
    """标题在 summary 里被截到 40 字，这里断言 40 字之后的尾巴 ⇒ 只能来自 detail_json。"""
    pid = _pid(client)
    title = "Site-level photovoltaic forecasting with limited data under cloudy seasons"
    paper_memory.save_many(pid, [{"id": "x1", "title": title, "year": 2024, "citation_count": 3}])
    client.post("/companion/paper/feedback",
                json={"project_id": pid, "paper": {"id": "x1", "title": title},
                      "status": "rejected", "reason": "方法不对路"})
    row = _rows(client, pid)["paper_feedback"]
    assert title[:20] in row["text"], "先确认 summary 里确实截断了（否则下面那条断言没在测东西）"
    assert len(row["text"].split("[")[1].split("]")[0]) == 40, "summary 的标题截断长度变了，本判据要重算"
    assert row["source"] == f"你在论文上的标记：《{title}》"


def test_assumption_feedback_source_carries_the_student_words(client):
    pid = _pid(client)
    commitments.note_assumption(pid, False, "其实我用的是树莓派，不是工控机")
    row = _rows(client, pid)["assumption_feedback"]
    assert row["text"] == "纠正了研伴的理解", "summary 里没有学生补的话，出处才有资格断言它"
    assert row["source"] == "你确认/纠正了研伴的理解：「其实我用的是树莓派，不是工控机」"


def test_rows_with_no_detail_report_empty_source_not_a_fabricated_one(client):
    pid = _pid(client)
    db_module.write_event(db_module.get_conn(), pid, "decision", "选定对比维度：数据集/指标")
    db_module.get_conn().commit()
    row = _rows(client, pid)["decision"]
    assert row["source"] == "", "库里没记出处就返回空串；编一句是把没有的说成有"
    assert row["text"] == "选定对比维度：数据集/指标", "空出处不影响这一行照常列出"


def test_state_change_source_says_the_system_wrote_it(client):
    pid = _pid(client)
    dialogue_manager.transition(pid, dialogue_manager.State.FORMULATE, "补齐了时间窗")
    row = _rows(client, pid)["state_change"]
    assert row["source"] == "研伴状态机记的阶段切换"


def test_every_row_carries_a_source_key(client):
    pid = _pid(client)
    client.post(f"/companion/memory/{pid}/note", json={"text": "先跑 URL 基线"})
    r = client.get(f"/companion/memory/{pid}/all")
    ms = r.json()["memories"]
    assert ms, "没有行就没在测东西"
    assert all("source" in m for m in ms), "缺键时前端印 undefined，看上去像'没出处'其实是 bug"
    assert {"project_created", "user_note"} <= {m["kind"] for m in ms}


# ── 前端：只印后端给的那一句，不自己映射第二遍 ────────────────────────
def panel_code() -> str:
    text = PANEL.read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith("//"))


def test_frontend_prints_backend_source_or_says_none():
    s = panel_code()
    assert s.count('出处：{m.source || "无出处记录"}') == 1
    assert "deletable: boolean; source: string }" in s, "类型里没 source 就不会跟着响应走"
    assert "data-testid={`mem-source-${m.id}`}" in s, "行级 testid 是 CDP 逐行对账用的锚点"


def test_the_who_table_exists_only_in_the_backend():
    """谁-文案这张表只能有一份。前端抄一遍 ⇒ 两处各改各的，界面上就会出现库里没有的说法。"""
    who_lines = [ln for ln in (ROUTER.read_text(encoding="utf-8").split("\n"))
                 if "episodic_memory.source_label" in ln]
    assert len(who_lines) == 1, f"后端出处只许一处装配，实际 {len(who_lines)}"
    for tsx in WEBUI.rglob("*.tsx"):
        body = tsx.read_text(encoding="utf-8")
        for phrase in ("你显式让我记住", "你在论文上的标记", "研伴状态机记的阶段切换",
                       "研伴从你这轮话里抽取", "研伴规划时记的台账", "无出处记录"):
            if phrase == "无出处记录":
                assert body.count(phrase) <= 1, f"{tsx.name} 里「无出处记录」超过一处（兜底文案被复制）"
            else:
                assert phrase not in body, f"{tsx.name} 抄了后端文案：{phrase}"
