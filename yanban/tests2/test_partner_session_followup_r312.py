# -*- coding: utf-8 -*-
"""轮 312 第十段 —— 队长 §5 用例 09：跨轮的「改年份 / 续找 / 总结上一批」要接得上上一轮。

机制（`literature_partner.py` 的会话表）判的是四件事，每件各一条正向：
  S0 不带会话时行为逐字等于轮 311（篇数默认 10、认不出主题就反问）；
  S1 同 sessionId、换了 taskId、这句里没有新主题但点明了年份窗 ⇒ 沿用上轮主题 + **新**年份窗 + 篇数不加量；
  S2 「再多给 3 篇」⇒ 沿用主题，篇数＝上一轮 + 3，并如实报"相对上一批新增几条、重复几条"；
  S3 「总结一下上一批」⇒ **不再发检索**，只复述已发出去那份清单（条数／年份跨度／来源分布／条目）；
  S4 换一个 sessionId ⇒ 不串用别人的上一轮；换主题 ⇒ 显式断开，第三轮接的是新主题。
S5/S6 钉住「复述靠的是 `sources._fmt` 真发出来的行形状」这条耦合：桩的返回值由 `_fmt` 生成，
   所以 `_fmt` 一改排版，这里就会数不到条目而红——手写样串就看不见这件事。
S7 是「重启后不假装记得」：清掉会话表之后，同一 sessionId 的追加句要说**没有记录**，不许编主题。
S8 是我自己引入的新风险：继承说明那句会被外部网关原样并回下一轮，必须被回声分层摘干净。
I1–I3 是植入，逐条把机制改坏，要求对应正向判据必须红（闸门不许装饰）。

🔴 全程零网络、零模型调用：`lp._search` 打桩，`_glm_review` 不进这几条路径。
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import re
import sys
import time
import uuid

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sources                                               # noqa: E402
from acps_sdk.aip import TextDataItem                        # noqa: E402
from acps_sdk.aip.aip_base_model import TaskCommand          # noqa: E402
from companion.intent.extractor import year_range as _yr     # noqa: E402


def _load_lp():
    spec = importlib.util.spec_from_file_location("lp_under_test_r312sess", str(ROOT / "literature_partner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_lp()

TOPIC_A = "钓鱼网站检测"
TOPIC_B = "多智能体协作"


@pytest.fixture(autouse=True)
def _clean_sessions():
    """会话表是进程内的：每例前后都清干净，否则用例之间会互相「记得」。"""
    lp._sessions.clear()
    yield
    lp._sessions.clear()


def _cmd(text: str, task_id: str | None = None, session_id: str | None = None) -> TaskCommand:
    kw = {"type": "task-command", "id": "c-" + uuid.uuid4().hex[:8],
          "sentAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "senderRole": "leader", "senderId": "1.2.156.3088.1.1.LDR001.LDR001.1.0002",
          "command": "start", "taskId": task_id or ("t-" + uuid.uuid4().hex[:8]),
          "dataItems": [TextDataItem(text=text)]}
    if session_id is not None:
        kw["sessionId"] = session_id
    return TaskCommand(**kw)


def _list_md(titles, years, venue="Crossref", doi_prefix="10.1000"):
    """造一份**由产品自己的排版函数**产出的清单（不是手写样串，理由见模块 docstring 的 S5/S6）。"""
    papers = [{"title": t, "authors": ["A. Author", "B. Bee"], "year": y, "venue": venue,
               "doi": f"{doi_prefix}.{i}", "citations": 7} for i, (t, y) in enumerate(zip(titles, years), 1)]
    return "## 检索结果\n" + "\n".join(sources._fmt(papers, venue))


def _search_stub(monkeypatch, md_by_call):
    """桩：记下每一发的 (topic, limit, year_range)，按调用次序给回清单文本。"""
    calls: list[tuple] = []

    async def fake(topic: str, limit: int, year_range=None) -> str:
        calls.append((topic, limit, year_range))
        md = md_by_call[min(len(calls) - 1, len(md_by_call) - 1)]
        return md if isinstance(md, str) else md(topic)

    monkeypatch.setattr(lp, "_search", fake)
    return calls


def _text_of(result) -> str:
    items = (result.products or [])[0].dataItems if result.products else result.status.dataItems
    return items[0].text


# ── S0：没有会话记录时，行为逐字等于轮 311 ─────────────────────────────────────
def test_S0_first_turn_keeps_the_r311_shape(monkeypatch):
    calls = _search_stub(monkeypatch, [_list_md(["Paper One"], [2021])])
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献", session_id="s-r312-s0"), None))
    assert calls == [(TOPIC_A, 10, None)], f"没写篇数就该是轮 311 那个 10：{calls}"
    calls.clear()
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 4 篇", session_id="s-r312-s0b"), None))
    assert calls == [(TOPIC_A, 4, None)], calls


def test_S0b_no_topic_and_no_session_still_asks_for_topic(monkeypatch):
    calls = _search_stub(monkeypatch, [_list_md(["Paper One"], [2021])])
    got = asyncio.run(lp.on_start(_cmd("你现在能正常工作吗？不要检索。", session_id=None), None))
    assert not calls, f"认不出主题却去检索了：{calls}"
    assert got.status.state == "awaiting-input"
    assert "请补充研究主题" in got.status.dataItems[0].text


# ── S1：跨 taskId 的同会话改年份 ───────────────────────────────────────────────
def test_S1_year_only_followup_inherits_topic_with_new_window(monkeypatch):
    two = _list_md(["Phishing site detection", "URL ranking"], [2019, 2024])
    calls = _search_stub(monkeypatch, [two, two])
    sid = "s-r312-s1"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-s1-a", session_id=sid), None))
    assert calls[-1] == (TOPIC_A, 5, None)
    got = asyncio.run(lp.on_start(_cmd("改成近三年的", task_id="t-s1-b", session_id=sid), None))

    topic, limit, yr = calls[-1]
    assert len(calls) == 2, f"追加句没有触发第二发检索：{calls}"
    assert topic == TOPIC_A, f"主题没继承：{topic}"
    assert yr == _yr("近三年") and yr is not None, f"年份窗没按这句走：{yr}"
    assert limit == 5, f"只换年份不许顺手加量：{limit}"
    text = _text_of(got)
    assert "沿用上一轮主题" in text and "这一句里没有新主题" in text, text
    assert TOPIC_A in text


# ── S2：续找要加量，并如实报新增/重复 ─────────────────────────────────────────
def test_S2_more_followup_grows_limit_and_reports_overlap(monkeypatch):
    first = _list_md(["Alpha", "Beta", "Gamma"], [2020, 2021, 2022])
    second = _list_md(["Alpha", "Beta", "Delta", "Epsilon"], [2020, 2021, 2023, 2024])
    calls = _search_stub(monkeypatch, [first, second])
    sid = "s-r312-s2"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 3 篇", task_id="t-s2-a", session_id=sid), None))
    got = asyncio.run(lp.on_start(_cmd("再多给 4 篇", task_id="t-s2-b", session_id=sid), None))

    assert calls[-1][0] == TOPIC_A and calls[-1][1] == 7, f"续找篇数＝上一轮+点明数：{calls[-1]}"
    text = _text_of(got)
    assert "相对上一批：本轮 4 条，新增 2 条，重复 2 条" in text, text


# ── S3：总结上一批＝复述，不再发检索 ──────────────────────────────────────────
def test_S3_summarize_restates_the_delivered_list_without_a_new_search(monkeypatch):
    md = _list_md(["Alpha paper", "Beta paper", "Gamma paper"], [2019, 2023, 2023])
    calls = _search_stub(monkeypatch, [md])
    sid = "s-r312-s3"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 3 篇", task_id="t-s3-a", session_id=sid), None))
    assert len(calls) == 1
    got = asyncio.run(lp.on_start(_cmd("总结一下上一批", task_id="t-s3-b", session_id=sid), None))

    assert len(calls) == 1, f"复述这一发不该再检索：{calls}"
    text = _text_of(got)
    assert "本轮没有再发检索" in text, text
    assert "共 3 条" in text, text
    for t in ("Alpha paper", "Beta paper", "Gamma paper"):
        assert t in text, f"条目 {t} 没被复述出来：{text}"
    assert "2019—2023，2 个不同年份" in text, text
    assert "Crossref 3 条" in text, text
    assert TOPIC_A in text, "复述要说清这是哪一主题的上一批"


def test_S4_other_session_does_not_steal_the_previous_turn(monkeypatch):
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md])
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-s4-a", session_id="s-r312-A"), None))
    got = asyncio.run(lp.on_start(_cmd("改成近三年的", task_id="t-s4-b", session_id="s-r312-B"), None))

    assert len(calls) == 1, f"别的会话的追加句去查了：{calls}"
    text = got.status.dataItems[0].text
    assert "没有上一轮记录" in text, text
    assert TOPIC_A not in text, f"把 A 会话的主题递给了 B 会话：{text}"


def test_S4b_topic_switch_breaks_inheritance_then_follows_the_new_one(monkeypatch):
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md, md])
    sid = "s-r312-s4b"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-1", session_id=sid), None))
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_B}的文献 2 篇", task_id="t-2", session_id=sid), None))
    assert calls[1] == (TOPIC_B, 2, None), f"换题后不许把上一轮的窗/量带过来：{calls[1]}"
    asyncio.run(lp.on_start(_cmd("再多给 3 篇", task_id="t-3", session_id=sid), None))
    assert calls[2] == (TOPIC_B, 5, None), f"第三轮该接新主题而不是最初那个：{calls[2]}"


# ── S5/S6：复述靠的是 `_fmt` 真发出来的形状 ────────────────────────────────────
def test_S5_digest_reads_the_shipped_entry_shape(monkeypatch):
    """正向对照：桩给的是 `_fmt` 真产出的文本，摘取尺必须逐条数得出来。"""
    md = _list_md(["Alpha", "Beta", "Gamma"], [2019, 2024, 2024])
    assert lp._titles_of(md) == ["Alpha", "Beta", "Gamma"], "条目行形状与 `_fmt` 不再一致"
    details = lp._ENTRY_DETAIL.findall(md)
    assert [d[0] for d in details] == ["2019", "2024", "2024"], details
    assert all(d[2].strip() == "Crossref" for d in details), details


def test_S6_empty_list_is_reported_as_empty_not_invented():
    """上一发是综述提纲（没有清单）时，复述那支不许报出「共 0 条」假装做了事。"""
    prior = {"t": time.monotonic(), "topic": TOPIC_A, "year": None, "limit": 5,
             "skill": "yanban.literature.review-framework", "list_md": "", "titles": []}
    text = lp._summarize(prior)
    assert "没有留下可复述的条目清单" in text, text
    assert "共 0 条" not in text, text


# ── S7：重启后不假装记得 ───────────────────────────────────────────────────────
def test_S7_after_the_store_is_gone_it_asks_instead_of_pretending(monkeypatch):
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md])
    sid = "s-r312-s7"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-s7-a", session_id=sid), None))
    lp._sessions.clear()                        # ＝伙伴进程重启／重部署
    got = asyncio.run(lp.on_start(_cmd("总结一下上一批", task_id="t-s7-b", session_id=sid), None))

    assert len(calls) == 1, f"记录没了还去检索＝把追问洗成了动作：{calls}"
    text = got.status.dataItems[0].text
    assert "没有上一轮记录" in text and "请补充研究主题" in text, text
    assert TOPIC_A not in text, f"没有记录却把主题递回去了：{text}"


def test_S8_inheritance_head_is_stripped_as_echo_on_the_next_turn():
    """我这句话会被网关原样并回下一轮 ⇒ 回声分层必须整句摘掉，句里的主题不许当新主题竞争。"""
    head = lp._inherited_head({"topic": TOPIC_A, "why": "你这轮点明了新的年份窗"})
    assert lp._user_span(head).strip() == "", repr(lp._user_span(head))
    assert lp._topic_candidates(lp._user_span(head)) == []
    note = lp._fresh_note(_list_md(["Alpha", "Beta"], [2020, 2021]),
                          {"topic": TOPIC_A, "titles": ["Alpha"]})
    assert lp._user_span(note).strip() == "", repr(lp._user_span(note))
    # 正向对照：同一把尺对用户自己的话不动手，否则上面三条是恒真
    user = f"检索{TOPIC_A}的文献 5 篇"
    assert TOPIC_A in lp._user_span(user)


# ── S9：追加句的动词残片 ──────────────────────────────────────────────────────
def test_S9_condition_change_verbs_never_become_a_topic():
    """追加句里的动词残片不许被当主题——这是会话继承的前提（轮 312 探针
    `输出/_logs/r312_p276_probe_followup_topic.log`：改前 `改成近三年的` 吐出「改成」、
    `再多给 4 篇` 吐出「再多给」、`继续找` 吐出「继续」，于是永远轮不到继承那条路）。
    🔴 没有会话记录时也一样要成立：那种残片拿去检索＝拿动词去查文献，是独立的产品缺陷。"""
    for s in ("改成近三年的", "换成近三年的", "再多给 4 篇", "总结一下上一批", "继续找", "改一下年份"):
        assert asyncio.run(lp._extract_topic(s)) == "", f"{ascii(s)} 还吐出残片"
    # 正向对照：同一把尺对真主题不动手，否则上面六条是恒真
    assert asyncio.run(lp._extract_topic(f"检索{TOPIC_A}的文献 5 篇")) == TOPIC_A
    assert asyncio.run(lp._extract_topic(f"改成近三年的{TOPIC_B}")) == TOPIC_B


# ── I1–I3：植入，证明上面那些绿不是运气 ────────────────────────────────────────
def test_I1_if_the_lookup_returns_nothing_S1_must_not_inherit(monkeypatch):
    """把「取上一轮记录」改成恒空 ⇒ S1 那两句必须退化成反问，而不是偷偷继续。"""
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md])
    monkeypatch.setattr(lp, "_session_peek", lambda sid: None)
    sid = "s-r312-i1"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-i1-a", session_id=sid), None))
    got = asyncio.run(lp.on_start(_cmd("改成近三年的", task_id="t-i1-b", session_id=sid), None))
    assert len(calls) == 1, f"摘掉记录这一支还跑了第二发：{calls}"
    assert "没有上一轮记录" in got.status.dataItems[0].text


def test_I2_if_summarize_pattern_is_loose_a_year_turn_gets_no_search(monkeypatch):
    """判据放宽成恒真 ⇒ 「改成近三年的」会被当成复述，检索不发——正向 S1 的红就是这条的绿。"""
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md])
    monkeypatch.setattr(lp, "_SUMMARIZE_R", re.compile(".*"))
    sid = "s-r312-i2"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-i2-a", session_id=sid), None))
    got = asyncio.run(lp.on_start(_cmd("改成近三年的", task_id="t-i2-b", session_id=sid), None))
    assert len(calls) == 1, f"复述判据恒真却还是查了：{calls}"
    assert "本轮没有再发检索" in _text_of(got)


def test_I3_keying_by_task_instead_of_session_breaks_S1(monkeypatch):
    """把键从 sessionId 换成 taskId ⇒ 换发 taskId 的追加句立刻接不上（这正是选 sessionId 的理由）。"""
    md = _list_md(["Alpha", "Beta"], [2020, 2021])
    calls = _search_stub(monkeypatch, [md, md])
    real_store = lp._session_store

    def by_task(sid, rec):
        real_store("t-" + uuid.uuid4().hex[:8], rec)      # 每发都换一个键

    monkeypatch.setattr(lp, "_session_store", by_task)
    sid = "s-r312-i3"
    asyncio.run(lp.on_start(_cmd(f"检索{TOPIC_A}的文献 5 篇", task_id="t-i3-a", session_id=sid), None))
    got = asyncio.run(lp.on_start(_cmd("改成近三年的", task_id="t-i3-b", session_id=sid), None))
    assert len(calls) == 1 and "没有上一轮记录" in got.status.dataItems[0].text
