"""承诺台账（跨轮行动清单跟进）的回归测试，全部在 LLM_MOCK 下、零真实调用。

这一层要守住的不是"功能存在"，而是三件容易回退的事：
① 清单必须真的在下一轮进到提示词里（接线，不是自证）；
② 状态只由**明确的**学生线索或界面按钮改，含糊句一律不改（台账写假 = 下一轮基于假事实推进）；
③ 台账只有一个出处（events 表），不许出现第二份抄本。
"""
import os

os.environ["LLM_MOCK"] = "1"

import pytest

import companion.db as db_module
from companion.conversation import commitments, dialogue_manager, mentor
from companion.literature import engine
from companion.intent.model import ResearchIntent


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module._CONN = None
    yield tmp_path / "test.db"
    db_module._CONN = None


PLAN = {"actions": [{"do": "跑通 XGBoost 基线", "with": "12k 条 URL", "dose": "2 周",
                     "check": "同一口径下准确率到 90%"},
                    {"do": "画方法谱系图", "with": "近 2 年 5 篇综述", "dose": "3 天",
                     "check": "图上每条线都有代表方法"},
                    {"do": "定投稿目标", "with": "导师意见", "dose": "1 周内",
                     "check": "选定 1 个会议并核对截稿日"}],
        "milestones": ["第 1 段：两个月出基线"], "artifact": "一张基线对比表",
        "pitfall": "别一上来就换数据集", "question": ""}

# 一句同时过得了全部硬闸门的合规回复（与 test_dialogue_llm 用的同一条）
GOOD_REPLY = ("建议你先精读 5 篇近两年的综述，把方法谱系画成一张图。可以先拿 12k 条 URL 跑一遍 "
              "XGBoost 基线，两周能看到 90% 左右的准确率。第三步用 3 张公开数据集各跑 1 次对照，"
              "1 周内出同一口径的对比表。你导师更看重期刊还是会议？")


def _record(pid="p1", plan=PLAN):
    return commitments.record(pid, plan)


# ── 写入侧 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("empty", [None, {}, {"actions": []}, {"actions": [{"do": ""}]},
                                   {"actions": [{"check": "只有判据没有动作"}]}])
def test_record_skips_plans_without_an_action(empty, isolated_db):
    assert commitments.record("p1", empty) is None
    assert commitments.latest("p1") is None


def test_record_does_not_repeat_the_identical_list(isolated_db):
    """同一份清单重复入库会把「待跟进」顶成一个假的累积量。"""
    first = _record()
    assert first and _record() is None
    assert commitments.latest("p1")["id"] == first


def test_record_caps_number_of_items(isolated_db):
    big = {"actions": [{"do": f"第 {i} 件事", "check": f"判据 {i}"} for i in range(9)]}
    _record("p1", big)
    assert len(commitments.latest("p1")["items"]) == commitments.MAX_ITEMS


def test_latest_keeps_artifact_across_a_status_write(isolated_db):
    """改状态是整份 detail_json 覆写，漏带 artifact 就会把交付物静默抹掉。"""
    _record()
    commitments.set_status("p1", 0, "done")
    led = commitments.latest("p1")
    assert led["artifact"] == "一张基线对比表"
    assert led["items"][0]["status"] == "done" and led["items"][1]["status"] == "open"


# ── 状态：显式与推断都要保守 ──────────────────────────────────────────
def test_set_status_rejects_unknown_status_and_missing_ledger(isolated_db):
    with pytest.raises(ValueError):
        commitments.set_status("p1", 0, "finished")
    _record()
    assert commitments.set_status("p1", 9, "done") is None      # 越界下标
    assert commitments.set_status("p0", 0, "done") is None      # 根本没有台账


def test_report_done_cue_moves_only_the_matching_item(isolated_db):
    _record()
    changed = commitments.apply_student_report("p1", "我按你说的把 XGBoost 基线跑完了")
    assert [c["index"] for c in changed] == [0]
    items = commitments.latest("p1")["items"]
    assert items[0]["status"] == "done" and items[1]["status"] == "open"


def test_report_without_discriminator_changes_nothing(isolated_db):
    """"做完了"不含任何一条的判别词时不许猜——猜错就是台账里的一条假事实。"""
    _record()
    assert commitments.apply_student_report("p1", "昨天那篇论文做完了笔记") == []
    led = commitments.latest("p1")
    assert commitments.open_count(led) == len(PLAN["actions"])
    assert all(i["status"] == "open" for i in led["items"])


def test_report_with_both_cues_changes_nothing(isolated_db):
    _record()
    assert commitments.apply_student_report("p1", "基线跑通了但还是卡住在数据上") == []


def test_global_cue_closes_every_open_item(isolated_db):
    _record()
    changed = commitments.apply_student_report("p1", "上次那几条都做完了")
    assert len(changed) == 3
    assert commitments.open_count(commitments.latest("p1")) == 0


def test_global_blocked_cue_marks_every_open_item(isolated_db):
    _record()
    changed = commitments.apply_student_report("p1", "上次的清单都没来得及做")
    assert {c["status"] for c in changed} == {"blocked"}


@pytest.mark.parametrize("sentence", [
    "上次交代那三条，我只跑通了一条，另外两条没时间弄",
    "上次的清单我没全做完，就跑通了一条",
    "那几条里基线跑通了，谱系图还卡在数据上",
    "上次说的我只做了一件，剩下的来不及",
])
def test_limited_or_negated_report_never_closes_the_whole_ledger(isolated_db, sentence):
    """独立评审实测出的 P0：限量/否定一出现，"整张结掉"就不许下结论。

    旧写法把「上次交代那三条，我只跑通了一条，另外两条没时间弄」记成 done 3 / open 0，
    下一轮就对他说"已回报完成 3 项"——这是本函数注释里承诺要挡的那类假记忆。
    """
    _record()
    commitments.apply_student_report("p1", sentence)
    led = commitments.latest("p1")
    done = sum(1 for i in led["items"] if i["status"] == "done")
    assert done <= 1, f"一句话结掉了 {done} 条：{sentence}"
    assert commitments.open_count(led) >= 2


def test_plain_global_report_still_closes_everything(isolated_db):
    """反向：没有限量词的明确回报必须照旧整表结掉（守卫不许把功能一起废掉）。"""
    _record()
    changed = commitments.apply_student_report("p1", "上次那三条我都做完了")
    assert len(changed) == 3 and commitments.open_count(commitments.latest("p1")) == 0


# ── 读取侧：进提示词的块 ─────────────────────────────────────────────
def test_followup_block_absent_when_nothing_open(isolated_db):
    assert commitments.followup_block("p1") == ""
    _record()
    commitments.apply_student_report("p1", "上次那几条都做完了")
    assert commitments.followup_block("p1") == ""


def test_followup_block_lists_open_items_and_refuses_to_declare_done(isolated_db):
    _record()
    commitments.set_status("p1", 2, "dropped")
    block = commitments.followup_block("p1")
    assert "行动清单" in block and "跑通 XGBoost 基线" in block and "同一口径下准确率到 90%" in block
    assert "定投稿目标" not in block                       # 已结掉的那条不再出现
    assert "不要替学生宣布某项已经完成" in block            # 诚实性约束随块一起进提示词
    assert "小时前" in block or "天前" in block or "刚才" in block


def test_followup_block_declares_what_it_left_out(isolated_db):
    """截断必须说出来，且说的两个数与**同一份清单**同源：模型看不见第 4 项却来一句
    "上次那几件事都做完了吗"，就把没列的那项当成不存在（界面上学生看得见全表）。"""
    assert commitments.MAX_ITEMS >= 4 and commitments.MAX_INJECT < commitments.MAX_ITEMS, \
        "前提变了（上限不再低于条数上限），这条用例就不再测截断了"
    four = {"actions": [{"do": f"第 {i} 件事", "check": f"判据 {i}"} for i in range(1, 5)]}
    _record("p1", four)
    block = commitments.followup_block("p1")
    led = commitments.latest("p1")
    assert f"未结的一共 {commitments.open_count(led)} 项" in block
    assert f"只列出前 {commitments.MAX_INJECT} 项" in block
    assert "不许把列出的这几条说成全部" in block
    assert block.count(". 第 ") == commitments.MAX_INJECT          # 只列前 MAX_INJECT 条
    _record("p2", PLAN)                                            # PLAN 恰好 3 条，不截断
    assert "未结的一共" not in commitments.followup_block("p2"), "没截断也报未列出 = 新的一句假话"


def test_truncation_note_stays_consistent_with_the_tally(isolated_db, monkeypatch):
    """独立评审量到的形状：把动作上限抬到 8、其中几条已结掉，"另有 N 项未列出"与头部
    tally（按整张表数 done/blocked）就会对不上账。现在两个数都从同一份 opens 派生，
    这条用例钉的是**同源**，不是某个具体数字——抬上限也不会再打架。"""
    monkeypatch.setattr(commitments, "MAX_ITEMS", 8)
    eight = {"actions": [{"do": f"任务 {i}", "check": f"判据 {i}"} for i in range(1, 9)]}
    _record("p1", eight)
    for i in (0, 1, 2):
        commitments.set_status("p1", i, "done")
    commitments.set_status("p1", 3, "blocked")
    led = commitments.latest("p1")
    block = commitments.followup_block("p1")
    opens = commitments.open_count(led)
    assert opens == 4 and block.count(". 任务 ") == commitments.MAX_INJECT
    assert f"未结的一共 {opens} 项" in block
    assert "已回报完成 3 项" in block
    # 列出条数 + 未列出条数 == 未结总数：一句话里三个数必须自己算得回来
    assert opens - commitments.MAX_INJECT == 1


def test_stats_shape_for_the_ui(isolated_db):
    assert commitments.stats("p1") is None
    _record()
    st = commitments.stats("p1")
    assert st["open"] == 3 and st["total"] == 3 and len(st["items"]) == 3
    assert {"do", "check", "status"} <= set(st["items"][0])


# ── mentor 的两个上下文块都接上 ───────────────────────────────────────
def test_reply_and_plan_prompts_both_carry_the_block():
    it = ResearchIntent(project_id="p1", topic="钓鱼检测")
    for fn, name in ((mentor.build_prompt, "正文"), (mentor.plan_prompt, "规划")):
        out = fn(user_text="我时间不多了", intent=it, followup_ctx="【你 3 天前交代的行动清单】\n1. 跑通基线")
        assert "行动清单" in out, f"{name}提示词没带上跟进块"
        assert fn(user_text="随便", intent=it) .count("行动清单") == 0


def test_note_plan_has_exactly_one_recording_helper_and_two_callers():
    """台账只有 note_plan 一个入口，且两条生成路径（ask / ask_stream）都真的调它。"""
    import inspect
    src = inspect.getsource(mentor)
    assert src.count("note_plan(intent, plan)") == 2
    assert src.count("def note_plan(") == 1
    # 生产代码里 record 的调用点也只能有一处，否则两份台账迟早各说各话
    prod = (db_module.Path(inspect.getsourcefile(mentor)).parent / "commitments.py").read_text(encoding="utf-8")
    assert prod.count("def record(") == 1
    assert src.count("commitments.record(") == 1


# ── 端到端：第二轮真的看得见第一轮交代的事 ───────────────────────────
def _two_turn_prompts(monkeypatch, first_text, second_text):
    prompts: list[str] = []

    async def fake_chat(prompt, **kw):
        prompts.append(prompt)
        return GOOD_REPLY

    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "make_plan", lambda **kw: PLAN)
    monkeypatch.setattr(engine, "search_for_intent", lambda *a, **k: [])
    pid = dialogue_manager.create_project(name="测试")
    dialogue_manager.handle_message(first_text, pid)
    dialogue_manager.handle_message(second_text, pid)
    return pid, prompts


def test_ledger_injection_is_off_by_default_but_wired(monkeypatch, isolated_db):
    """g181 把"端清单进提示词"这一腿判成不留下（收益未证明、S6 护栏开火 B 23>A 16），
    所以默认关。但**接线必须留着**：开关一抬就该真的进去——否则下次想开的时候，
    这条路径又变成"只在测试里生效"的旧毛病（轮 166 那个形状）。"""
    assert dialogue_manager.INJECT_FOLLOWUP is False, "默认值变了要连 g181 的判读一起改，不许静默抬"
    pid, prompts = _two_turn_prompts(monkeypatch, "我想做钓鱼网站检测，只剩三个月", "基线我还没开始跑")
    assert len(prompts) >= 2
    assert all("行动清单" not in p for p in prompts), "默认关着却还在注入"
    assert commitments.open_count(commitments.latest(pid)) == 3      # 台账照记，没被关掉


def test_turning_the_switch_on_reaches_the_prompt(monkeypatch, isolated_db):
    monkeypatch.setattr(dialogue_manager, "INJECT_FOLLOWUP", True)
    pid, prompts = _two_turn_prompts(monkeypatch, "我想做钓鱼网站检测，只剩三个月", "基线我还没开始跑")
    assert "行动清单" not in prompts[0]                    # 第一轮没有台账可提
    assert "行动清单" in prompts[-1] and "跑通 XGBoost 基线" in prompts[-1]
    assert commitments.open_count(commitments.latest(pid)) == 3


def test_gate_stays_armed_with_injection_off(monkeypatch, isolated_db):
    """关掉注入不许顺手关掉硬闸门：`open_items` 仍要传到 `violations`，
    否则"没端清单"就等于"看不见冒领"——那正是 g176 量出来的那个伤害会回来的方式。"""
    _record()
    intent = ResearchIntent(project_id="p1")
    turn = {"project_id": "p1", "intent": intent, "history": [], "papers": [], "gaps": [],
            "memory_items": [], "changed": [], "state": "DELIVER", "still_clarifying": False}
    kw = dialogue_manager._mentor_kwargs(turn, "随便聊点别的")
    assert kw["followup_ctx"] == "" and len(kw["open_items"]) == 3
    from companion.conversation import mentor as _m
    v = _m.violations("看你已经把 XGBoost 基线做完了，接下来把谱系图画一下。",
                      papers=[], history=[], user_text="随便聊点别的",
                      open_items=kw["open_items"])
    assert v["false_completion"] is True, "闸门跟着注入一起被关掉了"


def test_student_report_of_done_then_ledger_goes_quiet(monkeypatch, isolated_db):
    prompts: list[str] = []

    async def fake_chat(prompt, **kw):
        prompts.append(prompt)
        return GOOD_REPLY

    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "make_plan", lambda **kw: PLAN)
    monkeypatch.setattr(engine, "search_for_intent", lambda *a, **k: [])
    pid = dialogue_manager.create_project(name="测试")
    dialogue_manager.handle_message("我想做钓鱼网站检测，只剩三个月", pid)
    dialogue_manager.handle_message("上次那几条都做完了", pid)
    out = dialogue_manager.handle_message("接下来该写哪一节", pid)
    assert commitments.open_count(commitments.latest(pid)) == 0
    assert "行动清单" not in prompts[-1]                   # 结掉了就别再念
    assert out["commitments"]["open"] == 0                 # 界面读到的状态与库里一致


def test_finalize_and_meta_both_expose_the_ledger(monkeypatch, isolated_db):
    async def fake_chat(prompt, **kw):
        return GOOD_REPLY

    monkeypatch.setattr(mentor, "chat", fake_chat)
    monkeypatch.setattr(mentor, "make_plan", lambda **kw: PLAN)
    monkeypatch.setattr(engine, "search_for_intent", lambda *a, **k: [])
    pid = dialogue_manager.create_project(name="测试")
    out = dialogue_manager.handle_message("我想做钓鱼网站检测，只剩三个月", pid)
    assert out["commitments"]["open"] == 3 and out["commitments"]["total"] == 3
    turn = dialogue_manager.prepare_turn("再说说投稿的事", pid)
    assert dialogue_manager.meta_event(turn)["commitments"]["open"] == 3


# ── 台账里除 items 外的每个字段都必须在"整份覆写"后活着 ─────────────────
def test_record_stores_the_situation_verbatim(isolated_db):
    with_situation = dict(PLAN, situation="三个月内开题，只有 12k 条 URL，DL 只学过一点")
    _record("p1", with_situation)
    st = commitments.stats("p1")
    assert st["situation"] == "三个月内开题，只有 12k 条 URL，DL 只学过一点"


@pytest.mark.parametrize("field", ("artifact", "situation"))
def test_status_write_keeps_every_declared_ledger_field(isolated_db, field):
    """改状态是把 detail_json 整份重写：漏带任何一个非 items 字段就会静默丢数据。

    🔴 参数表用**字面清单**而不是 `commitments.LEDGER_FIELDS`：首版拿注册表当参数，
    植入"从清单里删掉 situation"时 pytest 只是少收一条用例、照旧全绿（体检当场抓到）。
    """
    assert field in commitments.LEDGER_FIELDS, f"{field} 没登记进 LEDGER_FIELDS，覆写就会丢它"
    plan = dict(PLAN, situation="三个月内开题")
    _record("p1", plan)
    before = commitments.stats("p1")[field]
    assert before
    commitments.set_status("p1", 0, "done")
    assert commitments.stats("p1")[field] == before
    commitments.apply_student_report("p1", "上次那几条都做完了")
    assert commitments.stats("p1")[field] == before


def test_ledger_registry_matches_the_stored_row(isolated_db):
    """detail_json 里除 items 外的键，必须**恰好**等于 LEDGER_FIELDS——两个方向都要对账。

    少一个：写了但没登记 ⇒ 下次覆写就丢；多一个：登记了但没写 ⇒ 覆写时凭空造一个空字段。
    """
    import json as _json
    _record("p1", dict(PLAN, situation="三个月内开题"))
    led = commitments.latest("p1")
    row = db_module.get_conn().execute("SELECT detail_json FROM events WHERE id=?",
                                       (led["id"],)).fetchone()
    keys = {k for k in _json.loads(row["detail_json"]) if k != "items"}
    assert keys == set(commitments.LEDGER_FIELDS)
    for field in commitments.LEDGER_FIELDS:
        assert field in led and field in commitments.stats("p1")


def test_one_student_sentence_closes_at_most_one_item(isolated_db):
    """一句话最多结掉一条：`_mentor_kwargs` 一轮里会被调两次（正文 + repair 重采样），
    不缓存的话"XGBoost 基线和谱系图跑通了"会把两条都结掉——把学生的一句话当成两句用。"""
    from companion.conversation import dialogue_manager
    _record()
    intent = ResearchIntent(project_id="p1")
    turn = {"project_id": "p1", "intent": intent, "history": [], "papers": [], "gaps": [],
            "memory_items": [], "changed": [], "state": "DELIVER", "still_clarifying": False}
    text = "XGBoost 基线和谱系图跑通了"
    dialogue_manager._mentor_kwargs(turn, text)
    assert commitments.open_count(commitments.latest("p1")) == 2
    dialogue_manager._mentor_kwargs(turn, text)          # 同一轮的第二次装配
    assert commitments.open_count(commitments.latest("p1")) == 2, "同一句话结掉了第二条 ⇒ 缓存没生效"
    assert turn["followup_updates"] == [{"index": 0, "status": "done"}]


def test_note_assumption_records_the_click_and_leaves_the_ledger_alone(isolated_db):
    _record()
    eid = commitments.note_assumption("p1", False, "其实我是本科生，没有 GPU")
    assert eid > 0
    row = db_module.get_conn().execute(
        "SELECT kind, summary, detail_json FROM events WHERE id=?", (eid,)).fetchone()
    assert row["kind"] == "assumption_feedback" and "纠正" in row["summary"]
    import json as _json
    assert _json.loads(row["detail_json"])["agree"] is False
    led = commitments.stats("p1")
    assert led["open"] == 3                      # 点「不对」不等于某件事做完了
