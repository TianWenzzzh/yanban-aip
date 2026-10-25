"""钉住看图仪器 `tools/rendercheck/` 的字节。

这些是「凡是渲染给人看的东西，必须亲眼看图」那条要求的量具：右边界、浅/深色、按区域取色、
切片、PDF 转图。它们原来只在迁移包 `输出/scripts/` 里，而那个目录不在任何 git 仓里 ——
尺子被改了、删了都不会留痕（轮 137 批准后纳入本仓）。

为什么用测试钉而不是写在注释里：一轮读数写成文档之后，读数和尺子就分家了。
`png_theme.py` 的判线（`MIN_SHARE = 0.8`）被改松，历史上那些"浅色占 94.8% / 深色占 xx%"
就再也复现不出来，而文件里的数字看着一切正常。

改仪器的正确姿势：改完就更新这里的表，并在 `docs/ITERATION_LOG.md` 说明哪一轮读数受影响。
"""
from __future__ import annotations

import hashlib
import pathlib

DIR = pathlib.Path(__file__).resolve().parents[1] / "tools" / "rendercheck"

PINNED = {
    "pdf_to_png.py": "b98fffc9e61af3cd97aad9dba84c48e443f8886fc8e6330dc60b064b9e1a5aaa",
    "png_colors.py": "baa60fad92f86b41d741072d2865fcd38fb59a599e7d6e20371f346122d88fff",
    "png_crop.py": "a80215cc90476e2aa052e82b11d07cec2914767ccc9ab97546a41eac0d5d46c3",
    "png_right_edge.py": "4610a1117947c4115754370e468932afa0908e245f07a6dab19bfa01bf741a3b",
    "png_theme.py": "902c5c56a3e8708f4c0891dcf20ec3c963c63b6f72d19f67bc11b84b054fa695",
}


def mismatches(pinned: dict[str, str]) -> list[str]:
    """钉住的值与磁盘字节不一致（或缺文件）的那些名字。抽成函数是为了让植入用例真的喂它一份错表。"""
    bad = []
    for name, want in pinned.items():
        path = DIR / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            bad.append(name)
    return bad


def test_every_render_instrument_matches_its_pinned_bytes():
    assert not mismatches(PINNED), f"这些仪器与钉住的字节不一致：{mismatches(PINNED)}"


def test_the_check_fires_on_a_planted_instrument():
    planted = dict(PINNED)
    victim = next(iter(planted))
    planted[victim] = "0" * 64                       # 差一个字符都不许过
    assert mismatches(planted) == [victim], "改一个哈希都不红 ⇒ 这条门是装饰"
    assert mismatches({}) == [], \
        "空表也会红的话，下一条（孤儿检查）就没必要存在了 —— 这里钉的是「本函数只查登记过的」"


def test_no_unpinned_instrument_sits_in_rendercheck():
    """防"仪器在但没人钉"：mismatches 只遍历 PINNED，新丢进来的尺子对它完全隐形。"""
    on_disk = {p.name for p in DIR.glob("*.py")}
    assert on_disk, "tools/rendercheck 里没有 .py：空集合会让下面这行恒真，先确认仪器还在不在"
    orphans = sorted(on_disk - set(PINNED))
    assert not orphans, f"这些仪器没钉字节（改了不会有人知道）：{orphans}"
