"""消息流的"跟随最新"语义（轮 175b）：只在学生本来就贴着底部时跟随，往上翻了给一颗跳回来的按钮。

为什么值得钉：流式输出期间每来一段字就 `scrollIntoView`，等于**不许回看**——
学生想核对上一句自己说过的话，会被一路拽回底部。这条改动没有渲染层以外的证据链，
所以判据落在源码上（与同目录其它前端判据一致：管的是"接线与语义"，真实滚动由 CDP 目视通道量）。
"""
import os
import pathlib
import re

os.environ["LLM_MOCK"] = "1"
TSX = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
       / "pages" / "Companion.tsx").read_text(encoding="utf-8")


def test_follow_is_conditional_not_unconditional():
    body = re.sub(r"/\*.*?\*/", "", TSX, flags=re.S)
    body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
    # 每次 messages/busy 变化都滚到底的那一行必须已经没了
    assert "useEffect(() => { bottomRef.current?.scrollIntoView" not in body
    assert re.search(r"useEffect\(\(\) => \{\s*if \(atBottomRef\.current\)", body)
    assert "onScroll={onLogScroll}" in body and "ref={logRef}" in body


def test_there_is_a_way_back_to_the_bottom():
    """往上翻了之后要看得见"怎么回来"，否则条件跟随只是把学生困在旧内容里。"""
    assert 'data-testid="jump-latest"' in TSX
    assert "onClick={jumpLatest}" in TSX
    # 点它必须同时做两件事：滚到底 + 把"贴着底部"的旗子立回来（否则下一段字又不再跟随）
    jump = TSX[TSX.index("const jumpLatest"):TSX.index("useEffect", TSX.index("const jumpLatest"))]
    assert "atBottomRef.current = true" in jump and "scrollIntoView" in jump


def test_gap_is_measured_on_whoever_actually_scrolls():
    """只量消息区会**静默失效**：这份布局里消息区的 clientHeight == scrollHeight（其实是整页在滚），
    于是它永远"贴着底"，那颗按钮一次都不会出现。CDP 实测读数：logOverflow=0 而 docOverflow=671。
    判据要钉的是"两处都问一遍 + 监听要摘掉"，不是"看起来接了 onScroll"。"""
    assert "document.documentElement" in TSX and "window.scrollY" in TSX
    assert 'window.addEventListener("scroll", onLogScroll' in TSX
    assert "return () => window.removeEventListener" in TSX
