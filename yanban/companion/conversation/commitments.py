"""承诺台账：把上一轮交代给学生的行动清单持久化，下一轮据此跟进。

为什么单开这一层：规划阶段（mentor.make_plan / plan_prefix）产出的 actions 每条都带
「做什么 / 用什么 / 多大量 / 完成判据」，但以前**生完就丢**——下一轮 agent 不知道自己
交代过什么，只能把学生当第一次见面。导师型 agent 的差别正在这里：他记得上次留的活，
下次见面先问那件事做了没有，并据回答调整后面的路。

单一出处：台账只存在 events 表（kind="commitment"，detail_json 装条目与状态），
intent.extra 里不留副本、文档里也不抄计数——两处各存一份迟早互相打脸。
状态判定只用**保守的机械匹配**（学生原话里出现完成/受阻线索、且与该条的判别词有重合），
匹配不上就维持原状；绝不让模型自己"推断"学生做没做，那是假记忆的源头。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from companion import db

KIND = "commitment"
# 台账里除 items 之外还带哪些字段：写进这份清单就等于写进覆写逻辑
LEDGER_FIELDS = ("artifact", "situation")
STATUSES = ("open", "done", "blocked", "dropped")
# 状态文案只在这里有一份：前端进度卡上那几个字必须由后端带过去（与 RESEARCH_STAGES 同规矩），
# 否则界面抄一份、代码改一份，两处迟早说的是不同的话。
STATUS_LABELS = {"open": "待回报", "done": "已完成", "blocked": "卡住了", "dropped": "不用做了"}
def _plan_action_cap() -> int:
    """动作条数上限只有一份出处：`mentor.PLAN_COUNTS["actions"]`。
    这里以前抄了一个 4 并注释"与之一致"——自认的抄本迟早会漂，所以改成现派生。"""
    from companion.conversation import mentor
    return int(mentor.PLAN_COUNTS.get("actions", 4))


MAX_ITEMS = _plan_action_cap()
MAX_INJECT = 3           # 提示词里最多回溯这几条，堆多了会把当前问题挤出去
MIN_TOKEN_OVERLAP = 1    # 判别词重合下限：0 个就说明这句话根本不指向该条

DONE_RE = re.compile(r"做完了|跑完了|跑通了|已完成|已经做|已按|按你说的做了|搞定了|做掉了|通过了|"
                     r"提交完了|写完了|跑出来|达标了")
BLOCKED_RE = re.compile(r"没做成|没跑出来|跑不通|卡住|报错|失败|没时间做|来不及做|还没做|没来得及|"
                        r"一直没做|做不动|没有结果")
# 「都/上次/那几/清单」+ 8 字内出现完成或受阻线索 ⇒ 整张清单一起结掉，不再逐条对判别词。
# 分开写两条而不是共用一份"线索词表"：同一批词既要判完成又要判受阻，含糊句就会两边都命中而被
# apply_student_report 整条放弃（实测过：一句"都跑完了"落到"分不清"分支，台账纹丝不动）。
ALL_DONE_RE = re.compile(r"(都|全都|全部|基本都|那几|上次|之前|清单|任务)[^。；]{0,8}"
                         r"(做完了|跑完了|跑通了|完成了|搞定了|提交了)")
ALL_BLOCKED_RE = re.compile(r"(都|全都|全部|基本都|那几|上次|之前|清单|任务)[^。；]{0,8}"
                            r"(没做|没跑|一件没|全卡住|都没时间|来不及做|没来得及)")
# 限量与否定一出现，"整张清单结掉"这种全局结论就不许下（宁可退回逐条保守匹配）
AMBIGUOUS_RE = re.compile(r"只|没|未|但|还|不算|部分|来不及|卡")

LATIN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}|\d{2,}")


def _grams(text: str) -> set[str]:
    """一条行动与学生原话**共用**的可比单元：拉丁词/多位数字（小写）+ 中文连续串的相邻二字组。

    两侧必须走同一个函数：以前各写一套（一侧按词、一侧按窗口），大小写与分词的差会让
    "明明说了基线"判成没重合，于是台账永远不动。
    """
    t = (text or "").lower()
    out = set(LATIN_RE.findall(t))
    for run in re.findall(r"[一-鿿]+", t):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _now() -> str:
    return db.utcnow()


def _items(plan: dict | None) -> list[dict[str, str]]:
    """从一份规划里取出可跟进的条目（缺字段给空串，不编造）。"""
    out: list[dict[str, str]] = []
    for raw in (plan or {}).get("actions") or []:
        if not isinstance(raw, dict):
            continue
        do = str(raw.get("do") or "").strip()
        if not do:
            continue
        out.append({"do": do[:60],
                    "with": str(raw.get("with") or "").strip()[:60],
                    "dose": str(raw.get("dose") or "").strip()[:40],
                    "check": str(raw.get("check") or "").strip()[:60],
                    "status": "open",
                    "updated_at": ""})
        if len(out) >= MAX_ITEMS:
            break
    return out


def record(project_id: str, plan: dict | None) -> int | None:
    """一轮规划产出了行动清单 → 追加一条 commitment 事件。清单为空或上一条逐字相同则不记。"""
    items = _items(plan)
    if not items:
        return None
    prev = latest(project_id)
    if prev and [i["do"] for i in prev["items"]] == [i["do"] for i in items]:
        return None       # 同一份清单重复入库会让"待跟进"看起来在累积
    conn = db.get_conn()
    summary = "行动清单：" + "；".join(i["do"] for i in items[:3])
    detail = {"items": items, "artifact": str((plan or {}).get("artifact") or "")[:60],
              "situation": str((plan or {}).get("situation") or "")[:80]}
    eid = db.write_event(conn, project_id, KIND, summary, detail)
    conn.commit()
    return eid


def latest(project_id: str) -> dict | None:
    """最近一份台账（含已完成的条目，供界面显示进度）。"""
    row = db.get_conn().execute(
        "SELECT id, summary, detail_json, created_at FROM events WHERE project_id=? AND kind=? "
        "ORDER BY id DESC LIMIT 1", (project_id, KIND)).fetchone()
    if not row:
        return None
    detail = _loads(row["detail_json"])
    items = detail.get("items") or []
    return {"id": int(row["id"]), "summary": row["summary"], "items": items,
            "artifact": detail.get("artifact") or "", "situation": detail.get("situation") or "",
            "created_at": row["created_at"], "age_hours": _age_hours(row["created_at"])}


def _loads(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _age_hours(created_at: str) -> float:
    try:
        then = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, round((datetime.now(timezone.utc) - then).total_seconds() / 3600.0, 1))


def _save_ledger(led: dict, items: list[dict[str, Any]]) -> None:
    """整份覆写 detail_json：除 items 外的每个字段都必须从旧台账带过来。

    🔴 这是「覆写丢字段」那一类缺陷（artifact 先漏过一次，situation 是第二个）。
    字段清单只在 LEDGER_FIELDS 出现一次，加第三个字段而忘了让它进 latest/stats，
    用例 test_status_write_keeps_every_declared_ledger_field 会红。
    """
    conn = db.get_conn()
    detail = {"items": items}
    detail.update({k: led.get(k) or "" for k in LEDGER_FIELDS})
    conn.execute("UPDATE events SET detail_json=? WHERE id=? AND kind=?",
                 (json.dumps(detail, ensure_ascii=False), led["id"], KIND))
    conn.commit()


def set_status(project_id: str, index: int, status: str) -> dict | None:
    """显式改一条的状态（界面上的「已完成 / 卡住了 / 不用做了」走这里）。"""
    if status not in STATUSES:
        raise ValueError(f"未知状态 {status}")
    led = latest(project_id)
    if not led:
        return None
    if not (0 <= index < len(led["items"])):
        return None
    items = [dict(i) for i in led["items"]]
    items[index]["status"] = status
    items[index]["updated_at"] = _now()
    _save_ledger(led, items)
    return {"index": index, "status": status, "open": open_count({"items": items})}


def open_count(led: dict | None) -> int:
    return sum(1 for i in (led or {}).get("items", []) if i.get("status") == "open")


def apply_student_report(project_id: str, user_text: str) -> list[dict[str, Any]]:
    """学生这句话里有没有回报上一轮的清单？只在线索明确时改状态，返回改动的条目。

    🔴 保守侧失败：宁可留着"待回报"让学生自己点按钮，也不许把没做过的事记成已完成——
    台账一旦写假，下一轮 agent 就会基于假事实推进计划（本项目测出过"假称记得"这类缺陷，
    所以这里全程不用模型判断）。
    """
    led = latest(project_id)
    if not led or not (user_text or "").strip():
        return []
    text = "".join((user_text or "").split())
    items = [dict(i) for i in led["items"]]
    changed: list[dict[str, Any]] = []
    # 🔴 全局线索先过"限量/否定"守卫：独立评审实测「上次交代那三条，我只跑通了一条，另外两条没时间弄」
    # 会被旧写法整表结掉（done 3 / open 0），下一轮就对他说"已回报完成 3 项"——这正是本函数
    # 承诺要挡的假记忆。**守卫只管"完成"这一侧**：把整张表记成"受阻"不会凭空邀功，
    # 而「都没来得及做」这种明确的全盘受阻回报是真话，不该被守卫挡掉。
    unambiguous = not AMBIGUOUS_RE.search(text)
    if unambiguous and ALL_DONE_RE.search(text):
        for i, it in enumerate(items):
            if it["status"] == "open":
                it.update(status="done", updated_at=_now())
                changed.append({"index": i, "status": "done"})
    elif ALL_BLOCKED_RE.search(text):
        for i, it in enumerate(items):
            if it["status"] == "open":
                it.update(status="blocked", updated_at=_now())
                changed.append({"index": i, "status": "blocked"})
    else:
        said_done, said_blocked = bool(DONE_RE.search(text)), bool(BLOCKED_RE.search(text))
        if not (said_done ^ said_blocked):
            return []       # 两类线索都有或都没有 → 分不清，不改
        status = "done" if said_done else "blocked"
        said = _grams(text)
        for i, it in enumerate(items):
            if it["status"] != "open":
                continue
            shared = _grams(" ".join([it.get("do", ""), it.get("with", ""),
                                      it.get("dose", ""), it.get("check", "")])) & said
            if len(shared) >= MIN_TOKEN_OVERLAP:
                it.update(status=status, updated_at=_now())
                changed.append({"index": i, "status": status})
                break       # 一次只认一条：学生说"那个跑通了"通常只指一件事
    if changed:
        _save_ledger(led, items)
    return changed


def followup_block(project_id: str) -> str:
    """给提示词的跟进上下文块：只讲事实（哪一轮、几项、状态），要不要开口问由规划决定。"""
    led = latest(project_id)
    if not led or not open_count(led):
        return ""
    lines = []
    opens = [i for i in led["items"] if i.get("status") == "open"]
    for n, it in enumerate(opens[:MAX_INJECT], 1):
        check = f"｜完成判据：{it['check']}" if it.get("check") else ""
        dose = f"｜分量：{it['dose']}" if it.get("dose") else ""
        lines.append(f"{n}. {it['do']}{dose}{check}")
    # 🔴 截断要说出来，而且要说**两个数**：只报"另有 N 项"会与上面 tally（按整张表数 done/blocked）
    #    在不同配置下对不上账（独立评审用 5 open + 2 done + 1 blocked 量到过），模型拿一份对不上的
    #    数字去跟学生说话，比不说更糟。这里同时给出"未结共几项"与"列了几项"，两个数同源。
    if len(opens) > MAX_INJECT:
        lines.append(f"（未结的一共 {len(opens)} 项，这里只列出前 {MAX_INJECT} 项；"
                     "剩下的学生在跟进卡上看得见——不许把列出的这几条说成全部）")
    ages = _age_phrase(led["age_hours"])
    done = sum(1 for i in led["items"] if i.get("status") == "done")
    blocked = sum(1 for i in led["items"] if i.get("status") == "blocked")
    tally = f"其中已回报完成 {done} 项" + (f"、受阻 {blocked} 项" if blocked else "") if done or blocked else "尚无一项回报完成"
    return (f"【你{ages}交代给这位学生的行动清单（{tally}）】\n" + "\n".join(lines) +
            "\n本轮处理：若学生的话与这张清单接得上，就先用一句话回应最该推进的那一项"
            "（整条回复只允许一个问句），再往下给下一步；接不上就别硬扯这张清单，"
            "也不要替学生宣布某项已经完成。")


def _age_phrase(hours: float) -> str:
    if hours < 1:
        return "刚才"
    if hours < 24:
        return f"{int(round(hours))} 小时前"
    return f"{int(round(hours / 24))} 天前"


def note_assumption(project_id: str, agree: bool, text: str = "") -> int:
    """学生点了「对/不对」：记下这次点击，界面才有的可统计，产品才知道理解偏在哪。

    记的是**点击**这个事实，不推断动机；「不对」时把学生补的话一起存下来（可为空）。
    """
    conn = db.get_conn()
    eid = db.write_event(conn, project_id, "assumption_feedback",
                         ("确认了研伴的理解" if agree else "纠正了研伴的理解"),
                         {"agree": bool(agree), "text": (text or "")[:200]})
    conn.commit()
    return eid


def open_items(project_id: str) -> list[dict[str, str]]:
    """还没结掉的条目（闸门要用）。没有台账就是空表，空表 ⇒ 相关闸门不开火。"""
    led = latest(project_id)
    if not led:
        return []
    return [i for i in led["items"] if i.get("status") == "open"]


CLAIM_RE = re.compile(r"做完了|已完成|跑通了|搞定了|你已经完成|你都做完了|做掉了|完成了")
QUESTION_RE = re.compile(r"吗|呢|？|\?|没做|是否")


def premature_claims(reply: str, items: list[dict]) -> int:
    """回复在"这条还开着"的时候替学生宣布完成 ⇒ 有几条这种话。

    🔴 只在**陈述句**里判：「上次那条基线跑通了吗？」正是我们希望 agent 问出口的话，
    把它当冒领会让闸门专挑自己想要的行为开火。所以先按句切，问句一律跳过。
    为什么这是一条**硬闸门**而不是一句提示词：g176（40 对 A/B）实测注入台账的那一臂
    有 4/32 句替学生宣布完成，关掉注入的是 0/32 —— 块里写着"不要替学生宣布某项已经完成"，
    模型不遵守。本仓库早已量过两次同类事实（措辞与重试都封不掉，轮 75—78），
    所以按自己的规矩办：能机检的就做成闸门，不做成愿望。
    """
    if not items:
        return 0
    grams_per = [_grams(it.get("do", "")) for it in items]
    n = 0
    for sent in re.split(r"(?<=[。！？!?])|\n", reply or ""):
        if not sent.strip() or QUESTION_RE.search(sent):
            continue
        g = _grams(sent)
        for m in CLAIM_RE.finditer(sent):
            near = sent[max(0, m.start() - 14):m.end() + 14]
            if any(gr and (_grams(near) & gr) for gr in grams_per):
                n += 1
                break
    return n


def stats(project_id: str) -> dict[str, Any] | None:
    """界面用的台账摘要（无台账返回 None，前端据此整块不渲染）。"""
    led = latest(project_id)
    if not led:
        return None
    return {"age_hours": led["age_hours"], "age_phrase": _age_phrase(led["age_hours"]),
            "situation": led["situation"],
            "artifact": led["artifact"], "open": open_count(led),
            "total": len(led["items"]), "labels": dict(STATUS_LABELS),
            "items": [{"do": i.get("do", ""), "check": i.get("check", ""),
                       "status": i.get("status") or "open"} for i in led["items"]]}
