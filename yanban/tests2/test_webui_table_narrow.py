"""窄屏 markdown 小表格不许被压成「一字一行」：`.report-prose` 里的表格必须走定宽布局并给首列留宽。

判据出处（实测，不是拍的）：轮 277 只读量尺 `输出/scripts/ui_r277_table_fix_candidates.mjs` 在**现役构建**的
r247 夹具上逐格量「行盒数 == 字符数 且 字符数 > 1」——390 档 2 枚（表头「形状」2/2、正文「一句话」3/3），
1280 档 0 枚。六条候选里 C1（`table-layout:fixed`＋`width:100%`＋首列 `6em`）／C2（7em）／C4（`nowrap`）
两档四格全绿；C3（`min-width:max-content`）／C5（`display:block`＋`overflow-x`）／C6（`width:max-content`）
三条声明都落地、也被 CSSOM 解析，却仍修不掉 390 那一格。读数
`输出/_logs/r277_s1_fix_candidates.log`＋`输出/_scratch/r277_fix_candidates.json`。

为什么取 C1 不取 C4：C4 让首列按内容全长撑开（夹具 3 字才 65.5px），首列一旦放长文本就有撑破视口的风险
——这一条是**推断**（夹具没有那个形状），不是实测。为什么取 6em 不取 7em：两者都把第二列从 3 行推到 4/5 行，
7em 只是多占 13.5px 换来第二列多一行，没有任何一格变好（实测）。

本闸门守的是源码层（别再退回"没有定宽"的写法＋作用域不许越出 `.report-prose`）；屏面那把尺在它自己名下，
同一把判据两侧共用会一起瞎（§0.10），所以这里只钉 CSS 声明的形状，不复制屏面读数。
"""
from __future__ import annotations

import pathlib
import re

WEBUI = pathlib.Path(__file__).resolve().parent.parent / "webui"
CSS = WEBUI / "src" / "index.css"

TABLE_SEL = ".report-prose table"
FIRST_COL_KEYS = (".report-prose th:first-child, .report-prose td:first-child",)


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def rules(text: str) -> list[tuple[str, dict[str, str]]]:
    """扁平 CSS 里每条规则：(规范化选择器文本, {属性: 值})。

    声明按属性名取值 ⇒ 「换序／多空一格」这类等价改写不会改变判定（下面 test 4 就是钉这件事的）。
    """
    out: list[tuple[str, dict[str, str]]] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", strip_comments(text)):
        sel = " ".join(m.group(1).split())
        decls: dict[str, str] = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                k, _, v = d.partition(":")
                decls[" ".join(k.split()).lower()] = " ".join(v.split()).lower()
        out.append((sel, decls))
    return out


def one_rule(rs: list[tuple[str, dict[str, str]]], sel: str, why: str):
    hits = [r for r in rs if r[0] == sel]
    assert len(hits) == 1, f"{sel} 应恰好命中 1 条规则（{why}），实得 {len(hits)} 条：{[r[0] for r in rs if sel in r[0]]}"
    return hits[0][1]


def test_css_parses_and_is_not_hollow():
    """解析自检：读不到足够多的规则 ⇒ 后面几条全是空转恒绿。"""
    rs = rules(CSS.read_text(encoding="utf-8"))
    assert len(rs) > 25, f"index.css 只解析出 {len(rs)} 条规则 ⇒ 这把尺没接上文件，判红"
    assert any(sel == ".report-prose p" for sel, _ in rs), "解析器看不见 .report-prose 家族 ⇒ 选择器归一化有误"


def test_prose_table_uses_fixed_layout_and_full_width():
    """390 档那 2 枚一字一行的直接成因是自动布局把首列挤到 47px（实测）；定宽才拦得住。"""
    decls = one_rule(rules(CSS.read_text(encoding="utf-8")), TABLE_SEL, "本轮修的表格规则")
    assert decls.get("table-layout") == "fixed", f"table-layout 必须是 fixed，实得 {decls.get('table-layout')!r}"
    assert decls.get("width") == "100%", f"width 必须是 100%（fixed 布局的表宽由此而来），实得 {decls.get('width')!r}"


def test_first_column_has_the_measured_width():
    decls = one_rule(rules(CSS.read_text(encoding="utf-8")), FIRST_COL_KEYS[0], "本轮加的首列宽度规则")
    assert decls.get("width") == "6em", (
        f"首列宽度必须是量到的 6em（81px＝容 4 个 13.5px 汉字；7em 实测只让第二列多一行），实得 {decls.get('width')!r} "
        f"⇒ 要改这个数就得重新跑量尺并换判据")


def test_the_fix_is_scoped_to_report_prose():
    """作用域判据必须**读盘上的选择器**：首跑时这条只检查写在测试里的两个常量串，P2 植入（把首列选择器的
    `.report-prose` 去掉）实测它不红 ⇒ 那是恒真装饰（§0.7 要的就是这种时候红）。改成先用「带本轮声明的规则」
    找回盘上那两条，再验它们的选择器逐个都在 `.report-prose` 作用域内。
    越界成裸 `table` 会连 Companion.tsx 那张结果表一起吃掉（轮 276 的 J3 注入就是裸选择器，比产品修法宽）。
    第二版曾写「承载规则必须恰好 2 条」，P1／P3 植入实测它把「声明在不在」也吞进来判红了（两件事耦在一起，
    归因就分不开）⇒ 现在只要求**至少找回一条**承载规则的集合（空集判红），逐条验它的作用域。"""
    guards = [(sel, d) for sel, d in rules(CSS.read_text(encoding="utf-8"))
              if d.get("table-layout") == "fixed" or d.get("width") == "6em"]
    assert guards, "盘上一条承载本轮修法的规则都没找回 ⇒ 作用域判据无从可判（不许空集放过）"
    for sel, _ in guards:
        for part in (p.strip() for p in sel.split(",")):
            assert part.startswith(".report-prose"), f"选择器 {part!r} 越出 .report-prose ⇒ 会命中非 markdown 的表格"


def test_equivalent_rewrite_still_passes():
    """把两条声明换序、空白改掉：语义没变 ⇒ 闸门不该红。字面串必须匹配不上，否则这条对照是假绿。"""
    original = CSS.read_text(encoding="utf-8")
    rewritten = original.replace(TABLE_SEL + " {", TABLE_SEL + " {  ").replace(
        "border-collapse: collapse; margin: 12px 0; table-layout: fixed; width: 100%;",
        "width : 100% ; border-collapse:collapse;\n  margin: 12px 0 ; table-layout:fixed;")
    assert rewritten != original, "等价改写没落到文本上 ⇒ 这条对照是打空气"
    assert "table-layout: fixed; width: 100%;" not in rewritten, "改写后仍留着原字面串 ⇒ 没换成等价形状"
    rs = rules(rewritten)
    t = one_rule(rs, TABLE_SEL, "等价改写份")
    assert t.get("table-layout") == "fixed" and t.get("width") == "100%", f"等价改写被判红：{t}"
    f = one_rule(rs, FIRST_COL_KEYS[0], "等价改写份的首列")
    assert f.get("width") == "6em", f"等价改写把首列读丢了：{f}"
