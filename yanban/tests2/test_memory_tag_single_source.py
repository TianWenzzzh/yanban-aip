"""轮 192：记忆「这是条什么」的标签只许有一张表，且它必须盖住所有真实写入点。

实测到的缺陷（`输出/_scratch/measure_r192_kinds_v2.py` RC=0，别凭读码印象复述）：
- `MemoryPanel.tsx` 顶部另写了一张 6 个键的 `KIND_LABEL`，而**同一行**下面那句「出处」来自后端
  `SOURCE_WHO` ⇒ 一行里两个口径；4 个 kind 措辞不同（前端「意图变化」vs 后端「研伴从你这轮话里抽取」），
  库里另外 3 个 kind（commitment / profile_learned / project_created）前端那张表根本没有 ⇒ 徽标直接露英文。
- 对话区「研伴这轮参考了 N 条记录」逐条打的是 `[{m.source}]` = 原始英文 kind（user_note），
  同一个东西在面板叫「你让我记住」、在对话里叫 `user_note`。

八条判据各自的来由：
① 双向对账：表里的键集合必须 == 代码里真会写进 events.kind 的那些字符串。
   少一个 ⇒ 界面上露英文；多一个 ⇒ 是没人写的死条目（`tool_call` 就是这么留在旧表里的）。
② 认不出的 kind 退回原始 kind，不许编一句标签（与轮 191「库里没有就写无出处记录」同一条线）。
③ 召回还会取到 messages 表的行：user 与 assistant 必须分开说，否则把研伴自己的话记成"你说过"。
④ 清单接口每行带 `tag`，值必须等于 `source_tag(kind)`（前端不许自己再算一遍）。
⑤ 召回载荷的 tag 走同一张表；聊天行没有 event_id ⇒ 必须是 None，不是 0、不是编一个。
⑥ `event_id` 必须就是面板那一行的 id（这才能从"这轮参考了它"跳到"清单里的它"）。
⑦ 前端那张表必须真的没了（`KIND_LABEL` 零命中，后端标签串零命中）。
⑧ 标签装配只住在 `episodic_memory`：表定义 1 处、后端调用点 2 处（清单 + 召回），前端 0 处。
"""
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion import db
from companion.conversation import dialogue_manager as dm
from companion.memory import episodic_memory, retriever

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui" / "src"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


# ── ① 覆盖度：表里的键 == 代码里真写的 kind ─────────────────────────
def _kinds_written_in_code() -> set[str]:
    """events.kind 的字面量走两种形状：`write_event(conn, pid, "user_note", ...)` 位置参数，
    和 `write_event(conn, project_id, KIND, ...)`——后者要先在同文件里把 KIND 解析出来。"""
    called, kind_consts = set(), {}
    for f in (ROOT / "companion").rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'^KIND\s*=\s*"([a-z_]+)"', text, re.M):
            kind_consts[m.group(1)] = True
        for ln in text.split("\n"):
            m = re.search(r"write_event\([^)]*?,\s*\"([a-z_]+)\"", ln)
            if m:
                called.add(m.group(1))
            if re.search(r"write_event\([^)]*?,\s*KIND\s*,", ln) and kind_consts:
                called |= set(kind_consts)
    return called


def test_the_tag_table_covers_every_kind_the_code_writes():
    written = _kinds_written_in_code()
    assert written, "扫描没认出任何写入点，这条判据就成了装饰"
    missing = sorted(written - set(episodic_memory.SOURCE_TAG))
    unused = sorted(set(episodic_memory.SOURCE_TAG) - written)
    assert not missing, f"这些 kind 代码里会写、表里没标签（界面上会露英文）：{missing}"
    assert not unused, f"这些标签对应的 kind 没有任何写入点（死条目就该删）：{unused}"


# ── ② 认不出就退回原始 kind，不编 ───────────────────────────────────
def test_an_unlisted_kind_falls_back_to_the_raw_kind_instead_of_a_made_up_tag():
    assert episodic_memory.source_tag("decision") == "decision"
    assert episodic_memory.source_tag("") == ""
    assert episodic_memory.source_label("decision", {}) == ""   # 出处那一层仍然不许编


# ── ③ 聊天记录行：谁说的必须分开 ─────────────────────────────────────
def test_transcript_rows_name_the_speaker_without_mixing_them_up():
    assert episodic_memory.source_tag("", "user") == "你说过的话"
    assert episodic_memory.source_tag("", "assistant") == "研伴说过的话"
    assert episodic_memory.source_tag("", "user") != episodic_memory.source_tag("", "assistant")
    assert episodic_memory.source_tag("", "system") == "system"   # 认不出就原样，不套到任何一方头上


# ── ④⑤⑥ 接口与召回载荷 ──────────────────────────────────────────────
def _pid(client, name="标签"):
    return client.post("/companion/project", json={"name": name}).json()["project_id"]


def _rows(client, pid):
    d = client.get(f"/companion/memory/{pid}/all").json()["memories"]
    return {r["kind"]: r for r in d}


def test_the_list_endpoint_hands_the_badge_text_over(client):
    pid = _pid(client)
    conn = db.get_conn()
    for kind, summary in (("user_note", "我导师只认中文核心"),
                          ("commitment", "跑通 URL 模态基线"),
                          ("decision", "选定对比维度")):
        db.write_event(conn, pid, kind, summary, None)
    conn.commit()
    rows = _rows(client, pid)
    for kind, row in rows.items():
        assert row["tag"] == episodic_memory.source_tag(kind), (kind, row["tag"])
    assert rows["user_note"]["tag"] == "你让我记住"
    assert rows["decision"]["tag"] == "decision", "库里查不到写入点就露原始 kind，不编一句"
    assert rows["decision"]["source"] == "", "出处与标签是两件事：没出处仍然不许编出处"


def test_the_recall_row_carries_the_same_tag_plus_an_anchor():
    turn = {"memory_items": [
        {"id": 41, "kind": "user_note", "summary": "我导师只认中文核心", "score": 1.4},
        {"role": "assistant", "content": "那我们就只看中文核心期刊", "score": 0.6}],
        "papers": [], "intent": None, "project_id": "p", "state": "CLARIFY",
        "changed": [], "still_clarifying": True}
    mem = dm._used_memory(turn)
    assert [m["tag"] for m in mem] == ["你让我记住", "研伴说过的话"], mem
    assert mem[0]["event_id"] == 41 and mem[0]["source"] == "user_note"
    assert mem[1]["event_id"] is None, "聊天行没有事件行，锚点必须空着而不是拿 0 冒充"
    assert mem[1]["text"] == "那我们就只看中文核心期刊"


def test_the_recall_anchor_points_at_the_very_row_the_panel_lists(client):
    pid = _pid(client, "锚点")
    conn = db.get_conn()
    eid = db.write_event(conn, pid, "user_note", "我必须坚持用 BraTS 数据集", None)
    conn.commit()
    got = [m for m in retriever.retrieve(pid, "该用什么数据集", k=3) if m.get("kind") == "user_note"]
    assert got, "检索没想起这条笔记，锚点判据就成了空转"
    mem = dm._used_memory({"memory_items": got})
    assert mem and mem[0]["event_id"] == eid, (mem, eid)
    row = _rows(client, pid)["user_note"]
    assert row["id"] == eid, "跳过去的必须是面板里那一行，不是另一条同名笔记"


# ── ⑦⑧ 前端不许有第二张表，装配只在一处 ─────────────────────────────
def _webui_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in WEBUI.rglob("*.tsx"))


def test_the_frontend_no_longer_keeps_its_own_label_table():
    s = _webui_text()
    assert "KIND_LABEL" not in s, "前端又长回一张标签表（那必然与后端漂）"
    for tag in episodic_memory.SOURCE_TAG.values():
        assert f'"{tag}"' not in s and f"'{tag}'" not in s, f"前端抄了后端文案：{tag}"
    panel = (WEBUI / "pages" / "MemoryPanel.tsx").read_text(encoding="utf-8")
    assert "{m.tag}" in panel and "tag: string" in panel
    comp = (WEBUI / "pages" / "Companion.tsx").read_text(encoding="utf-8")
    assert comp.count("[{m.tag}]") == 1, "对话区那行的标签来源必须只有一处写法"
    assert comp.count("{m.tag}：") == 1


def test_the_tag_table_lives_only_in_the_backend():
    hits = []
    for p in (ROOT / "companion").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "SOURCE_TAG" in text:
            hits.append((p.name, text.count("SOURCE_TAG")))
    assert [h for h in hits if h[0] == "episodic_memory.py"][0][1] >= 1
    assert all(p == "episodic_memory.py" for p, _ in hits), f"标签表被抄到别的文件：{hits}"
    ep = (ROOT / "companion" / "memory" / "episodic_memory.py").read_text(encoding="utf-8")
    assert ep.count("SOURCE_TAG = {") == 1, "定义本身出现了第二份"
    call_sites = []
    for p in (ROOT / "companion").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(text.split("\n"), 1):
            if "source_tag(" in ln and "def source_tag" not in ln:
                call_sites.append(f"{p.name}:{i}")
    assert len(call_sites) == 2, f"标签装配应有 2 个调用点（清单 + 召回），实际 {call_sites}"
    assert all("MemoryPanel" not in c and "Companion" not in c for c in call_sites)
