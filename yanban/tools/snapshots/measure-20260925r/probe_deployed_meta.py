"""轮 153 线上复量：右栏进度卡要的字段，必须在**容器里那份代码**上成立。

本机 pytest 全绿只证明工作树里的逻辑对；卡片显示的是线上会话发回来的那三个字段。
轮 144 的教训是「部署回执 + 逐文件一致」曾一起为一份没上线的修复背书 ⇒ 先比 sha，再直接调线上那份函数。

判据（跑前写死，一条都不许事后加）：
  E 认路：容器里三个改动文件的 sha256 各等于本机提交份（不等就是给旧版做读数，整场作废）。
  M1 线：阶段已知的意图 ready_threshold == ready_for_search 的参数默认值；阶段未知 == 默认值 + 抬档常量。
      期望值全部从容器里的签名/常量取，本探针不写 0.52。
  M2 标签：空白意图的 gap_labels 条数 == gaps 条数，且每条标签含汉字。
  M2n 判据自证：喂一个两张表都没覆盖的字段，标签必须**没有**汉字 —— 证明 M2 真的会咬，不是恒绿。
  M3 正对照：放行那份意图的 gap_labels 必须是空列表（只测非空分支等于没测空分支）。
  M4 表头：meta.stage_labels 与容器里的 RESEARCH_STAGES 逐键相同（前端不许另建一份中文表）。
  Z 零模型调用：给 llm.chat_json/chat/chat_text 装雷，计数必须为 0；--plant-net 那场必须红在 Z。
  R 只读自证：/app/data/yanban.db 的 mtime+size 在任何产品函数被调用之前取，跑完再取，逐位相同。
  防空转：判定实际执行次数不足下限 ⇒ RC=2（早退或异常吞掉都不许读成全绿）。
起跑（本机绝对路径由 run_cloud_probe 负责上传，植入开关走 argv）：
  <python> 输出/scripts/run_cloud_probe.py <本文件绝对路径> --expect-sha-dm … --expect-sha-clar … --expect-sha-ia …
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

DB = pathlib.Path("/app/data/yanban.db")
FILES = {"dm": "/app/companion/conversation/dialogue_manager.py",
         "clar": "/app/companion/intent/clarification.py",
         "ia": "/app/companion/conversation/interview_agent.py"}

bad: list[str] = []
ran = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global ran
    ran += 1
    print(f"    {'✅' if ok else '❌'} {name}" + (f"｜{detail}" if detail else ""))
    if not ok:
        bad.append(name)
    return ok


def arg(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1].strip() if name in sys.argv else ""


def main() -> int:
    st0 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    print(f"DB 起始自证：{'存在 mtime_ns=%d size=%d' % st0 if st0 else '不存在'}")

    print("\nE 认路（先比字节，再谈读数）：")
    for key, path in FILES.items():
        p = pathlib.Path(path)
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        want = arg(f"--expect-sha-{key}")
        check(f"E {p.name} 与本机提交份一致", bool(want) and got == want, f"sha={got[:16]}…")
    if any(n.startswith("E ") for n in bad):
        print("\n判读：线上字节与本机份不符 ⇒ 后面全是给旧版做的读数，不落任何结论（RC=2）")
        return 2

    sys.path.insert(0, "/app")
    import llm
    from companion.conversation import dialogue_manager as dm
    from companion.conversation import interview_agent as ia
    from companion.intent import clarification as cl
    from companion.intent.model import RESEARCH_STAGES, ResearchIntent

    llm.LLM_MOCK = True
    calls = {"n": 0}
    for fn in ("chat_json", "chat", "chat_text"):
        if hasattr(llm, fn):
            setattr(llm, fn, (lambda orig: (lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                                             orig(*a, **k))[1]))(getattr(llm, fn)))
    base = __import__("inspect").signature(cl.ready_for_search).parameters["threshold"].default
    known = next(s for s in RESEARCH_STAGES if s != "unknown")

    def turn(topic: str, stage: str) -> dict:
        it = ResearchIntent(project_id="probe-r153", topic=topic, research_stage=stage)
        return {"project_id": it.project_id, "intent": it,
                "gaps": list(cl.clarification_gaps(it))[:3], "papers": [],
                "still_clarifying": True, "state": "CLARIFY", "history": []}

    print("\nM1 放行线（期望值全部从容器里的签名/常量取）：")
    mk = dm.meta_event(turn("多模态钓鱼网站检测", known))
    check("M1a 阶段已知 ⇒ 线 == 参数默认值", mk["ready_threshold"] == base,
          f"线={mk['ready_threshold']} 默认={base}")
    mu = dm.meta_event(turn("多模态钓鱼网站检测", "unknown"))
    check("M1b 阶段未知 ⇒ 线 == 默认值 + 抬档", mu["ready_threshold"] == base + cl.STAGE_UNKNOWN_PENALTY,
          f"线={mu['ready_threshold']} 抬档={cl.STAGE_UNKNOWN_PENALTY}")
    check("M1c 未知阶段那条线确实更高（不是两条恒等的装饰）",
          mu["ready_threshold"] > mk["ready_threshold"])

    print("\nM2 缺口中文标签：")
    g = mu["gap_labels"]
    check("M2a 条数与 gaps 对齐", len(g) == len(mu["gaps"]) and len(g) > 0,
          f"标签 {len(g)} / gaps {len(mu['gaps'])}")
    han = lambda s: any("一" <= ch <= "鿿" for ch in s)
    check("M2b 每条标签都含汉字", all(han(x["label"]) for x in g),
          "、".join(x["label"] for x in g[:4]))
    naked = ia.gap_labels(["__no_such_field__"])[0]["label"]
    check("M2n 判据自证：表外字段的标签必须没有汉字（否则 M2b 恒绿）", not han(naked), f"实得={naked!r}")

    print("\nM3/M4 正对照与表头同源：")
    rich = turn("多模态钓鱼网站检测", known)
    rich["gaps"] = []
    rich["still_clarifying"] = False
    mr = dm.meta_event(rich)
    check("M3 放行那份意图的标签必须是空列表", mr["gap_labels"] == [], f"实得={mr['gap_labels']}")
    check("M4 stage_labels 与容器里的 RESEARCH_STAGES 逐键相同", mr["stage_labels"] == RESEARCH_STAGES,
          f"{len(mr['stage_labels'])} 键")

    print("\nZ/R 自证：")
    if "--plant-net" in sys.argv:
        try:
            llm.chat_json("plant")
        except Exception:  # noqa: BLE001 — 植入只要留下调用计数
            pass
    check("Z 零模型调用（雷计数 == 0）", calls["n"] == 0, f"计数={calls['n']}")
    st1 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    check("R DB mtime+size 逐位未变（全程只读）", st1 == st0, f"前={st0} 后={st1}")

    if ran < 11:
        print(f"\n判读：只执行到 {ran} 条判定（下限 11）⇒ 有分支被跳过，这份读数不作数（RC=2）")
        return 2
    if bad:
        print(f"\n判读：{len(bad)} 条不成立 ⇒ {bad}（RC=2）")
        return 2
    print(f"\n判读：线上那份函数给出的线/标签/表头与本机一致，零模型调用、DB 未变（{ran} 条判定全过，RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
