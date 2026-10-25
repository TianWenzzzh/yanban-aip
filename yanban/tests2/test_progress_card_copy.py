"""钉住进度卡上两处**只有看图才发现**的文案缺陷（轮 153 欠的像素验收，本轮补看图时抓到）。

为什么落在源码上：这两条都不是版式，而是「卡片在说什么」——
① 已过线还印未来时：改前那张图上同时是「理解度 71%」「第 8 / 8 步」，右边却写
   「到 52% 就去查」。无障碍树与 SSE 契约读数都能对上（线确实是 0.52），只有人眼会读成
   「还没查」⇒ 这类矛盾机器读数看不见（轮 153 正是只看了无障碍树就宣布完成）。
② 开口前说「把下面这几格填上」，而它下面此时没有任何格子（格子＝缺项列表，只在 started 之后出现）。

判据写成 `problems(text)`，负对照直接喂**改前那份文本**：不这样配对，`problems` 可能恒返回空。
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src" / "components" / "ProgressCard.tsx"

# 过线与否必须由「理解度」与「线」比出来，不许在组件里抄线的数值
PAST_DERIVED = re.compile(r"\bconst past\s*=[^;\n]*confidence\s*>=\s*threshold")
# 改前的两态写法：threshold 为空/不空，没有「已经过了」这一态
TWO_STATE_LABEL = re.compile(r"threshold === null \?[^;\n]*就去查")
LITERAL_LINE = re.compile(r"\b0\.52\b|\b52%")


def problems(text: str) -> list[str]:
    """返回要印出来的违规句；空列表 = 通过。"""
    out: list[str] = []
    if not PAST_DERIVED.search(text):
        out.append("找不到 `const past = … confidence >= threshold`：过线与否必须由这两个数比出来"
                   "（组件里不许出现线的字面量，也不许只看 threshold 是否为空）")
    if TWO_STATE_LABEL.search(text):
        out.append("放行线仍是「够/不够」两态：已过线时那句未来时会被读成「还没查」")
    if "已过 ${pct(threshold)} 的线" not in text:
        out.append("缺「已过 N% 的线」这一支：过线之后卡上必须换个说法")
    if LITERAL_LINE.search(text):
        bad = [ln.strip()[:60] for ln in text.splitlines() if LITERAL_LINE.search(ln)]
        out.append(f"组件里抄了放行线的字面量（{bad}）⇒ 只能取 meta.ready_threshold")
    if "这几格" in text:
        out.append("开口前文案承诺「下面这几格」，而此刻卡上没有任何格子")
    return out


# 改前那份（轮 153 落地的形状），逐字摘自 git HEAD~ 的实现，用来证明判据会咬人
OLD_SHAPE = '''
  const started = confidence !== null;
  const lineState = threshold === null ? "信息够了，直接开检索" : `到 ${pct(threshold as number)} 就去查`;
          说一句你想研究的东西，哪怕很模糊。研伴一边接话一边把下面这几格填上，凑够了就去查论文。
'''


def test_progress_card_copy_has_no_stale_future_tense():
    found = problems(SRC.read_text(encoding="utf-8"))
    assert not found, "；".join(found)


def test_the_old_copy_would_have_been_caught():
    """负对照：改前那份必须被同一组判据判红，且三条各红在自己的那一条上。"""
    found = problems(OLD_SHAPE)
    assert any("两态" in p for p in found), f"旧文案没被抓住，problems 可能恒真：{found}"
    assert any("已过 N% 的线" in p for p in found), f"缺分支那条没开火：{found}"
    assert any("这几格" in p for p in found), f"假承诺格子那条没开火：{found}"


def test_past_flag_is_defined_exactly_once():
    """`past` 只许定义一次（重复定义在 TS 里直接编译失败，但这条判据顺手挡住改名式漂移）。"""
    text = SRC.read_text(encoding="utf-8")
    assert len(re.findall(r"\bconst past\s*=", text)) == 1, "past 应恰好定义一次"
    assert text.count("past") >= 2, "past 只有定义没有被引用 ⇒ 判据成了装饰"
