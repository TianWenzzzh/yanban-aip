"""轮 151–152：`capture_topic` 的规则模板接得住哪些中文说法——句集在仓里（单一源），纯函数、零模型调用。

判据（跑前写死，RC 语义分开）：
  Z0 只读自证：`llm.chat_json/chat/chat_text` 上装计数桩，模型调用次数必须为 0，否则 RC=2。
     「这盏灯真的会红」由 `--plant-net` 那场当场证明（故意发一次调用 ⇒ 必须红在 Z0），不靠文件头声明——
     轮 145 就是栽在「零网络」只写在头上、实际每条都走了网络分支。
  T0 同一份夹具、两版源码各量一次：对照取 **BASE_REV 的字节** exec（不重写公式）。
  T1 覆盖：正例抽出 topic 非空且含指定关键词且形状合格的条数（改动后要涨）。
  T2 纯度：负例必须抽出空串——误捕就是把闲聊碎片送去检索（轮 147 注释里的线上事故正是这个形状）。
  T3 形状：抽出的 topic 不得含句读、不得以「方向是/对象是/选的是」开头或「这个方向/这块」结尾。
  T4 前后对照：覆盖必须涨、误捕必须不涨，任一不满足 RC=2。
     认路哨兵：对照份源码里不该出现现役新增的正则，出现就说明对照不是「改动前」。
  T5 未申报的漏网：任何一条正例没被规则通道接住、又不在夹具的 `LLM_ONLY` 名单里 ⇒ RC=2。
     这条防的是"悄悄放弃"——判给模型兜底通道是合法决定，但必须写在夹具里被 xfail(strict) 钉住，
     哪天接住了 XPASS 会红，逼人来销账。
  T6 候选模板代价表（决策量，不是闸门）：三条候选各自量「夹具覆盖增量 / 夹具负例误捕增量 /
     真实线上消息池新增抓取」，并把新增抓取**逐条打出来给人看**——垃圾只能眼看，机器只会数数。
     现役采纳的是 C3；C1/C2 的否决理由就是这张表上的数。

为什么基线要钉 rev 而不是取 HEAD：轮 151 一提交，`git show HEAD:companion/intent/extractor.py`
就含本轮新正则，"改动前"会跟着产物走。同一个量被核对动作自己推进，只能单向对账。
真实消息池取 `code/yanban/docs/evidence/` 里轮 98/114 冻结的线上 user_text——
那批句子不是为这次改动写的，所以"零新增误捕"在它上面才有意义（轮 152 的教训：
「熟悉 X」在 71 条真实消息上零开火，只是因为那池子里一条带「熟悉」的句子都没有）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "code" / "yanban"), str(ROOT / "code" / "yanban" / "tests2")]

import companion.intent.extractor as cur                       # noqa: E402
from fixtures_topic_corpus import LLM_ONLY, NEGATIVE, POSITIVE, shape_ok  # noqa: E402

# Z0 只读自证：本机这台仪器一次模型调用都不该发。轮 145 的教训是这句话**写在文件头不等于被盯着**——
# 当时那句「零网络」实测每条提问都走了 llm_topic 的网络分支。所以这里装计数桩，并留一个植入开关自证会红。
import llm as _llm                                             # noqa: E402

_CALLS = {"n": 0}


def _tripwire(name):
    def boom(*a, **kw):
        _CALLS["n"] += 1
        raise AssertionError(f"Z0 破：仪器调了 {_llm.__name__}.{name} ⇒ 这份读数不是零模型调用")
    return boom


for _fn in ("chat_json", "chat", "chat_text"):
    if hasattr(_llm, _fn):
        setattr(_llm, _fn, _tripwire(_fn))


BASE_REV_DEFAULT = "81988dff"     # 轮 150 收尾时的 HEAD = 轮 151/152 任何改动之前
POOL_FILES = ("g114_question_pool_full70.json", "g98_cite_pool.json")

# 候选模板：C3 已采纳进产品，C1/C2 被这张表上的数量否决（留在仪器里，下次有人想加回来会当场看到代价）
CANDIDATES: dict[str, str] = {
    "C1_做X结尾": r"(?<!怎么|如何)(?:在|正在)?做((?:[\w一-鿿]{2,14}))$",
    "C2_熟悉X": r"熟悉(?:一下|下)?((?:[\w一-鿿]{2,16}))",
    "C3_讲X的文章": r"讲((?:[\w一-鿿]{2,16}?))(?:的)?(?:文章|论文|综述|报告)",
}


def load_git(rev: str) -> tuple[dict, str]:
    """把某个 git 版本的 extractor.py 按字节 exec 出来当"改动前"的那台仪器（不重写公式）。"""
    import re
    import subprocess

    import llm
    from companion.intent.model import RESEARCH_STAGES
    out = subprocess.run(["git", "-C", str(ROOT / "code" / "yanban"), "show", f"{rev}:companion/intent/extractor.py"],
                         capture_output=True)
    if out.returncode:
        raise SystemExit(f"判读：取不到 {rev} 的 extractor.py ⇒ 没有对照（RC=2）")
    src = out.stdout.decode("utf-8")
    needle = "from .model import RESEARCH_STAGES"
    if src.count(needle) != 1:
        raise SystemExit("判读：相对 import 不是恰好 1 次 ⇒ 装载口径可疑（RC=2）")
    ns: dict = {"__name__": f"extractor_{rev}", "re": re, "llm": llm, "_RS": RESEARCH_STAGES}
    exec(compile(src.replace(needle, "RESEARCH_STAGES = globals().get('_RS')"), f"<{rev}>", "exec"), ns)
    return ns, src


def real_pool() -> list[str]:
    seen, out = set(), []
    for name in POOL_FILES:
        p = ROOT / "code" / "yanban" / "docs" / "evidence" / name
        for row in json.loads(p.read_text(encoding="utf-8")):
            t = (row.get("user_text") or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def with_extra(extra):
    """现役 capture_topic + 追加模板（追加在原表之后，先让老口径命中）。"""
    import re as _re

    pats = tuple(_re.compile(src) for src in extra)

    def run(text: str) -> str:
        saved = cur._TOPIC_PATTERNS
        cur._TOPIC_PATTERNS = tuple(list(saved) + list(pats))
        try:
            return cur.capture_topic(text)
        finally:
            cur._TOPIC_PATTERNS = saved
    return run


def cov(fn) -> int:
    return sum(1 for t, w in POSITIVE if w in fn(t) and shape_ok(fn(t)))


def fpn(fn) -> int:
    return sum(1 for t in NEGATIVE if fn(t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_REV_DEFAULT, help="「改动前」对照的 git rev（冻结值，不跟 HEAD 走）")
    ap.add_argument("--plant-net", action="store_true",
                    help="植入：故意发一次模型调用，用来证明 Z0 计数桩真的会红（期望 RC=2 红在 Z0）")
    a = ap.parse_args()
    rev = a.base
    if a.plant_net:
        try:
            _llm.chat_json("植入：这一次调用就该被 Z0 抓住")
        except AssertionError as e:
            print(f"Z0 开火：{e}")

    cur_src = (ROOT / "code" / "yanban" / "companion" / "intent" / "extractor.py").read_text(encoding="utf-8")
    before, before_src = load_git(rev)
    rc = 0
    for needle in ("_TOPIC_LINK", "调研(?:一下"):
        if needle in before_src:
            print(f"判读：git 取来的对照份（{rev}）里已有 {needle} ⇒ 对照不是「改动前」（RC=2）")
            return 2
        if needle not in cur_src:
            print(f"判读：现役文件里找不到 {needle} ⇒ 被测物不对（RC=2）")
            return 2

    hit, bad_shape, miss = 0, [], []
    for text, want in POSITIVE:
        got = cur.capture_topic(text)
        key_ok = bool(got) and want in got
        ok = key_ok and shape_ok(got)
        hit += ok
        if key_ok and not ok:
            bad_shape.append((text, got))
        if not ok:
            miss.append(text)
    f_after, f_before, h_before = fpn(cur.capture_topic), fpn(before["capture_topic"]), cov(before["capture_topic"])

    pool = real_pool()
    print(f"对照基线 rev={rev}（冻结值，可 --base 覆盖）｜真实消息池 {len(pool)} 条去重 user_text（{' + '.join(POOL_FILES)}）")
    print(f"句集：正例 {len(POSITIVE)} 条｜负例 {len(NEGATIVE)} 条（构造夹具，非真实分布）\n")
    print(f"T0/T4 同一份夹具、两版源码各量一次：")
    print(f"      覆盖 {h_before}/{len(POSITIVE)} → {hit}/{len(POSITIVE)}"
          f"｜负例误捕 {f_before}/{len(NEGATIVE)} → {f_after}/{len(NEGATIVE)}\n")

    for text, want in POSITIVE:
        got = cur.capture_topic(text)
        ok = bool(got) and want in got and shape_ok(got)
        flag = "✅" if ok else ("🟡 形状不合格" if want in got else ("⚠️ 已申报天花板" if text in LLM_ONLY else "❌ 未申报"))
        print(f"  {flag} 期望含{want!r:<12} 实抽={got!r:<26} {text[:32]}")
    for text in NEGATIVE:
        got = cur.capture_topic(text)
        print(f"  {'❌ 误捕' if got else '✅ 空'} 实抽={got!r:<24} {text[:32]}")

    print(f"\nT1 覆盖 {hit}/{len(POSITIVE)}｜T2 负例误捕 {f_after}/{len(NEGATIVE)}｜T3 带壳 {len(bad_shape)} 条"
          + "".join(f"\n    · {t[:26]} → {g!r}" for t, g in bad_shape))
    if f_after:
        print("\n判读：负例被误捕 ⇒ 不许用误捕换覆盖（RC=2）")
        rc = 2
    if f_after > f_before:
        print(f"判读：误捕 {f_before}→{f_after} 涨了 ⇒ 覆盖是拿纯度换的（RC=2）")
        rc = 2
    if hit <= h_before:
        print(f"判读：覆盖 {h_before}→{hit} 没涨 ⇒ 这轮改动没买到东西（RC=2）")
        rc = 2

    undeclared = [t for t in miss if t not in LLM_ONLY]
    stale = [t for t in LLM_ONLY if t not in miss]
    print(f"\nT5 未申报漏网 {len(undeclared)} 条｜天花板名单 {len(LLM_ONLY)} 条（其中失效申报 {len(stale)} 条）")
    for t in undeclared:
        print(f"    ❌ 没接住又没申报：{t}")
    for t in stale:
        print(f"    ❌ 申报了但规则通道已接住，该销账：{t}")
    if undeclared or stale:
        rc = 2

    print(f"\nT6 候选模板代价表（相对现役）｜「买回天花板」= 该候选能接住的已申报漏网正例条数，"
          f"「Δ未申报漏网」应为 0（有的话说明漏网不是申报问题）")
    print(f"      {'候选':<14} {'买回天花板':>7} {'Δ未申报漏网':>8} {'Δ负例误捕':>8} {'池新增抓取':>8}")
    base_pool = [cur.capture_topic(t) for t in pool]
    base_ceil = sum(1 for t in LLM_ONLY if cur.capture_topic(t))
    base_undecl = sum(1 for t, w in POSITIVE if t not in LLM_ONLY and w in cur.capture_topic(t) and shape_ok(cur.capture_topic(t)))
    for name, src in CANDIDATES.items():
        fn = with_extra([src])
        d_ceil = sum(1 for t in LLM_ONLY if fn(t)) - base_ceil
        d_undecl = sum(1 for t, w in POSITIVE if t not in LLM_ONLY and w in fn(t) and shape_ok(fn(t))) - base_undecl
        new_pool = [(t, fn(t)) for t, g in zip(pool, base_pool) if fn(t) and fn(t) != g]
        mark = "采纳" if name == "C3_讲X的文章" else "否决"
        print(f"  {name:<14} {d_ceil:>+7} {d_undecl:>+10} {fpn(fn) - f_after:>+8} {len(new_pool):>8}  ← {mark}")
        for t, g in new_pool:
            print(f"        新增抓取 topic={g!r:<24} {t[:40]}")
    print("      （池新增抓取要逐条目视：'项目了'/'环境' 这类不是研究主题，是模板开火的残渣。"
          "C2 在这里 0 处开火只是因为真实池里没有带「熟悉」的句子——它的代价在夹具负例那一列。）")

    if _CALLS["n"]:
        print(f"\n判读：Z0 破——仪器发了 {_CALLS['n']} 次模型调用 ⇒ 这份读数不是零模型调用（RC=2）")
        rc = 2
    else:
        print("\nZ0 只读自证：模型调用计数 0（桩装在 llm.chat_json/chat/chat_text 上，"
              "「会红」由 --plant-net 那场证明，不靠这句声明）")

    print(f"\n判读：{'通过' if rc == 0 else '不通过'}（RC={rc}）"
          f"覆盖 {h_before}→{hit}/{len(POSITIVE)}、误捕 {f_before}→{f_after}/{len(NEGATIVE)}、"
          f"带壳 {len(bad_shape)}、申报天花板 {len(LLM_ONLY)} 条")
    return rc


if __name__ == "__main__":
    sys.exit(main())
