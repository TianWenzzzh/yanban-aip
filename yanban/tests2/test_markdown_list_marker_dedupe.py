# -*- coding: utf-8 -*-
"""轮 223 闸门：屏上「1. 1.」双重编号（未决 22 后半）——钉住产品里那一条尺子，并真跑它。

三件事缺一不可：
  T1 判据不许有第二份：正则只存在于 `webui/src/components/Markdown.tsx`，本用例**从源码里把它抠出来**再用；
  T2 接线要同时钉两处：调用点必须把文本过 `dedupeListMarkers` 再交给 marked（只钉定义＝摘掉调用点也绿）；
  T3 行为用产品自己的 marked 跑三格夹具：双重编号折叠成一个号、单个号不误伤、小数 `3.14` 不被吃掉。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))          # …/code/yanban/tests2
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # tests2 → yanban → code → 迁移包
MARKDOWN_TSX = os.path.join(ROOT, "code", "yanban", "webui", "src", "components", "Markdown.tsx")
RUNNER = os.path.join(ROOT, "输出", "scripts", "md_marker_runner.cjs")
WEBUI_PKG = os.path.join(ROOT, "code", "yanban", "webui", "package.json")

CASES = [
    # 名字 → 输入 markdown / 期望每个 <li> 的文本
    {"name": "double_numbered", "text": "以下是三条建议：\n\n1. 1. 查阅公开数据集列表\n2. 2. 解释选择原因",
     "expectLi": ["查阅公开数据集列表", "解释选择原因"]},
    {"name": "single_numbered", "text": "1. 查阅公开数据集列表\n2. 解释选择原因",
     "expectLi": ["查阅公开数据集列表", "解释选择原因"]},
    {"name": "decimal_not_eaten", "text": "2. 3.14 是圆周率的近似值",
     "expectLi": ["3.14 是圆周率的近似值"]},
    {"name": "bullet_then_number", "text": "- · 第一条建议",
     "expectLi": ["第一条建议"]},
]


def _src():
    return open(MARKDOWN_TSX, encoding="utf-8").read()


def _pattern():
    m = re.search(r"export const DUP_LIST_MARKER = /(.*)/([a-z]+);", _src())
    assert m, "Markdown.tsx 里找不到 DUP_LIST_MARKER 这条正则（判据被摘掉了？）"
    return m.group(1), m.group(2)


def test_the_dedupe_regex_lives_only_in_the_product_source():
    """T1：正则只在产品源码里有一份；本用例不复制它，只从源码抠。"""
    pattern, flags = _pattern()
    assert flags == "gm", f"DUP_LIST_MARKER 必须带 gm（逐行锚定），实际 {flags!r}"
    assert _src().count("DUP_LIST_MARKER") == 2, "定义一次 + 使用一次，多出来的就是第二份尺子"


def test_the_renderer_wires_dedupe_into_the_parse_call():
    """T2：接线同时钉「函数定义」与「parse 调用点」——只钉一处，摘掉另一处照样绿。"""
    src = _src()
    assert "export function dedupeListMarkers(text: string): string" in src
    assert re.search(r"marked\.parse\(\s*dedupeListMarkers\(\s*text\s*\)\s*\)", src), \
        "Markdown 的 parse 调用点没走 dedupeListMarkers ⇒ 双重编号会原样上屏"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_real_marked_pipeline_leaves_exactly_one_marker(case):
    """T3：拿产品自己的 marked 跑，屏上（=DOM 文本）每个列表项只剩一个号。"""
    pattern, flags = _pattern()
    spec = {"pattern": pattern, "flags": flags, "webui": WEBUI_PKG, "cases": [case]}
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "cases.json")
        json.dump(spec, open(f, "w", encoding="utf-8"))
        r = subprocess.run(["node", RUNNER, f], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"跑手没跑起来：{r.stdout[-300:]}{r.stderr[-300:]}"
    payload = json.loads(r.stdout)
    assert payload.get("ok"), payload.get("error")
    got = payload["out"][0]
    # 真正的缺陷形状：marked 把 "1. 1. X" 解析成 <li><ol><li>X</li></ol></li>——外层号 + 内层号
    # ＝屏上两个号（轮 219 看图所见）。所以判据钉「列表项里不许再套列表」，光看 li 文本会漏掉它。
    nested = re.search(r"<li>(?:(?!</li>).)*?<(?:ol|ul)[ >]", got["html"], re.S)
    assert not nested, f'{case["name"]}：列表项里套出了第二层列表（屏上会多一个号）\n{got["html"]}'
    assert got["lis"] == case["expectLi"], \
        f'{case["name"]}：期望每个 <li> 是 {case["expectLi"]}，实际 {got["lis"]}（markdown={got["deduped"]!r}）'
    assert not re.search(r"^\s*\d+[.)]\s+\d+[.)]\s", " ".join(got["lis"])), "行首还躺着两个号"
    assert re.search(r"<(ol|ul)[ >]", got["html"]), "夹具本该渲染成列表，却没出列表元素"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "--tb=short"]))
