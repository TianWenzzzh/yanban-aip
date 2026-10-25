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

# 自己往容器里落一份输出：上一轮 g90 就是靠 SSH 通道回传，通道一断，一小时的重采样全部丢掉
# （05:30 起本地文件停在 48 字节，而容器里进程一直在跑）。有了这份边车文件，
# `remote_sh.py "docker exec yanban-leader-1 cat /tmp/probe_out.txt" --host` 随时能把读数捞回来。
_TEE = os.environ.get("PROBE_TEE") or ("/tmp/probe_out.txt" if os.name == "posix" else None)
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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

CASES = [
    ("我在准备毕业论文，题目是医学图像分割，实验室只有两块 3080，怕算力不够", "不太会调参"),
    ("我已经读了二十来篇多智能体协作的论文，想整理成综述，但不知道按什么线组织", "导师想让我投 CCF-B"),
    ("我只想把实验数据的相关性分析跑通，不确定用什么统计方法，别给我讲太多理论", "变量 12 个，样本 87 份，多数不是正态"),
    ("我第一次做科研，想做多模态钓鱼网站检测，深度学习只学过一点", "只有一台轻薄本，没有 GPU，数据自己爬"),
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
    # 逐样本即时打印：这支探针原来只在最后一次性输出，几小时的跑法中途看不出
    # "卡住了"还是"在跑"，也看不见失败/跳过计数（长跑前的只读体检因此无从下手）。
    fired = ",".join(k for k in keys if v_pre[k]) or "无"
    left = ",".join(k for k in keys if v_post[k]) or "无"
    print(f"[{n}] 长度 {len(out):>4d}｜闸门前 {fired}｜闸门后 {left}"
          f"｜计划={'有' if kw.get('plan') else '无'}", flush=True)
    return out


mentor.chat = spy_chat
mentor.repair = spy_repair


def main() -> int:
    if getattr(llm, "LLM_MOCK", False):
        print("LLM_MOCK=1：闸门产量必须在真模型上测，这里不出数。")
        return 3
    from companion.conversation import dialogue_manager as dm
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
    print("结论：" + ("全部闸门都在减少违规" if not bad else f"{bad} 道闸门让违规变多了，先修它别看胜率"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
