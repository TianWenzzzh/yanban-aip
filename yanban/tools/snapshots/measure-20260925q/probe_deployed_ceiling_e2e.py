"""轮 152 追加：那两条判给 `llm_topic` 的天花板句，端到端到底进不进得了检索轮（**真发模型调用**）。

我在夹具与迭代日志里都写了「判给兜底通道」，但轮 148 量到"模型即便给出 topic，confidence 也只 0.231、
ready_for_search 仍为假"——所以"判给 llm_topic"这句话目前只是把球传出去，没证明有人接。
这一场把那一格从"未证"变成数：走**产品自己的** `extract_update`（内含 `capture_topic(text) or llm_topic(text)`），
再喂 `apply_update` → `ready_for_search`，不重写任何一道判定。

判据（跑前写死）：
  E 认路：`/app/companion/intent/extractor.py` 的 sha256 == 本机提交份；
  F 同源：`/tmp/fixtures_topic_corpus.py` 的 sha256 == 仓内那份（句子只有一份）；
  Z0 认路哨兵：`dialogue_manager.prepare_context` 里那句合取逐字在且只在 1 处（轮 149 同一条）；
  M1 模式：`llm.LLM_MOCK` 必须为 **False**（这场就要真调用）；
  M2 活性：真实调用计数必须 ≥1——为 0 就说明两条句子根本没走进兜底通道，那读数只是规则通道的老账（RC=2）；
      `--plant-mock` 把 chat_json 摘成不计数，必须红在 M2（不点一下分不清"没调用"与"计数是装饰"）；
  C 读数：每条天花板句报 topic（规则/兜底分开）、confidence、ready_for_search、缺哪些槽位；
  P 正对照：g98 里线上真拿到过卡的那句中文，同口径跑，topic 必须非空；
  N 负对照：「我刚入门，不知道做什么方向」放行必须为 False（尺子两向都分得开才算尺子）；
  R 只读自证：`/app/data/yanban.db` 的 mtime+size 在任何产品函数之前取、跑完再取，逐位相同。
阈值一律现读（`ready_for_search` 的默认参数），不写死数字。
"""
from __future__ import annotations

import hashlib
import inspect
import pathlib
import sys

DB = pathlib.Path("/app/data/yanban.db")
SRC = pathlib.Path("/app/companion/intent/extractor.py")
FIX = pathlib.Path("/tmp/fixtures_topic_corpus.py")
DM = pathlib.Path("/app/companion/conversation/dialogue_manager.py")
CONJUNCTION = ("still_clarifying = compose_interview_reply(intent) is not None "
               "and not wants_results(user_text)")


def arg(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1].strip() if name in sys.argv else ""


def main() -> int:
    bad: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"    {'✅' if ok else '❌'} {name}" + (f"｜{detail}" if detail else ""))
        if not ok:
            bad.append(name)

    got = hashlib.sha256(SRC.read_bytes()).hexdigest()
    want = arg("--expect-sha")
    check("E 线上 extractor.py 与本机提交份一致", bool(want) and got == want, f"sha={got[:12]}…")
    fx = hashlib.sha256(FIX.read_bytes()).hexdigest()
    check("F 句集与仓内那份同源", fx == arg("--expect-fixture-sha"), f"sha={fx[:12]}…")
    if bad:
        print("判读：认路/同源不过 ⇒ 后面的读数不作数（RC=2）")
        return 2

    sys.path.insert(0, "/tmp")
    import fixtures_topic_corpus as fx           # noqa: E402
    import llm                                    # noqa: E402
    from companion.conversation.interview_agent import compose_interview_reply, wants_results
    from companion.intent import extractor        # noqa: E402
    from companion.intent.clarification import ready_for_search
    from companion.intent.model import ResearchIntent

    real = llm.chat_json
    calls = {"n": 0}

    def counted(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    if "--plant-mock" in sys.argv:
        print("    （植入）把兜底通道摘成不计数，期望红在 M2")
        llm.chat_json = lambda *a, **kw: {}
    else:
        llm.chat_json = counted

    print(f"Z0 认路哨兵：合取逐字在 prepare_context 里 {DM.read_text(encoding='utf-8').count(CONJUNCTION)} 处")
    if DM.read_text(encoding="utf-8").count(CONJUNCTION) != 1:
        print("判读：产品那步合取换了形状 ⇒ 复刻的不是线上那份判定（RC=2）")
        return 2
    check("M1 LLM_MOCK 为假（这场要真调用）", llm.LLM_MOCK is False, f"LLM_MOCK={llm.LLM_MOCK}")

    st0 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    thr = inspect.signature(ready_for_search).parameters["threshold"].default

    def verdict(text: str) -> dict:
        it = ResearchIntent(project_id="probe-r152e2e")
        upd = extractor.extract_update(text, it)
        rule = extractor.capture_topic(text)
        it.apply_update(upd)
        still = compose_interview_reply(it) is not None and not wants_results(text)
        ok, _why = ready_for_search(it)
        return {"topic": it.topic or "", "rule": rule, "conf": it.confidence,
                "ready": ok, "go": not still, "missing": it.missing_fields()}

    print(f"\nC 天花板句端到端（阈值 {thr} 现读自 `ready_for_search` 默认参数）")
    rows = {}
    for t in sorted(fx.LLM_ONLY):
        v = verdict(t)
        rows[t] = v
        via = "规则通道" if v["rule"] else ("兜底 llm_topic" if v["topic"] else "两条都没给")
        print(f"  · {t[:34]}\n      topic={v['topic']!r}（{via}）｜confidence={v['conf']}"
              f"｜ready_for_search={v['ready']}｜首问放行={v['go']}｜缺槽位={v['missing']}")

    import json
    g98_path = next((p for p in (pathlib.Path("/tmp/g98_cite_pool.json"),
                                 pathlib.Path("/app/docs/evidence/g98_cite_pool.json")) if p.exists()), None)
    g98 = json.loads(g98_path.read_text(encoding="utf-8")) if g98_path else []
    pos = next((r.get("user_text") or "" for r in g98 if (r.get("user_text") or "").strip()), "")
    if not pos:
        print("\nP 正对照：没拿到 g98 那份样本文件 ⇒ 这条没测，记未决（不当通过）")
        bad.append("P 正对照缺样本")
    else:
        p = verdict(pos)
        print(f"\nP 正对照（规则通道就该命中）：topic={p['topic']!r}｜conf={p['conf']}｜放行={p['go']}")
        print(f"    原文：{pos[:60]}")
        check("P 正对照 topic 非空且放行", bool(p["topic"]) and p["go"] is True)
    n = verdict("我刚入门，不知道做什么方向")
    print(f"N 负对照：topic={n['topic']!r}｜conf={n['conf']}｜放行={n['go']}（必须 False）")
    check("N 负对照放行必须为 False", n["go"] is False)

    st1 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    check("R 线上库只读自证：mtime+size 逐位未变", st0 is not None and st0 == st1)
    check("M2 真实模型调用计数 ≥1（兜底通道真的被走到）", calls["n"] >= 1, f"计数={calls['n']}")

    if bad:
        print(f"判读：{len(bad)} 条不过：{bad}（RC=2）")
        return 2
    given = sum(1 for v in rows.values() if v["topic"])
    ready = sum(1 for v in rows.values() if v["ready"])
    print(f"\n判读：两条天花板句里 llm_topic 给出 topic {given}/{len(rows)} 条、"
          f"ready_for_search 为真 {ready}/{len(rows)} 条（RC=0）｜真实调用 {calls['n']} 次")
    return 0


if __name__ == "__main__":
    sys.exit(main())
