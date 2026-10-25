"""钉住「质量结构」那张图**不再把五个独立评分画成份额**。

为什么要机器盯：轮 142 看图才发现，`DataOverview.tsx` 的环形图按 `d.avg / total`
切五片、中心印「五维均分合计 38.0」——可这五个数是各自 0—10 的独立均分，
不是同一个整体的份额。饼图邀请出「引用规范占 21.6%」这种不成立的读法，
而五片全在 6.5—8.4 之间、角度几乎等分，真正的差异反而看不见（旁边那张条形图更清楚）。
测试全绿、tsc 全绿、vite 全绿，只有把页面截下来看才发现 ⇒ 这条判据必须落在源码上。

判据写成 `problems(text)`，负对照直接喂**改前那份表达式**：不这样配对，
`problems` 可能恒返回空（上面那条活用例就跟着恒真）。
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src" / "pages" / "DataOverview.tsx"

# 份额式画法：把某维的值除以「所有维之和」。五维是独立评分，这个除法没有意义。
SHARE_EXPR = re.compile(r"\bd\.avg\s*/\s*(?:total|sum)\b")
# 每维各自除以满分的常量（正解）
PER_DIM_MAX = re.compile(r"\bd\.avg\s*/\s*DIM_MAX\b")
# 印出来的「合计」必须带分母，否则 38.0 会被读成一个绝对分数
TOTAL_WITHOUT_DENOM = re.compile(r"合计(?!\s*\{[^}\n]*\}\s*/\s*\{)")


def problems(text: str) -> list[str]:
    """返回要印出来的违规句；空列表 = 通过。"""
    out = []
    if SHARE_EXPR.search(text):
        out.append("仍按 `d.avg / total`（份额）切图：五维是各自 0—满分的独立评分，"
                   "除以总和会邀请出「某维占 N%」这种不成立的读法 ⇒ 应除以 DIM_MAX")
    if not PER_DIM_MAX.search(text):
        out.append("找不到 `d.avg / DIM_MAX`：每维的弧长没有除以该维满分，图就没法读")
    if not re.search(r"\bconst DIM_MAX\s*=\s*\d+;", text):
        out.append("满分必须是单一常量 DIM_MAX（三处引用它），不要在弧长/文案里各抄一个字面量")
    bad = [ln for ln in text.splitlines() if "合计" in ln and TOTAL_WITHOUT_DENOM.search(ln)]
    if bad:
        out.append(f"印出来的「合计」没带分母（{len(bad)} 处）：读者会把 38.0 当成绝对分数 ⇒ "
                   f"要写成 合计 X / (满分 × 维数)")
    if "五维均分合计" in text:
        out.append("中心标题仍是「五维均分合计」：五个独立评分相加不等于一个可解释的量")
    return out


# 改前的画法（轮 142 看图抓到的那份），逐字摘自 git 里的旧实现，用来证明判据会咬人
OLD_SHAPE = """
  const total = dims.reduce((a, d) => a + d.avg, 0) || 1;
          const frac = d.avg / total;
          五维均分合计
        {total.toFixed(1)}
"""


def test_rubric_chart_is_not_a_share_of_total():
    text = SRC.read_text(encoding="utf-8")
    found = problems(text)
    assert not found, "；".join(found)


def test_the_old_share_shape_would_have_been_caught():
    """负对照：改前那份表达式必须被同一组判据判红，且红在说得出的那两条上。"""
    found = problems(OLD_SHAPE)
    assert any("份额" in p for p in found), f"旧画法没被判据抓住，problems 可能恒真：{found}"
    assert any("DIM_MAX" in p for p in found), f"旧画法没触发满分常量缺失：{found}"
    assert any("五维均分合计" in p for p in found), f"旧画法的中心标题没被抓：{found}"


def test_max_scale_is_a_single_source():
    """满分只许出现一次定义；弧长、aria-label、文案三处都引用常量。"""
    text = SRC.read_text(encoding="utf-8")
    assert len(re.findall(r"\bconst DIM_MAX\s*=", text)) == 1, "DIM_MAX 应恰好定义一次"
    assert text.count("DIM_MAX") >= 4, "DIM_MAX 只有定义没有引用 ⇒ 判据成了装饰"
