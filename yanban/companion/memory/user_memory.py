"""User Profile 记忆：稳定用户画像（跨项目可复用，且学生看得见、可删）。

以前这个模块有表有函数，却**没有任何生产写入点**（只有测试调 upsert_user）——
画像永远是 {}，所谓"用户级记忆"是一句承诺。这一版把写入接上：
从**已经结构化好的** intent 里机械派生，不额外调模型、不从自由文本里猜。

只收这几个键（PROFILE_KEYS）：基础、机器与数据、时间、目标、阶段。
🔴 刻意不收 topic / 具体方向：那是项目内的东西，跨项目搬过去既不相干，也是把
一段研究经历当成"用户属性"的隐私越界（跨项目共享文献池是产品决策，不在本轮）。

存储形状：profile_json = {key: {"value": str, "project": pid, "at": utc}}。
`get_profile` 返回**扁平** {key: value}（老调用方与测试的口径不变），
需要出处就用 `profile_records`；写入口只有 `learn`，`upsert_user` 只负责整体覆盖时的归一化。
"""
from __future__ import annotations

import json

from companion import db

PROFILE_KEYS = ("experience", "hardware", "timeline", "target", "stage")
PROFILE_LABELS = {"experience": "基础", "hardware": "机器与数据", "timeline": "时间",
                  "target": "目标", "stage": "阶段"}
STAGE_CN = {"exploration": "领域调研", "literature_review": "文献综述", "topic_selection": "选题确定",
            "method_design": "方法设计", "experiment": "实验验证", "writing": "论文写作"}
MAX_VALUE_CHARS = 60


def _wrap(profile: dict) -> dict:
    """把扁平 dict 归一成带出处的记录；已经是记录形状的原样保留（幂等，防二次包装）。"""
    out: dict = {}
    for k, v in (profile or {}).items():
        if k not in PROFILE_KEYS:
            continue
        if isinstance(v, dict) and "value" in v:
            if str(v.get("value") or "").strip():
                out[k] = {"value": str(v["value"])[:MAX_VALUE_CHARS],
                          "project": str(v.get("project") or ""), "at": str(v.get("at") or "")}
        elif str(v or "").strip():
            out[k] = {"value": str(v).strip()[:MAX_VALUE_CHARS], "project": "", "at": db.utcnow()}
    return out


def upsert_user(user_id: str, profile: dict) -> dict:
    conn = db.get_conn()
    wrapped = _wrap(profile)
    conn.execute(
        "INSERT INTO users (id, profile_json, created_at) VALUES (?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json",
        (user_id, json.dumps(wrapped, ensure_ascii=False), db.utcnow()),
    )
    conn.commit()
    return {"user_id": user_id, "profile": {k: v["value"] for k, v in wrapped.items()}}


def profile_records(user_id: str) -> dict[str, dict]:
    row = db.get_conn().execute("SELECT profile_json FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["profile_json"] or "{}")
    except (ValueError, TypeError):
        return {}
    return _wrap(data if isinstance(data, dict) else {})


def get_profile(user_id: str) -> dict:
    return {k: v["value"] for k, v in profile_records(user_id).items()}


def snapshot(user_id: str) -> dict:
    return {"user_id": user_id, "profile": get_profile(user_id)}


def facts_from_intent(intent) -> dict[str, str]:
    """从结构化意图里机械派生画像事实——空值一律不收，收了就是拿"没说"当"说过"。"""
    ub = getattr(intent, "user_background", None)
    pg = getattr(intent, "publication_goal", None)
    rc = getattr(intent, "research_constraints", None)
    facts: dict[str, str] = {}
    level = next((getattr(ub, k, None) for k in ("dl_level", "ml_level", "programming_level")
                  if getattr(ub, k, None)), None) if ub else None
    if level:
        facts["experience"] = str(level)
    if rc and getattr(rc, "compute", None):
        facts["hardware"] = str(rc.compute)
    if pg and getattr(pg, "timeline", None):
        facts["timeline"] = str(pg.timeline)
    if pg and getattr(pg, "type", None) and pg.type != "unknown":
        facts["target"] = " ".join(x for x in (str(pg.type), str(getattr(pg, "level", "") or "")) if x)
    stage = getattr(intent, "research_stage", None)
    if stage and stage != "unknown":
        facts["stage"] = STAGE_CN.get(stage, str(stage))
    return {k: v.strip()[:MAX_VALUE_CHARS] for k, v in facts.items()
            if k in PROFILE_KEYS and str(v).strip()}


def learn(user_id: str, facts: dict[str, str], source_project: str = "") -> list[str]:
    """把这一轮问出来的稳定事实并进画像，返回**真的变了**的键。

    同值不重写时间戳：否则每轮都"刚刚更新"，出处与新鲜度两个读数都会失真。
    """
    if not facts:
        return []
    records = profile_records(user_id)
    changed: list[str] = []
    for k, v in facts.items():
        if k not in PROFILE_KEYS or not v:
            continue
        if records.get(k, {}).get("value") == v:
            continue
        records[k] = {"value": v, "project": source_project or records.get(k, {}).get("project", ""),
                      "at": db.utcnow()}
        changed.append(k)
    if changed:
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO users (id, profile_json, created_at) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET profile_json=excluded.profile_json",
            (user_id, json.dumps(records, ensure_ascii=False), db.utcnow()))
        conn.commit()
    return changed


def forget(user_id: str, key: str) -> bool:
    """学生说"这条别记了"：整键删掉，不留软删标记（留着就还是会被拼进提示词）。"""
    records = profile_records(user_id)
    if key not in records:
        return False
    records.pop(key)
    conn = db.get_conn()
    conn.execute("UPDATE users SET profile_json=? WHERE id=?",
                 (json.dumps(records, ensure_ascii=False), user_id))
    conn.commit()
    return True


def profile_block(user_id: str, except_project: str = "") -> str:
    """给提示词的画像块：每条都带着"来自另一个项目"的出处，绝不说成"你在这里说过"。"""
    records = profile_records(user_id)
    if not records:
        return ""
    lines = []
    for key in PROFILE_KEYS:
        rec = records.get(key)
        if not rec:
            continue
        if except_project and rec.get("project") == except_project:
            continue            # 本项目刚说过的不算"跨项目记忆"，说出来是假记忆
        lines.append(f"- {PROFILE_LABELS[key]}：{rec['value']}")
    if not lines:
        return ""
    return ("【这位学生在其它项目里稳定下来的画像（可能已过时，与本轮说法冲突时以本轮为准）】\n"
            + "\n".join(lines))
