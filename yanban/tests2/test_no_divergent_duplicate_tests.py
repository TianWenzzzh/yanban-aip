"""同一文件里重名的 `test_*` 定义必须逐字节相同：挡住「改了那份却永远不跑」。

轮 154 实测：`tests2/test_dialogue_llm.py` 有 139 个 test 定义、只有 115 个唯一名字
⇒ 24 组重名。Python 的函数定义按名字覆盖，所以每组只有**后一份**会执行。
眼下这 24 组都逐字节相同（复制粘贴整段所致），所以没有读数被骗；
但下一次有人去改**前一份**（比如加一条断言），那条断言就永远不会跑，而采集数也不会长——
这正是「测试全绿但没人跑过」的形状，所以把它变成一条闸门。

判据：扫 `tests2/*.py`，同名 test 定义若 AST 转印不同 ⇒ 点名文件、函数名、两处行号。
负对照：把其中一份改一个字节，必须判红且红在正确的名字上。
"""
from __future__ import annotations

import ast
import collections
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent


def divergent(text: str, fname: str = "<str>") -> list[str]:
    """返回「同名但体不同」的人话清单；空 = 通过。"""
    out: list[str] = []
    tree = ast.parse(text)
    by: dict[str, list] = collections.defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            by[node.name].append(node)
    for name, nodes in by.items():
        if len(nodes) < 2:
            continue
        shapes = {ast.dump(n, include_attributes=False) for n in nodes}
        if len(shapes) > 1:
            lines = "、".join(str(n.lineno) for n in nodes)
            out.append(f"{fname}::{name} 有 {len(nodes)} 份定义且**内容不同**（行 {lines}）"
                       f"⇒ 只有最后一份会跑，前面那些断言永远不会执行")
    return out


def test_no_divergent_duplicate_tests_in_tests2():
    found: list[str] = []
    for f in sorted(TESTS.glob("*.py")):
        found += divergent(f.read_text(encoding="utf-8"), f.name)
    assert not found, "；".join(found)


def test_divergent_duplicate_is_caught():
    """负对照：两份同名测试只差一个字节也必须点名。"""
    src = ("def test_a():\n    assert 1\n\n"
           "def test_a():\n    assert 2\n")
    found = divergent(src, "fake.py")
    assert len(found) == 1 and "test_a" in found[0], f"没抓到分叉的重名测试：{found}"
    # 同名的两份完全相同（现状那 24 组）不算违规，只登记不判红
    assert divergent("def test_b():\n    assert 1\n\ndef test_b():\n    assert 1\n") == []
