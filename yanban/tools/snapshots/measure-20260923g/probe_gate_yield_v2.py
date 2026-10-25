"""闸门产量体检（升级版）：每道闸门"该拦的拦住没有"、重采样花了几次、放过去多少。

历史版本只盯 no_substance/thin_quant 两道，轮 52 之后闸门已经有七道
（fake_title / multi_question / false_memory / no_substance / no_quantity / plan_dropped / no_echo），
而且新加的 plan_dropped、no_echo 需要 plan 与 user_text 才能判定——不传就永远不触发，读数会假绿。
所以这里按 violations() 的完整键集统计，并单独报每次回复花了几次模型调用。

容器内跑：python /tmp/probe_gate_yield_v2.py [每档轮数]
"""
from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")      # 也允许在 code/yanban 下本地跑 LLM_MOCK 自检

import llm                                        # noqa: E402
from companion.conversation import mentor         # noqa: E402

# 自己往容器里落一份输出：上一轮 g90 就是靠 SSH 通道回传，通道一断，一小时的重采样全部丢掉。
# 断线后用 `fetch_probe_output.py <本地路径>` 一条命令取回（它会顺便回答「到底跑完没有」）。
# 文件名带脚本名：两个探针同时跑时，后启动的那个不会把前一份截断成空。
_TEE = os.environ.get("PROBE_TEE") or (
    f"/tmp/probe_out_{os.path.basename(__file__)[:-3]}.txt" if os.name == "posix" else None)
if _TEE:
    class _Tee:
        def __init__(self, path: str) -> None:
            self.f = open(path, "w", encoding="utf-8")

        def write(self, s: str) -> int:
            self.f.write(s)
            self.f.flush()
            return sys.__stdout__.write(s)

        def flush(self) -> None:
            self.f.flush()

    sys.stdout = _Tee(_TEE)     # type: ignore[assignment]
    print(f"边车（本地管道断了就取回这份）：{_TEE}", flush=True)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

CASES = [
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够", "不太会调参"),
    ("我已经读了二十来篇多智能体协作的论文，想整理成综述，但不知道按什么线组织", "导师想让我投 CCF-B"),
    ("我只想把实验数据的相关性分析跑通，不确定用什么统计方法，别给我讲太多理论", "变量 12 个，样本 87 份，多数不是正态"),
    ("我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点", "只有一台轻薄本，没有 GPU，数据自己爬"),
    # 轮 115 加的第 5 个情景：g96a 量到它 10/10 都带 8 张卡，是 `no_citation` 少见的稳定靶子；
    # 原来那 4 个里有 2 个 10/10 一张卡都没有（闸门按设计不许对空池开火），
    # 所以那批读数里这条闸门 0 次开火并不说明它没代价——只是那台仪器看不见它。
    ("我们实验室在做光伏功率预测，我想找一个还没人做的点", "只有两年历史数据，15 分钟采样"),
]

keys = list(mentor.violations("试", papers=[], history=[]).keys())
pre = collections.Counter()
post = collections.Counter()
plan_ok = 0
n = 0
fail_reasons = collections.Counter()
calls = 0
resampled = 0
lengths = []
cards: list[int] = []      # 每条回复当轮拿到几张论文卡：闸门开火率的分母

_chat = mentor.chat


async def spy_chat(prompt, **kw):
    global calls
    calls += 1
    return await _chat(prompt, **kw)


_repair = mentor.repair


def spy_repair(text, **kw):
    global n, plan_ok, resampled
    out = _repair(text, **kw)
    n += 1
    import inspect
    kw.setdefault("papers", [])
    kw.setdefault("history", [])
    kw.setdefault("user_text", "")
    allowed = set(inspect.signature(mentor.violations).parameters)   # 部署中的 build 可能还没有新参数
    arg = {k: kw[k] for k in ("papers", "history", "user_text") if k in allowed}
    if "plan" in allowed:
        arg["plan"] = kw.get("plan")
    v_pre = mentor.violations(text, **arg)
    v_post = mentor.violations(out, **arg)
    for k in keys:
        pre[k] += bool(v_pre[k])
        post[k] += bool(v_post[k])
    resampled += any(v_pre.values())
    if kw.get("plan"):
        plan_ok += 1
    else:   # 计划没产出时把 intent.extra 里的原因收上来（44% → 归因，不再靠猜）
        intent = kw.get("intent")
        why = str(getattr(intent, "extra", {}).get("plan_fail", "未记录"))[:70] if intent else "无 intent"
        fail_reasons[why] += 1
    lengths.append(len(out))
    nc = len(arg.get("papers") or [])
    cards.append(nc)
    # 逐样本即时打印：这支探针原来只在最后一次性输出，几小时的跑法中途看不出
    # "卡住了"还是"在跑"，也看不见失败/跳过计数（长跑前的只读体检因此无从下手）。
    # 卡数必须一起印：轮 115 的 `no_citation` 报了 0/48 开火，可没有分母就分不清
    # "闸门没出手（这批回复本来就没卡）"与"闸门坏了"——那是两回事。
    fired = ",".join(k for k in keys if v_pre[k]) or "无"
    left = ",".join(k for k in keys if v_post[k]) or "无"
    print(f"[{n}] 长度 {len(out):>4d}｜卡 {nc}｜闸门前 {fired}｜闸门后 {left}"
          f"｜计划={'有' if kw.get('plan') else '无'}", flush=True)
    return out


mentor.chat = spy_chat
mentor.repair = spy_repair


def main() -> int:
    if getattr(llm, "LLM_MOCK", False):
        print("LLM_MOCK=1：闸门产量必须在真模型上测，这里不出数。")
        return 3
    from companion.conversation import dialogue_manager as dm
    # 每个情景两轮（首句 + 追问），所以上限是 N×情景×2，不是 N×情景：
    # 上一版文件名写成 n24（按 6×4 算），实际样本是 48 —— 分母标错比没测更坏，这里自报。
    print(f"探针上限：{N} 轮 × {len(CASES)} 情景 × 2 轮对话 = {N * len(CASES) * 2} 条回复"
          f"｜比对键集 {len(keys)} 道闸门", flush=True)
    for rep in range(N):
        for say, follow in CASES:
            turn = dm.handle_message(say, None)
            dm.handle_message(follow, turn["project_id"])
    print(f"样本：{n} 次回复，{calls} 次模型调用（平均 {calls / max(1, n):.2f} 次/回复）")
    print(f"规划阶段产出计划的比例：{plan_ok}/{n} = {100 * plan_ok / max(1, n):.0f}%")
    if fail_reasons:
        print("没产出计划的原因分布：")
        for why, cnt in fail_reasons.most_common(6):
            print(f"  {cnt:>2}× {why}")
    print(f"触发过重采样的回复：{resampled}/{n} = {100 * resampled / max(1, n):.0f}%"
          f"｜回复长度中位 {sorted(lengths)[len(lengths) // 2] if lengths else 0} 字")
    print("逐道闸门：违规率 闸门前 → 闸门后（闸门必须让违规变少，不能变多）")
    bad = 0
    for k in keys:
        a, b = pre[k], post[k]
        mark = ""
        if b > a:
            mark = "　✗ 变差"
            bad += 1
        print(f"  {k:<14} {a}/{n} = {100 * a / max(1, n):>3.0f}%  →  {b}/{n} = {100 * b / max(1, n):>3.0f}%{mark}")
    # 分母自证：`no_citation` 只对"当轮给了论文卡"的回复有资格开火，用全量分母印 0% 会被读成"不会误伤"。
    with_cards = sum(1 for c in cards if c)
    print(f"\n带论文卡的回复：{with_cards}/{n}｜空池 {n - with_cards} 条（闸门对空池按设计不开火，不是坏了）")
    fired_c = pre.get("no_citation", 0)
    if with_cards >= 8:
        print(f"闸门开火率（只在带卡回复上算）：no_citation {fired_c}/{with_cards} = "
              f"{100 * fired_c / with_cards:.0f}%　→ 分母够，这条闸门的代价可读")
    else:
        print(f"⚠️ 带卡回复只有 {with_cards} 条（<8）：no_citation {fired_c} 次开火**不能**读成"
              "「代价测过了」，也**不能**读成「不会误伤」——这台仪器仍然看不见它，预登记的判据在这里判「未测」。")
    print("结论：" + ("全部闸门都在减少违规" if not bad else f"{bad} 道闸门让违规变多了，先修它别看胜率"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
