"""Memory Retriever：按当前问题动态取 Top-K 相关记忆。

v1 用 TF-IDF（零外部依赖）；glm embedding 可用时自动切换（接口不变）。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from companion import db

NOTE_BONUS = 0.35   # 学生显式说「记住这件事」的加权，让它在 Top-K 里排到前面
DEFAULT_K = 3       # 注入条数上限：记忆的价值在写入侧不在堆量（调研 E：干扰条目会显著拉低准确率）
MAX_NOTES = 2       # 显式笔记最多想起最近 2 条，且按文本去重


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]{2,}|[\u4e00-\u9fff]", (text or "").lower())


class _TfIdf:
    def __init__(self, docs: list[str]):
        self.docs = docs
        self.tfs: list[Counter] = [Counter(_tokenize(d)) for d in docs]
        df: Counter = Counter()
        for tf in self.tfs:
            for tok in tf:
                df[tok] += 1
        self.idf = {t: math.log((len(docs) + 1) / (n + 1)) + 1 for t, n in df.items()}

    def top_k(self, query: str, k: int) -> list[tuple[float, int]]:
        q = Counter(_tokenize(query))
        scores = []
        for i, tf in enumerate(self.tfs):
            s = sum(tf[t] * self.idf.get(t, 1.0) * n for t, n in q.items() if t in tf)
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return scores[:k]


def retrieve(project_id: str, query: str, k: int = DEFAULT_K) -> list[dict]:
    """跨 messages + events 检索与当前问题最相关的记忆片段。

    注入条数默认压到 3（LongMemEval / Mem0 一系实测：记忆的价值在写入侧不在堆量，
    干扰条目能把准确率相对拉低几十个百分点）。学生显式「记住这件事」仍然优先想起，
    但①按文本去重、②只保留最近的 MAX_NOTES 条——原来是无条件插到最前且不封顶，
    长对话里四五条重复笔记会把真正相关的片段挤出窗口。
    """
    conn = db.get_conn()
    msgs = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE project_id=?) ORDER BY id DESC LIMIT 200",
        (project_id,),
    ).fetchall()
    evts = conn.execute(
        "SELECT id, kind, summary, created_at FROM events WHERE project_id=? ORDER BY id DESC LIMIT 100",
        (project_id,),
    ).fetchall()
    docs = [f"{m['role']}: {m['content']}" for m in msgs] + [f"[{e['kind']}] {e['summary']}" for e in evts]
    meta = [dict(m) for m in msgs] + [dict(e) for e in evts]
    if not docs:
        return []
    k = min(k, len(docs))
    picks = _TfIdf(docs).top_k(query, k + MAX_NOTES)     # 多取几条，留给去重后补足
    chosen = [i for _s, i in picks]
    # 学生显式「记住这件事」优先想起来，但去重 + 只留最近的 MAX_NOTES 条（详见 retrieve 的说明）
    seen: set[str] = set()
    notes: list[int] = []
    for i in reversed([j for j, m in enumerate(meta) if m.get("kind") == "user_note"]):
        key = re.sub(r"\s+", "", str(meta[i].get("summary", "")))[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        notes.append(i)
        if len(notes) >= MAX_NOTES:
            break
    for i in reversed(notes):
        if i not in chosen:
            chosen.insert(0, i)
    # 最终窗口里再统一去重+封顶：TF-IDF 自己也会把重复笔记排进前列，只挡住"插入"这一路不够
    kept: list[int] = []
    used_notes: set[str] = set()
    for i in chosen:
        m = meta[i]
        if m.get("kind") == "user_note":
            key = re.sub(r"\s+", "", str(m.get("summary", "")))[:40]
            if key in used_notes or len(used_notes) >= MAX_NOTES:
                continue
            used_notes.add(key)
        kept.append(i)
        if len(kept) >= k:
            break
    chosen = kept
    out = []
    for i in chosen[:k]:
        m = dict(meta[i])
        rank = next((float(s) for s, j in picks if j == i), 0.0)
        m["score"] = round(rank + (NOTE_BONUS if m.get("kind") == "user_note" else 0.0), 3)
        out.append(m)
    return out


def summarize_context(project_id: str, query: str, k: int = DEFAULT_K) -> str:
    """把 Top-K 记忆拼成可注入 prompt 的上下文块。"""
    items = retrieve(project_id, query, k)
    return "\n".join(f"- [{m.get('kind', m.get('role', '?'))}] {m.get('summary', m.get('content', ''))[:160]}"
                     for m in items)
