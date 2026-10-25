"""B1 证据底座计数条（对标 undermind U2）：这一轮的回答踩在多少料上，这些数一律由后端算。

钉三件事：
① 数从 `intent.extra` 与库里来，改一个桩值事件字段就得跟着改（不是前端自己数一遍）；
② 「没这个数」与「这个数是 0」必须是两种读数——反问轮没检索，写成 0 就是把"没查"
   伪装成"查了但一无所获"（轮 105/106 同类的谎）；
③ 前端那一行只渲染 `evidence_basis`，不出现 `papers.length`、也不许出现数字常量。

判据一律走产品入口（prepare_turn / SSE 端点），不直接调私有函数——同 test_search_diagnostics 的纪律。
"""
import json
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import dialogue_manager as dm
from companion.intent.model import ResearchIntent
from companion.literature import engine
from companion.memory import paper_memory

SAY = "我们实验室在做光伏功率预测，我想找一个还没人做的点"
KEYS = ("search_engine_returned", "search_stored", "off_topic_dropped",
        "query_variants", "search_ms", "library_pdfs", "library_abstracts")


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


def _stub_search(returned: int, variants: int, dropped: int):
    """假引擎：既返回论文，也把引擎自己记账的那两个数写进 intent.extra（与真 engine 同形）。"""
    def go(intent, limit=8):
        intent.extra["query_variants"] = variants
        intent.extra["off_topic_dropped"] = dropped
        return [{"id": f"p{i}", "title": f"Photovoltaic power forecasting study {i}"}
                for i in range(returned)]
    return go


def test_the_four_search_numbers_come_from_intent_extra(isolated, monkeypatch):
    monkeypatch.setattr(engine, "search_for_intent", _stub_search(5, 3, 2))
    turn = dm.prepare_turn(SAY, None)
    basis = dm.evidence_basis(turn)
    assert (basis["search_engine_returned"], basis["search_stored"]) == (5, 5)
    assert (basis["query_variants"], basis["off_topic_dropped"]) == (3, 2)


@pytest.mark.parametrize("returned,variants", [(5, 3), (7, 4)])
def test_changing_the_stub_moves_the_reported_number(isolated, monkeypatch, returned, variants):
    """桩数据改一个值，读数必须跟着变——否则这一行是在打印常量。"""
    monkeypatch.setattr(engine, "search_for_intent", _stub_search(returned, variants, 0))
    turn = dm.prepare_turn(SAY, None)
    basis = dm.evidence_basis(turn)
    assert basis["search_engine_returned"] == returned
    assert basis["query_variants"] == variants


def test_a_clarifying_turn_reads_as_none_not_zero(isolated, monkeypatch):
    """反问轮根本没检索：那四个数必须是 None（"未记录"），不许用 0 冒充"查过但什么都没有"。"""
    monkeypatch.setattr(engine, "search_for_intent", lambda *a, **k: [])
    turn = dm.prepare_context("在吗，我有件事想请教", None)
    assert turn["still_clarifying"], "这条样本必须真的被判成反问轮，否则测试是空转"
    basis = dm.evidence_basis(dm.run_search(turn))
    for key in ("search_engine_returned", "search_stored", "off_topic_dropped", "query_variants"):
        assert basis[key] is None, f"{key} 没发生过却读成 {basis[key]!r}：None 与 0 不许合并"
    # 库内两格是真读出来的零——与上面那四格的 None 必须不同形
    assert (basis["library_pdfs"], basis["library_abstracts"]) == (0, 0)


def test_library_counts_abstracts_and_cached_pdfs(isolated):
    from companion.paper import downloader
    pid = dm.create_project(name="计数")
    paper_memory.save_many(pid, [
        {"id": "a1", "title": "Paper one", "abstract": "has an abstract"},
        {"id": "a2", "title": "Paper two", "abstract": "  "},        # 空白摘要不算有
        {"id": "a3", "title": "Paper three", "abstract": "also one"},
    ])
    out = downloader.PDF_DIR / pid
    out.mkdir(parents=True, exist_ok=True)
    (out / "a1.pdf").write_bytes(b"%PDF-1.4")
    (out / "a3.pdf").write_bytes(b"")                                # 空文件=没缓存成功
    assert paper_memory.library_counts(pid) == {"abstracts": 2, "pdfs": 1}
    intent = ResearchIntent(project_id=pid)
    basis = dm.evidence_basis({"project_id": pid, "intent": intent})
    assert (basis["library_pdfs"], basis["library_abstracts"]) == (1, 2)


def _sse_events(raw: str) -> dict[str, dict]:
    """把 SSE 文本切成 {事件名: 载荷}；meta/done 各只许出现一次。"""
    out, name = {}, ""
    for line in raw.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:") and name in ("meta", "done"):
            assert name not in out, f"{name} 出现了第二次，这一行没有唯一出处"
            out[name] = json.loads(line[5:].strip())
    return out


def test_stream_carries_the_basis_on_both_meta_and_done(client, monkeypatch):
    """走真端点：前端那一行的数只能来自 meta/done 的字段，所以字段必须在传输层就存在。"""
    monkeypatch.setattr(engine, "search_for_intent", _stub_search(4, 2, 1))
    with client.stream("POST", "/companion/chat/stream",
                       json={"user_text": SAY, "project_id": None}) as r:
        assert r.status_code == 200, r.read().decode("utf-8")[:200]
        events = _sse_events(r.read().decode("utf-8"))
    assert {"meta", "done"} <= set(events), f"少事件：{sorted(events)}"
    for name in ("meta", "done"):
        basis = events[name].get("evidence_basis")
        assert basis, f"{name} 没带 evidence_basis，前端就只能自己数"
        assert set(basis) == set(KEYS), f"{name} 的键与后端清单不一致：{sorted(basis)}"
        assert (basis["search_engine_returned"], basis["query_variants"],
                basis["off_topic_dropped"]) == (4, 2, 1), name
    assert events["meta"]["evidence_basis"] == events["done"]["evidence_basis"]


# ── 前端渲染面：只渲染后端给的数 ──────────────────────────────────────────
TSX = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src" / "pages" / "Companion.tsx"

WIRING = re.compile(r"if \(d\.evidence_basis\) setBasis\(d\.evidence_basis\)")
NUMBER_BRANCH = re.compile(r'typeof value === "number"')


def strip_region(text: str) -> str:
    """计数条那一段：从 data-testid 起，到消息滚动容器为止（滚动容器之外=不随消息滚走）。"""
    start = text.index('data-testid="evidence-basis"')
    stop = text.index("ref={logRef}")
    return text[start:stop]


def problems(text: str) -> list[str]:
    out: list[str] = []
    if not WIRING.search(text):
        out.append("absorb 没有把 d.evidence_basis 存进 state ⇒ 这一行没有后端来源")
    if 'data-testid="evidence-basis"' not in text:
        out.append("对话区顶部没有计数条")
        return out
    region = strip_region(text)
    for key in KEYS:
        if f"basis.{key}" not in region:
            out.append(f"计数条少了 {key} 这一格（后端给了却不显示）")
    if not NUMBER_BRANCH.search(region):
        out.append("没按 `typeof value === \"number\"` 分两态：null 会被渲染成空或 0")
    if "未记录" not in region:
        out.append("缺「未记录」文案：没这个数时不许摆个零在那儿")
    if re.search(r"\.length|parseInt|\bMath\.", region):
        bad = [ln.strip()[:60] for ln in region.splitlines()
               if re.search(r"\.length|parseInt|\bMath\.", ln)]
        out.append(f"计数条在前端自己算数（{bad}）⇒ 只许渲染 evidence_basis")
    if text.index("</header>") > text.index('data-testid="evidence-basis"'):
        out.append("计数条不在 </header> 之后（放表头里会跟着状态机那行挤）")
    return out


# 改前的形状（轮 182 那份 Companion.tsx 的相关段落）：负对照，证明判据会咬人
OLD_SHAPE = '''
        </header>

        <div ref={logRef} onScroll={onLogScroll}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 py-4"
          role="log" aria-live="polite" aria-atomic="false">
'''


def test_frontend_renders_only_what_the_backend_sent():
    found = problems(TSX.read_text(encoding="utf-8"))
    assert not found, "；".join(found)


def test_the_pre_change_shape_would_have_been_caught():
    """负对照：改前那份必须同时被"没有条"与"没接线"两条判据抓住。"""
    found = problems(OLD_SHAPE)
    assert any("absorb" in p for p in found), f"接线那条没开火：{found}"
    assert any("没有计数条" in p for p in found), f"缺条那条没开火：{found}"

def test_a_round_without_search_does_not_report_the_previous_round(isolated, monkeypatch):
    """查过一轮之后再勾掉：那五个数这一轮**没有**，不许把上一轮的读数摆在这一行上。

    轮 198 看图（`docs/screenshots/r187_declined_1280.png`）抓到的正是这条：同一屏上
    「这轮你勾掉了检索，下面的话没有文献依据」与「引擎返回 2 · 入库 2」并存。
    上面那条反问轮用例只覆盖了「新项目 extra 本来就空」，没覆盖「先查一轮、再没查」。
    """
    monkeypatch.setattr(engine, "search_for_intent", _stub_search(2, 1, 0))
    pid = dm.create_project(name="计数条本轮语义")
    first = dm.evidence_basis(dm.prepare_turn(SAY, pid))
    assert first["search_engine_returned"] == 2, "正向对照：第一轮真查过，这一格必须有数"

    def no_call(intent, limit=8):
        raise AssertionError("勾掉之后还发外呼 ⇒ 这一轮的读数不是「没查」造成的")

    monkeypatch.setattr(engine, "search_for_intent", no_call)
    declined = dm.evidence_basis(dm.prepare_turn(SAY, pid, no_search=True))
    for key in ("search_engine_returned", "search_stored", "off_topic_dropped",
                "query_variants", "search_ms"):
        assert declined[key] is None, f"{key} 这一轮没查却读成 {declined[key]!r}：那是上一轮的数"
    # 项目级那两格是真读出来的，不许被一起抹平（否则「踩在多少料上」会连库里的料一起报没）
    assert isinstance(declined["library_pdfs"], int), "库内那一格被抹成 None ⇒ 项目级的料也没了"
    assert isinstance(declined["library_abstracts"], int), "同上，摘要那一格"
    assert (declined["library_pdfs"], declined["library_abstracts"]) == \
        (first["library_pdfs"], first["library_abstracts"])
    # 形状对照：库里的 extra 此时确实还留着上一轮那两个数——判据靠的是「这一轮发没发」，
    # 不是「extra 空不空」。少这一句，把标记删掉时没人看得见。
    assert dm.load_intent(pid).extra.get("search_engine_returned") == 2

