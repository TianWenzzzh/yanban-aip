"""B3 检索意图卡（对标 undermind U3）：搜索之前把「打算怎么查」摊开、允许改。

钉四件事（§4 B3 的验收 ①②③ + 一条我自己最怕的）：
① 改槽位改的是**真检索式**：断言 `engine._query_from_intent` 的结果跟着变，不只看 UI；
② 没点「启动」之前一次外呼都不发：哨兵把 `search_for_intent` 换成计数器，预览与保存都必须是 0；
③ 检索段耗时是真读出来的：成功给整数、失败给 None（不许用 0 冒充"查过了但很快"）；
④ 卡上只许摆**后端真读得上**的槽位——挂一颗没人读的控件等于骗用户"我控制了检索"。

判据走产品入口（HTTP 端点 / prepare_turn），不直接调私有函数；`_query_from_intent` 是例外，
因为这条验收点名的就是它。
"""
from __future__ import annotations

import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"  # 与同目录其它测试一致：import 期定值

import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.conversation import dialogue_manager as dm
from companion.intent import search_plan
from companion.intent.model import ResearchIntent
from companion.literature import engine

SAY = "我们实验室在做光伏功率预测，我想找一个还没人做的点"


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


def plan_path(pid: str) -> str:
    return f"/companion/intent/plan/{pid}"


# ── ① 改槽位 = 改真检索式 ────────────────────────────────────────────────
def test_editing_the_topic_slot_changes_the_query_actually_sent(client):
    pid = dm.create_project(name="意图卡")
    before = client.get(plan_path(pid)).json()
    r = client.post(plan_path(pid), json={"topic": "photovoltaic power forecasting"})
    assert r.status_code == 200, r.text[:200]
    after = r.json()

    intent = dm.load_intent(pid)                      # 重新读库：写没写进去以此为准
    assert after["query"] == engine._query_from_intent(intent)
    assert after["query"] == "photovoltaic power forecasting"
    assert before["query"] != after["query"], "改前改后同一个检索式 ⇒ 这颗槽位是假的"
    assert after["changed"] == ["topic"]              # 没改动的不许记账
    assert client.get(plan_path(pid)).json()["topic"] == "photovoltaic power forecasting"


def test_the_edited_topic_survives_the_next_turn(isolated, monkeypatch):
    """下一轮真的用改后的主题查。

    这条防最阴的一种假：卡上写进去了，但下一轮 extractor 又从用户话里重新捕获 topic 覆盖掉，
    于是"改了范围"这件事在检索那一刻根本不存在。（extractor.py:267 只在 topic 为空时捕获——这是证据。）
    """
    seen: list[str] = []

    def spy(intent, limit=8):
        seen.append(engine._query_from_intent(intent))
        return [{"id": "p1", "title": "Photovoltaic power forecasting baseline"}]

    monkeypatch.setattr(engine, "search_for_intent", spy)
    pid = dm.create_project(name="意图卡")
    intent = dm.load_intent(pid)
    assert search_plan.apply_edits(intent, {"topic": "graph anomaly detection"}) == ["topic"]
    dm.save_intent(intent)
    dm.prepare_turn(SAY, pid)
    assert seen, "这一轮没走到检索，上面那条断言等于空转（样本没被判定为可检索轮）"
    assert seen[-1] == "graph anomaly detection", f"改后的主题没被用上：{seen[-1]!r}"


def test_the_year_slot_is_read_but_never_leaks_into_the_query(isolated):
    """year_from 必须落到引擎读得到的字段；同时它不许混进检索式（实测拼偏好词会毁掉相关性判定）。"""
    intent = dm.load_intent(dm.create_project(name="意图卡"))
    intent.topic = "photovoltaic power forecasting"
    changed = search_plan.apply_edits(intent, {"year_from": 2022, "open_access": True})
    dm.save_intent(intent)
    assert set(changed) == {"paper_preferences.year_from", "paper_preferences.open_access_preferred"}
    re_read = dm.load_intent(intent.project_id)
    assert re_read.paper_preferences.year_from == 2022
    assert engine._query_from_intent(re_read) == "photovoltaic power forecasting"
    plan = search_plan.plan_for(re_read)
    assert plan["year_from"] == 2022 and plan["open_access"] is True


def test_bad_year_is_rejected_loudly(client):
    """非法输入静默忽略 = 用户以为改了、其实没改。必须 400，且原值不动。"""
    pid = dm.create_project(name="意图卡")
    client.post(plan_path(pid), json={"year_from": 2022})
    r = client.post(plan_path(pid), json={"year_from": "近几年"})
    assert r.status_code == 400, f"非法年份返回了 {r.status_code}：静默吞掉就是假控件"
    assert client.get(plan_path(pid)).json()["year_from"] == 2022


# ── ② 预览与保存都不外呼 ─────────────────────────────────────────────────
def test_preview_and_edit_issue_zero_provider_calls(client, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(engine, "search_for_intent",
                        lambda *a, **k: calls.append("search") or [])
    pid = dm.create_project(name="意图卡")
    client.post(plan_path(pid), json={"topic": "solar power forecasting"})
    for _ in range(3):
        assert client.get(plan_path(pid)).status_code == 200
    client.post(plan_path(pid), json={"year_from": 2023, "open_access": True})
    assert calls == [], f"意图卡自己发起了 {len(calls)} 次检索：预览卡变成了偷偷查"
    state = dm.get_state(pid).value
    assert state not in ("SEARCH", "SCREEN", "FORMULATE"), f"没点启动状态机就被推进了：{state}"


# ── ③ 检索段耗时 ─────────────────────────────────────────────────────────
def test_search_ms_is_a_real_reading_and_none_when_the_search_failed(isolated, monkeypatch):
    monkeypatch.setattr(engine, "search_for_intent",
                        lambda intent, limit=8: [{"id": "p1", "title": "Photovoltaic baseline"}])
    basis = dm.evidence_basis(dm.prepare_turn(SAY, None))
    assert isinstance(basis["search_ms"], int) and basis["search_ms"] >= 0

    def boom(intent, limit=8):
        raise RuntimeError("429 too many requests")

    monkeypatch.setattr(engine, "search_for_intent", boom)
    broken = dm.evidence_basis(dm.prepare_turn(SAY, None))
    assert broken["search_ms"] is None, "检索没跑成却给出耗时 = 编的数"
    assert broken["search_engine_returned"] == 0


# ── ④ 卡上不许有没人读的槽位 ─────────────────────────────────────────────
SLOT_ATTR = {"topic": "topic", "year_from": "year_from", "open_access": "open_access_preferred"}
# 每颗槽位**指名**"哪个文件里读得到哪一段"。只写"仓里某处提到过"是假判据：rejected_directions 也在
# mentor.py 与 plugins.py 里被提到（进提示词、进导图），但那不等于"能控制检索结果"。
# 🔴 前端那一格要按**接口真正下发的字段名**认：卡上拿到的是 `open_access`，不是模型里的
#    `open_access_preferred`——拿模型字段名去 TSX 里搜会永远搜不到（这条首跑就是这么红的）。
READER_FILES = {
    "topic": (("companion/literature/engine.py", "intent.topic"),),
    "year_from": (("companion/literature/engine.py", "paper_preferences.year_from"),),
    "open_access": (("companion/conversation/mentor.py", "open_access_preferred"),
                    ("webui/src/pages/Companion.tsx", "open_access")),
}
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_every_slot_on_the_card_is_actually_read():
    """假控件判据：卡上每颗槽位都要**点名读者文件**，且那个文件里真读得到那一段。

    这就是决定不放「排除项」的原因——没有任何检索/排序通道读 rejected_directions 去筛结果，
    摆上去就是一个看起来能控制、实际什么都不控制的开关。以后加槽位先在这里登记读者。
    """
    plan = search_plan.plan_for(ResearchIntent(project_id="probe"))
    assert set(READER_FILES) <= set(plan), f"卡上少了槽位：{sorted(set(READER_FILES) - set(plan))}"
    for key, readers in READER_FILES.items():
        for rel, needle in readers:
            src = (ROOT / rel).read_text(encoding="utf-8")
            assert needle in src, f"{key} 声称由 {rel} 读，但那文件里没有 {needle}"
    extra = set(plan) - {"query"} - set(READER_FILES)
    assert not extra, f"卡上出现了没登记读者的槽位 {sorted(extra)} ⇒ 要么接上读者，要么撤掉这颗"


def test_rejected_directions_has_no_retrieval_reader_yet():
    """「排除项」没上卡，是因为 engine 真没读它。哪天 engine 读了，这条会红 ⇒ 那时该加上卡并改判据。"""
    assert "rejected_directions" not in search_plan.plan_for(ResearchIntent(project_id="probe"))
    engine_src = (ROOT / "companion" / "literature" / "engine.py").read_text(encoding="utf-8")
    assert "rejected_directions" not in engine_src, \
        "engine 已经读 rejected_directions：该把排除项加上卡，并改这条判据"


# ── 前端渲染面：检索式必须由后端给，卡片不许自己拼 ─────────────────────────
TSX = ROOT / "webui" / "src" / "pages" / "Companion.tsx"

PLAN_REGION = re.compile(r"const loadPlan = useCallback.*?const startSearch", re.S)
RUN_DISABLED = "disabled={planBusy || busy || !input.trim() || !yearValid(draft.year)}"


def problems(text: str) -> list[str]:
    out: list[str] = []
    if 'data-testid="search-plan"' not in text:
        return ["输入框上方没有检索意图卡"]
    region = PLAN_REGION.search(text)
    if not region:
        return ["loadPlan/savePlan 的形状变了：判据取不到区域就没法核对卡的行为"]
    body = region.group(0)
    if "/intent/plan/${pid}" not in body:
        out.append("卡没有从 GET /intent/plan 取预览 ⇒ 检索式没有后端来源")
    if "/intent/plan/${projectId}" not in body:
        out.append("改完的槽位没往 POST /intent/plan 写回 ⇒ 改了等于没改")
    if "chat/stream" in body:
        out.append("预览/保存这段里出现了 chat/stream ⇒ 打开卡就在偷偷检索，这条必须守住")
    if re.search(r"query\s*:", body):
        bad = [ln.strip()[:70] for ln in body.splitlines() if re.search(r"query\s*:", ln)]
        out.append(f"卡在自己拼检索式（{bad}）⇒ 检索式只能由后端派生")
    if "不改检索式" not in text:
        out.append("开放获取那颗没写明「不改检索式」⇒ 会被读成它能改检索范围")
    at = text.find('data-testid="plan-run"')
    if at < 0:
        out.append("没有「按这个范围检索」这颗启动按钮")
    elif RUN_DISABLED not in text[at:at + 220]:
        out.append(f"启动按钮没按 `{RUN_DISABLED}` 挡住 ⇒ 空发送或连点会静默失灵")
    if "basis.search_ms" not in text:
        out.append("检索耗时没进计数条（§4 B3 验收③要求它显示在 B1 那条旁边）")
    return out


def test_the_card_renders_the_backends_query_and_saves_edits():
    found = problems(TSX.read_text(encoding="utf-8"))
    assert not found, "；".join(found)


def test_the_pre_change_shape_would_have_been_caught():
    """负对照：没有这张卡的那一份、撒谎的文案、以及把耗时写成常量，三样都必须被抓。"""
    live = TSX.read_text(encoding="utf-8")
    assert any("没有检索意图卡" in p for p in
               problems(live.replace('data-testid="search-plan"', 'data-testid="gone"')))
    assert any("开放获取" in p for p in problems(live.replace("不改检索式", "会改检索式")))
    assert any("检索耗时" in p for p in problems(live.replace("basis.search_ms", "7660")))
    assert any("启动按钮" in p for p in problems(live.replace(RUN_DISABLED, "disabled={false}")))
