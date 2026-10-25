"""跟进卡（CommitmentCard）的接线与"只有一份出处"回归测试。

这里刻意不跑浏览器：源码级断言管的是"接线与单一出处"这两类会静默回退的事实，
真实渲染由 `shot_companion_states.mjs` 截图目视（渲染类判据不许拿文本层冒充）。
"""
import os

os.environ["LLM_MOCK"] = "1"

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
from companion.conversation import commitments

WEBUI = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
COMPANION_TSX = (WEBUI / "pages" / "Companion.tsx").read_text(encoding="utf-8")
CARD_TSX = (WEBUI / "components" / "CommitmentCard.tsx").read_text(encoding="utf-8")
ROUTER_PY = (pathlib.Path(__file__).resolve().parents[1] / "companion" / "api" / "router.py"
             ).read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


# ── 接线 ─────────────────────────────────────────────────────────────
def test_card_is_imported_and_attached_to_every_panel():
    assert 'import CommitmentCard' in COMPANION_TSX
    assert COMPANION_TSX.count("{progress}{followup}") == 4     # 四个面板都垫，不给某一个独享
    assert "{followup}" in COMPANION_TSX


def test_ledger_enters_state_through_absorb_only():
    """流式与非流式两条路都汇到 absorb；再开一处 setLedger 就是第二份抄本。"""
    assert COMPANION_TSX.count("if (d.commitments !== undefined) setLedger(d.commitments);") == 1
    assert "if (data.commitments" not in COMPANION_TSX          # SSE 分支里不许另写一套


def test_card_renders_nothing_without_a_ledger():
    """无台账就整块不渲染——摆一张空卡等于承诺一件不存在的事。"""
    assert "if (!ledger || !ledger.items.length) return null;" in CARD_TSX


# ── 单一出处：状态文案由后端给 ────────────────────────────────────────
def code_of(tsx: str) -> str:
    """去掉注释再扫：判据管的是「代码里抄了一份」，注释里举旧形状的反例不是抄本。
    （首版这条判据第一次跑就红在我自己写的注释上——那是文本层判据的通病，
    正解是按语义归一化，而不是放宽正则或把注释改掉。）"""
    tsx = re.sub(r"/\*.*?\*/", "", tsx, flags=re.S)
    return re.sub(r"^\s*//.*$", "", tsx, flags=re.M)


def test_status_labels_are_not_copied_into_the_frontend():
    body = code_of(CARD_TSX)
    for label in commitments.STATUS_LABELS.values():
        assert label not in body, f"前端抄了一份「{label}」"
    assert "ledger.labels" in body


def test_status_set_is_not_copied_into_the_frontend():
    """文案不抄（上一条已钉），**状态集合**也不许抄：那份硬写的三个状态名
    在后端加一个状态时不会报错，只会让新状态在卡上永远没有按钮——静默的半截功能。"""
    body = code_of(CARD_TSX)
    assert not re.search(r'\[\s*"done"\s*,\s*"blocked"', body), "前端抄了一份状态清单"
    assert "Object.keys(ledger.labels)" in body       # 按钮集合由后端那份 labels 派生


def test_code_of_keeps_code_and_drops_comments():
    """归一化自己也要有活性探针：剥完注释后**代码必须还在**，且被剥掉的只是注释。
    少了这条，`code_of` 一旦退化成空串，上面两条判据就都在空集上读"零命中"而恒绿。"""
    sample = 'const a = ["done", "blocked"];  // 提到 done 与 blocked\n/* 旧形状：["done","blocked","dropped"] */\n<span>正文</span>'
    body = code_of(sample)
    assert '["done", "blocked"]' in body                      # 代码里的那份必须留着，判据才咬得住
    assert '"dropped"' not in body                            # 块注释里的举例要没
    assert "span" in body
    assert not any(ln.strip().startswith("//") for ln in body.splitlines())
    assert code_of(CARD_TSX).strip(), "整份组件被剥空 ⇒ 判据在空集上恒绿"


def test_closed_items_say_how_they_closed():
    """结掉的那条要把状态**写成字**：三种字形（✓ / ! / —）带 aria-hidden，屏幕阅读器读不到，
    视力正常的人也只看得见"一行灰字"，分不清是「卡住了」还是「不用做了」。"""
    body = code_of(CARD_TSX)
    assert 'it.status !== "open"' in body and "{label}" in body
    assert "const label = ledger.labels[it.status]" in body   # 那份字面仍来自后端 labels


def test_labels_cover_every_status_and_stats_carries_them(isolated_free_db):
    led = {"items": [{"do": "跑通基线", "with": "", "dose": "", "check": "", "status": "open",
                      "updated_at": ""}], "artifact": "一张表"}
    db_module.write_event(db_module.get_conn(), "p1", commitments.KIND, "行动清单：跑通基线", led)
    db_module.get_conn().commit()
    st = commitments.stats("p1")
    assert set(st["labels"]) == set(commitments.STATUSES)
    assert st["labels"][st["items"][0]["status"]] == "待回报"


@pytest.fixture()
def isolated_free_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "free.db")
    db_module._CONN = None
    yield
    db_module._CONN = None


# ── 端点契约 ─────────────────────────────────────────────────────────
def test_both_commitment_endpoints_exist():
    assert '@router.get("/commitments/{project_id}")' in ROUTER_PY
    assert '@router.post("/commitments/{project_id}/status")' in ROUTER_PY


def test_api_rejects_unknown_status_and_missing_ledger(client):
    pid = client.post("/companion/project", json={"name": "台账测试"}).json()["project_id"]
    assert client.get(f"/companion/commitments/{pid}").json()["commitments"] is None
    assert client.post(f"/companion/commitments/{pid}/status",
                       json={"index": 0, "status": "done"}).status_code == 404
    assert client.post(f"/companion/commitments/{pid}/status",
                       json={"index": 0, "status": "finished"}).status_code == 400


def test_api_roundtrip_updates_the_ledger(client):
    pid = client.post("/companion/project", json={"name": "台账测试"}).json()["project_id"]
    commitments.record(pid, {"actions": [{"do": "跑通 XGBoost 基线", "check": "准确率到 90%"},
                                         {"do": "画方法谱系图", "check": "每条线有代表方法"}],
                             "artifact": "一张对比表"})
    got = client.get(f"/companion/commitments/{pid}").json()["commitments"]
    assert got["open"] == 2 and got["total"] == 2
    r = client.post(f"/companion/commitments/{pid}/status", json={"index": 1, "status": "done"})
    assert r.status_code == 200
    body = r.json()
    assert body["open"] == 1 and body["commitments"]["items"][1]["status"] == "done"
    assert body["commitments"]["artifact"] == "一张对比表"      # 覆写不许把交付物抹掉


def test_assumption_endpoint_records_the_click(client):
    pid = client.post("/companion/project", json={"name": "假设测试"}).json()["project_id"]
    r = client.post(f"/companion/commitments/{pid}/assumption",
                    json={"agree": False, "text": "其实我是本科生"})
    assert r.status_code == 200 and r.json()["agree"] is False
    row = db_module.get_conn().execute(
        "SELECT summary, detail_json FROM events WHERE id=?", (r.json()["recorded"],)).fetchone()
    assert "纠正" in row["summary"] and "本科生" in row["detail_json"]


# ── 流式改写的呈现方式（append-only）──────────────────────────────────
def test_gate_rewrite_keeps_the_old_version_instead_of_overwriting_it():
    """闸门改写时旧版折叠留档，不原地覆盖：学生可能已经读到了那一版。"""
    assert "superseded = got" in COMPANION_TSX
    assert "{m.superseded && (" in COMPANION_TSX
    assert COMPANION_TSX.count("amendNote(data)") == 2          # amend 与 done 两条路都归因


def test_message_list_declares_log_semantics():
    """role=log + aria-atomic=false：读屏只播新增，替换会把正在读的那句打断。"""
    assert 'role="log"' in COMPANION_TSX
    assert 'aria-live="polite"' in COMPANION_TSX and 'aria-atomic="false"' in COMPANION_TSX


def test_rewrite_reason_comes_from_the_amend_payload_not_a_frontend_count():
    """原因里的数字必须从 amend 载荷取。

    🔴 首版只断言 `"d.removed_titles.length" in COMPANION_TSX`，而全文件有两处这个表达式
    （报告面板也用它），植入把 amendNote 里那处换成"删了几处"仍然绿——扫到别处去了。
    现在把 amendNote 的函数体单独抠出来判。
    """
    at = COMPANION_TSX.index("const amendNote = (")
    body = COMPANION_TSX[at:COMPANION_TSX.index("};", at)]
    assert "d.removed_titles.length" in body
    assert "上一版" in COMPANION_TSX


def test_assumption_block_is_rendered_from_backend_situation():
    assert "ledger.situation" in CARD_TSX and "它这么理解你" in CARD_TSX
    assert "onAssumption={onAssumption}" in COMPANION_TSX
    # 摆位与份数由 test_commitment_inline.py 管（宽屏侧栏 + 窄屏内联各一份）；
    # 两处各写一份计数迟早互相打脸——轮 167 就是这么撞上的，所以判据只留一个家。
    assert "/assumption`" in COMPANION_TSX
