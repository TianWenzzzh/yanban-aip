"""#25 的下一次起跑能不能不写一个字就走到检索轮？——扫外部提问池，零模型调用、零网络。

轮 144 的 D1 读数说：A/B 的 140 条回复全部落在 `skipped=clarifying`，因为 `prepare_context` 里
    still_clarifying = compose_interview_reply(intent) is not None and not wants_results(user_text)
——提问本身没说出"要结果"，产品就先反问，而反问轮按轮 40 的盲评结论**不检索**。

所以先量一件事：这批不是我写的提问里，有多少条**自己的措辞**就能让 `wants_results` 为真。
够 ⇒ 换驱动方式不需要我代写任何一句对话；不够 ⇒ 如实写"这一维在外部池上测不了"，
而不是偷偷补一句人写的话再把它读成"真实分布"。

判据（跑前写死）：
  W0 模式声明：`llm_topic`（`extractor.py:156`）只在 `LLM_MOCK=1` 时才不联网——本仪器**自己把这个
     环境变量设成 1**，并用计数器桩证明整场一次都没进过那条通道。计数器本身由 `--selftest`
     的植入用例盯着（调一次必须数到 1，不调必须数到 0）。
  W1 计数：`wants_results(t)` 为真的条数，与长跑启动门槛 `POOL_MIN` 比（从 probe_plan_ab 现读）。
  W1c 分解：规则通道 `capture_topic(t)` 抽出非空 topic 的条数——把「0 条放行」拆成
     「主题没抽出来」与「主题有了但访谈没问完」两类，好和线上 D1（140/140 topic 空串）同轴可比。
  W2 控制：正对照取 g98 池里**轮 139 真拿到过卡**的那条中文情景，负对照取明显不放行的英语求助句；
     两者方向相反才说明这把尺子分得开「会检索」与「被反问」。
  放行条件是 `访谈未完 且 不催结果` 的合取，所以 W1 与 W1c 两个读数要一起看。

🔴 为什么 W0 是后来加的（v1 的错误）：v1 在文件头写「零模型调用、零网络」，但那句**只是我的断言，
没有任何东西盯着它**。实测（`diag_llm_topic_local.py`）：本机 `LLM_MOCK` 未设 ⇒ `llm.LLM_MOCK=False`
⇒ 每条提问都真的走进 `llm_topic` 的网络分支，单次 1.32s、返回空串（本机 `llm.API_KEY` 为空，
请求打不出去也不消耗模型额度）。v1 那一场因此发了约 208 次失败的网络尝试。
读数（0/206）本身仍是「规则通道单独能放行几条」的有效回答——因为模型通道全挂了、topic 恒空——
但它**不是**「产品真实路径会怎么判」：线上 `llm_topic` 是可用的。v2 把这个区别变成机制而不是一句话。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"   # 必须早于任何 `import llm`，否则那条兜底通道会真的联网

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "code" / "yanban"), str(ROOT / "输出" / "scripts")]

POOL = ROOT / "code" / "yanban" / "docs" / "evidence" / "g132_external_questions_pool.json"


def gate() -> dict:
    """起跑预算的两个数从长跑源码现读：复用供给体检那台 `load_gate`，不再抄第三个抽取器。"""
    from probe_supply_precheck import load_gate
    ns, _ = load_gate(str(ROOT / "输出" / "scripts" / "probe_plan_ab.py"))
    return ns


CALLS = {"llm_topic": 0}


def instrument() -> tuple[int, str]:
    """把 `llm_topic` 换成计数桩，并确认 mock 真的生效。返回 (进入兜底通道的次数上限证据, mock 状态)。

    桩为什么有效：`extract_update` 是按**模块全局名**调 `llm_topic(text)` 的，所以替换
    `extractor.llm_topic` 就能计数；反过来，若哪天它改成 `from .extractor import llm_topic`
    式的局部绑定，桩就不再接线 ⇒ `--selftest` 的植入用例专门盯这件事。
    """
    from companion.intent import extractor
    import llm

    if os.environ.get("PROBE_PLANT_UNWIRE") == "1":
        # 负控制（跑法：PROBE_PLANT_UNWIRE=1 同一条命令）：把桩摘掉，W0b 必须红。
        # 不红就说明这条判据是装饰——它盯的是「计数与规则通道对账」，不是自己那行字。
        return bool(llm.LLM_MOCK), f"llm.LLM_MOCK={llm.LLM_MOCK!r}｜🔴 植入：计数桩未接线（本场是负控制）"

    real = extractor.llm_topic

    def counted(text: str) -> str:
        CALLS["llm_topic"] += 1
        return real(text)

    extractor.llm_topic = counted
    return bool(llm.LLM_MOCK), f"llm.LLM_MOCK={llm.LLM_MOCK!r}"


def selftest() -> int:
    """W0 的负控制：植入一次调用必须被数到，且没植入时计数必须为 0（否则计数器是装饰）。"""
    from companion.intent import extractor
    before = CALLS["llm_topic"]
    extractor.llm_topic("测试一下兜底通道有没有接线")
    hit = CALLS["llm_topic"] - before
    print(f"S1 植入 1 次调用 ⇒ 计数 +{hit}（必须 =1，=0 说明桩没接上，本仪器的 W0 是装饰）")
    print(f"S2 mock 下 llm_topic 返回 {extractor.llm_topic('随便一句话')!r}（必须 '' ：mock 生效时这条通道直接返回空、不联网）")
    if hit != 1:
        print("判读：计数器没接到植入 ⇒ 关掉它之后没有任何一层看得见这条通道（RC=2）")
        return 2
    print("判读：W0 桩与 mock 都自证过 ⇒ 本读数的「零网络」是机制，不是一句话")
    return 0


def clarifies_factory():
    from companion.conversation.dialogue_manager import compose_interview_reply, wants_results
    from companion.intent.extractor import extract_update
    from companion.intent.model import ResearchIntent

    def clarifies(t: str) -> bool:
        """逐字复刻 `dialogue_manager.prepare_context` 的那一句判定（规则通道，零模型调用）。"""
        it = ResearchIntent(project_id="probe_wants")
        it.apply_update(extract_update(t, it))
        return compose_interview_reply(it) is not None and not wants_results(t)

    return clarifies


def main() -> int:
    from companion.intent.extractor import capture_topic

    mock_on, mock_state = instrument()
    if "--selftest" in sys.argv:
        return selftest()
    if not mock_on:
        print(f"判读：W0 不成立（{mock_state}）⇒ 本场可能已真联网，「零模型调用」的口径不成立，读数不作数（RC=2）")
        return 2
    clarifies = clarifies_factory()

    rows = json.loads(POOL.read_text(encoding="utf-8"))
    texts = []
    seen: set[str] = set()
    for r in rows:
        t = (r.get("user_text") or "").strip()
        if len(t) >= 8 and t not in seen:
            seen.add(t)
            texts.append(t)
    print(f"池子去重后 {len(texts)} 条｜判定复刻自 prepare_context（含 extract_update 规则通道）")

    go = [t for t in texts if not clarifies(t)]
    themed = [t for t in texts if capture_topic(t)]
    # W0 的计数必须在跑完之后取：v2 第一版把这几行印在循环**之前**，于是「进入兜底通道 0 次」
    # 恒成立——一条看着像在自证的门其实什么都没看。现在加 W0b 把两个读数锁在一起（漏几条就该进几条），
    # 接线错、早退、被优化掉都会红。
    n_fb = CALLS["llm_topic"]
    print(f"W0 模式：{mock_state}｜全场进入 llm_topic 兜底通道 {n_fb} 次（mock 分支直接返回空，不发网络请求）")
    print(f"W0b 一致性：兜底进入次数必须 = 规则通道漏掉的条数 {len(texts) - len(themed)} "
          f"⇒ {'成立' if n_fb == len(texts) - len(themed) else '不成立'}")
    if n_fb != len(texts) - len(themed):
        print("判读：W0b 不成立 ⇒ 计数桩与规则通道对不上账，本场读数不作数（RC=2）")
        return 2
    print(f"W1 首问就直接进检索：{len(go)} 条｜W1b 仍被反问：{len(texts) - len(go)} 条")
    for t in go[:10]:
        print(f"    · {t[:70]}")

    print(f"W1c 规则通道抽出非空 topic：{len(themed)} 条｜其中首问就放行的：{sum(1 for t in go if capture_topic(t))} 条")
    print("     （这一分解与线上 D1「140/140 抽取后 topic 为空串」同轴：这里量的是规则通道单独能抽出几条）")

    # 正对照取 g98 池里**轮 139 真拿到过卡**的那条（不是标题，是当时的 user_text）
    g98 = json.loads((ROOT / "code/yanban/docs/evidence/g98_cite_pool.json").read_text(encoding="utf-8"))
    pos = next((r.get("user_text") or "" for r in g98 if (r.get("user_text") or "").strip()), "")
    neg = "Will my cancer ruin my chances of getting into a good PhD program?"
    ok_pos, ok_neg = (not clarifies(pos)) if pos else False, clarifies(neg)
    print(f"W2 正对照（g98 里轮 139 拿到卡的中文情景）直接进检索={ok_pos}｜"
          f"负对照（英语求助句）被反问={ok_neg}")
    if pos:
        print(f"    正对照原文：{pos[:64]}")
    if not (ok_pos and ok_neg):
        print("判读：W2 不成立 ⇒ 这把尺子分不开「会检索」与「被反问」，本次计数不作数（RC=2）")
        return 2

    pool_min = gate()["POOL_MIN"]
    if len(go) >= pool_min:
        print(f"判读：外部池里有 {len(go)} 条首问就进检索 ≥ POOL_MIN={pool_min:.0f}"
              "⇒ #25 可以只靠选子集起跑，不需要代写任何一句对话（RC=0）")
        return 0
    print(f"判读：只有 {len(go)} 条 < POOL_MIN={pool_min:.0f} ⇒ 外部池上「带料的 A/B」凑不出分母；"
          "要么按预登记改判据（换验收分母/换测量层），要么如实记这一维未测——"
          "不要代写第二回合再把它读成真实分布（RC=3）")
    return 3


if __name__ == "__main__":
    sys.exit(main())
