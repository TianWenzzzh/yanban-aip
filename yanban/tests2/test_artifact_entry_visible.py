"""交付面的发现性：单文件报告必须在**表头**有一个出口，不许只活在第四个 tab 里。

对标 WorkBuddy 那一类"交付可点击产物"的功能级差距，缺的不是产物本身
（`/artifact/{pid}`、RIS/BibTeX、知识库 zip 都已上线），而是**入口不显眼**：
改前那两个产物链接写在 `tab === "map"` 的分支里 ⇒ 用户得先点开右栏、再切到「导图·工具」
才看得见；窄屏下右栏整个收起，等于没有。这与轮 153 修掉的「四个面板全空」是同族问题。

判据写成 `problems(text)`，负对照直接喂**改前那份形状**（产物链接只在 tab 分支里）。
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "webui" / "src" / "pages" / "Companion.tsx"
HEADER = re.compile(r"<header\b.*?</header>", re.S)


def problems(text: str) -> list[str]:
    out: list[str] = []
    head = "".join(HEADER.findall(text))
    if not head:
        return ["找不到 <header> 区块 ⇒ 判据没锚到东西，这条要人看一眼"]
    if "/artifact/" not in head:
        out.append("表头没有产物出口：单文件报告只能从「导图·工具」tab 进去，"
                   "窄屏右栏收起时等于没有入口")
    m = re.search(r"href=\{`\$\{API\}/artifact/\$\{projectId\}`\}([^>]*)", head)
    if m:
        attrs = m.group(1)
        for need in ("target=\"_blank\"", "rel=\"noreferrer\""):
            if need not in attrs:
                out.append(f"表头产物链接缺 {need}：产物里带着外部标题与摘要，"
                           "新窗口打开必须切断 opener 引用")
    elif "/artifact/" in head:
        out.append("表头有 artifact 字样但链接不是 `${API}/artifact/${projectId}` 这个形状"
                   "⇒ 判据与实现脱节，要人判一次")
    if "/artifact/" in head and not re.search(r"projectId && \(\s*(?:\{\/\*[\s\S]*?\*\/\}\s*)?<a href=\{`\$\{API\}/artifact/", head):
        out.append("表头的产物出口没有按 projectId 收放（或被注释挤出了判定形状）："
                   "还没有项目时给一个会 404 的链接是假入口")
    return out


# 改前的形状（产物链接只在 map tab 的分支里），用来证明判据会咬人
OLD_SHAPE = """
        <header className="flex shrink-0 items-start justify-between gap-3">
          <div>研伴 · 认知层</div>
          <button onClick={() => setPanelOpen(v => !v)}>论文·知识库</button>
        </header>
          {tab === "map" && projectId && (
            <a href={`${API}/artifact/${projectId}`} target="_blank" rel="noreferrer">打开单文件报告</a>
          )}
"""


def test_artifact_entry_is_in_the_header():
    found = problems(SRC.read_text(encoding="utf-8"))
    assert not found, "；".join(found)


def test_the_tab_only_shape_would_have_been_caught():
    found = problems(OLD_SHAPE)
    assert any("表头没有产物出口" in p for p in found), f"改前形状没被抓到，problems 可能恒真：{found}"


def test_artifact_link_still_exists_inside_the_tab_too():
    """两处都要在：表头给发现性，tab 里给那句完整说明（断网可看、角标可点）。"""
    text = SRC.read_text(encoding="utf-8")
    assert text.count("/artifact/") >= 2, "表头加了出口不该把 tab 里那条说明挤掉"
