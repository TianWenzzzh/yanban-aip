"""Tailwind 色板档位必须存在：用了 `primary-400` 而配置里没有 400，编译期不报错、运行时只是「没有样式」。

为什么盯这个（轮 137，看图抓的）：数据概览页六行横条只有第一行画得出来，其余五行是空轨道——
像素数是 `#2563eb 97.6%` vs `非背景 49.9% 且无一个蓝像素`。根因是 `bg-primary-300` 这个档位
在 `tailwind.config.js` 里不存在（色板只写了 50/100/200/500/600/700）。同一族还有 `primary-400`
被用 8 次：输入框焦点环、卡片 hover 边、以及聊天页那颗「正在工作」的动画点——**全都不显示**。
这类缺陷 tsc 与 vite build 都不报（类名只是字符串），测试也看不见，只有渲染出来才现形；
所以把它从「每次看图」升级成一条闸门。
"""
from __future__ import annotations

import pathlib
import re

WEBUI = pathlib.Path(__file__).resolve().parent.parent / "webui"
CONFIG = WEBUI / "tailwind.config.js"
SRC = WEBUI / "src"

# 同一个档位有两种写法，只认第一种就会漏掉 `index.css`——而轮 137 出事的焦点环正是写在 CSS 里的
HYPHEN = re.compile(r"\b([a-z]+)-(\d{2,3})\b")                    # 类名 bg-primary-300
DOTTED = re.compile(r"\bcolors\.([a-z]+)\.(\d{2,3})\b")           # theme("colors.primary.500")


def defined_steps(text: str) -> dict[str, set[str]]:
    """从 `theme.extend.colors` 里取每个自定义色板写了哪些档位。

    只认**最内层**的块（`[^{}]*` 不含嵌套）：否则 `colors: { primary: {…} }` 会被当成一个叫
    `colors` 的色板，档位从内层行里抠出来照样非空——于是「用了不存在的档位」永远查不到（本仓库
    第一版就是这么错的，靠下面那条解析断言才没变成一条恒真闸门）。
    """
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"(\w+):\s*\{([^{}]*)\}", text):
        name, body = m.group(1), m.group(2)
        steps = set(re.findall(r"^\s*(\d{2,3})\s*:", body, re.M))
        if len(steps) >= 2:                            # 少于两档的不算色板（避免误抓别的配置块）
            out.setdefault(name, set()).update(steps)
    return out


def used_tokens(files: list[pathlib.Path]) -> set[tuple[str, str]]:
    used = set()
    for f in files:
        for name, step in HYPHEN.findall(f.read_text(encoding="utf-8")):
            used.add((name, step))
        for name, step in DOTTED.findall(f.read_text(encoding="utf-8")):
            used.add((name, step))
    return used


def missing(used: set[tuple[str, str]], cfg: dict[str, set[str]]) -> set[tuple[str, str]]:
    """只查「这份配置自己声明过的色板」——其余（gray/red/…）走 Tailwind 默认色板，不归本文件管。"""
    return {(n, s) for (n, s) in used if n in cfg and s not in cfg[n]}


def test_webui_exists_and_config_parses():
    assert CONFIG.is_file(), f"找不到 {CONFIG}"
    cfg = defined_steps(CONFIG.read_text(encoding="utf-8"))
    assert "primary" in cfg and "600" in cfg["primary"], f"色板解析不对：{cfg.get('primary')}"


def test_every_used_palette_step_is_defined():
    files = sorted(p for p in SRC.rglob("*") if p.suffix in {".tsx", ".ts", ".css"})
    assert files, "webui/src 没采到文件，这条闸门会恒真"
    bad = missing(used_tokens(files), defined_steps(CONFIG.read_text(encoding="utf-8")))
    assert not bad, ("这些 Tailwind 档位在用但配置里没有，编译期不报错、页面上直接不显示："
                     + "、".join(f"{n}-{s}" for n, s in sorted(bad)))


def test_the_checker_rejects_a_planted_step():
    """正向对照：植入一个不存在的档位，判据必须点出来——否则上一条测试可能只是恒真。"""
    cfg = {"primary": {"500", "600"}}
    assert missing({("primary", "400"), ("primary", "600")}, cfg) == {("primary", "400")}
    assert missing({("primary", "500")}, cfg) == set()
    # 默认色板（gray 等）不在配置里声明 ⇒ 不归本闸门管，不许误报
    assert missing({("gray", "950")}, cfg) == set()


def test_the_parser_reads_palettes_not_the_wrapping_key():
    """解析器自己也要有植入用例：把 `colors` 当成色板名会让整条闸门恒真。"""
    cfg = defined_steps('colors: { primary: {\n 500: "#3b82f6",\n 600: "#2563eb",\n}, }')
    assert cfg == {"primary": {"500", "600"}}, cfg
    assert "colors" not in cfg


def test_dotted_theme_references_in_css_are_not_a_blind_spot():
    """`index.css` 用 `theme("colors.primary.500")` 点号写法引用档位，类名正则抓不到它。

    轮 137 那次「焦点环 / 加载点全不显示」的缺陷族正是 CSS 里的档位引用：只认连字符类名的话，
    这条闸门对 CSS 恒绿，下一次改 `index.css` 写错档位仍然只有看图才发现。
    植入用点号写法，必须被同一个 `missing()` 判出来；并确认真实 CSS 里确实存在这种引用
    （否则这条只是在打空气）。
    """
    planted = 'outline: 2px solid theme("colors.primary.900");'
    got = {("primary", s) for _, s in DOTTED.findall(planted)}
    assert got == {("primary", "900")}, f"点号写法没被抓到：{got}"
    assert missing(got, {"primary": {"500", "600"}}) == {("primary", "900")}
    css = (SRC / "index.css").read_text(encoding="utf-8")
    real = set(DOTTED.findall(css))
    assert any(n == "primary" for n, _ in real), \
        f"index.css 里没有 theme(\"colors.primary.…\") 点号引用 ⇒ 这条植入是打空气（采到 {sorted(real)}）"
    cfg = defined_steps(CONFIG.read_text(encoding="utf-8"))
    assert not missing(real, cfg), f"index.css 实际用了未定义档位：{sorted(missing(real, cfg))}"
