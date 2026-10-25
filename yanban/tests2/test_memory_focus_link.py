"""轮 193：从「研伴这轮参考了这条」跳到记忆面板里的同一行，锚点只许是库里那行 id。

未决第 4 条剩下的那一半：轮 192 把 `event_id` 放进了召回载荷（聊天行如实为 None），
但界面上点不过去。这一轮补跳转，判据跟着补。

「这条与那条是同一条」的身份判据已经有一条更强的在场：`test_memory_tag_single_source.py`
里那条「召回载荷的 event_id == 面板那行的 id」（走真库、走真函数），本文件不重复它，
只钉**界面这一侧**必须用它、不许自己另找对齐方式（最容易的错法是拿文本相等当身份）。

六条判据各自的来由：
① 聊天行（messages 表）没有事件 id ⇒ 跳转按钮必须在 `typeof m.event_id === "number"` 守卫里，
   不拿 0 或空串冒充一个能跳的锚点。
② 只设高亮 id 不切 tab ⇒ 用户看不见任何变化，等于没跳：`setMemFocus(` 与 `setTab("memory")`
   必须在同一个 onClick 里。
③ 挂载点必须把 `focusId` 传进面板，且面板 props 类型收它（少一侧就是静默失效）。
④ 高亮与滚动都按 `m.id === focusId` 判定；出现「按文本相等当焦点身份」的写法直接红。
⑤ 清单里没有这条时要如实说（「不在当前清单里」），且它必须是与「还没有记忆」不同的另一分支
   ——把"没找到"读成"没有记忆"就是拿空态冒充结论。
⑥ 这套逻辑只许住在这两个文件里（第三处出现 focusId = 又抄了一份，早晚漂移）。
"""
import os
import pathlib

os.environ["LLM_MOCK"] = "1"

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui" / "src"
CMP = (WEBUI / "pages" / "Companion.tsx").read_text(encoding="utf-8")
MEM = (WEBUI / "pages" / "MemoryPanel.tsx").read_text(encoding="utf-8")


def _onClick_block(src: str, marker: str) -> str:
    """取包含 marker 的那次 onClick 的源码片段（按括号配平，够不到别的按钮）。"""
    i = src.index(marker)
    start = src.rindex("onClick=", 0, i)
    depth, j = 0, src.index("{", start)
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    raise AssertionError("onClick 括号没配平：" + marker)


def test_source_files_were_actually_read():
    """防空转：两份文件真读到了，且本次改动的标志串在场（否则下面每条都是绿给空气看）。"""
    assert len(CMP) > 50000 and len(MEM) > 3000
    assert "看这一条" in CMP and "memory-focus" in MEM


def test_jump_button_requires_a_real_event_id():
    guard = 'typeof m.event_id === "number"'
    assert guard in CMP, "跳转按钮没有包在 event_id 守卫里：聊天行会被造出一个假锚点"
    block = CMP[CMP.index(guard):CMP.index(guard) + 600]
    assert "setMemFocus(" in block and "看这一条" in block
    assert "data-event-id={m.event_id ?? undefined}" in block, "跳转按钮没带库里的行 id ⇒ 界面判据只能 DOM 对 DOM"


def test_jump_also_switches_to_the_memory_tab():
    block = _onClick_block(CMP, "setMemFocus(")
    assert 'setTab("memory")' in block, f"只设了高亮没切 tab，用户看不见跳转结果：{block[:160]}"
    assert "setMemFocus(" in block and block.count("setMemFocus(") == 1


def test_mount_passes_focus_id_and_panel_declares_it():
    assert "focusId={memFocus}" in CMP, "挂载点没把落点传给面板"
    assert "focusId?: number | null" in MEM, "面板 props 没收 focusId"
    assert CMP.count("focusId={memFocus}") == 1 and MEM.count("focusId?: number | null") == 1


def test_focus_identity_is_the_row_id_not_the_text():
    assert MEM.count("m.id === focusId") >= 2, "高亮/滚动没都按行 id 判定"
    assert 'ref={m.id === focusId ? focusRef : null}' in MEM
    assert 'data-testid={m.id === focusId ? "memory-focus" : undefined}' in MEM
    # 反例：拿文本相等当身份（文本相同是巧合，「不再引用」就是按文本挡的）
    assert "m.text === focus" not in MEM and "focusText" not in MEM


def test_missing_target_says_so_instead_of_showing_empty_state():
    assert 'data-testid="memory-focus-missing"' in MEM
    i = MEM.index('data-testid="memory-focus-missing"')
    seg = MEM[i:i + 200]
    assert "不在当前清单" in seg, seg
    assert "typeof focusId === \"number\" && !focusHit" in MEM, "没找到时没有独立分支"
    assert "还没有记忆" in MEM, "空态文案被顺手删掉＝两件事挤进同一分支"


def test_focus_logic_lives_in_exactly_two_files():
    hits = sorted(p.name for p in (WEBUI / "pages").glob("*.tsx") if "focusId" in p.read_text(encoding="utf-8"))
    assert hits == ["Companion.tsx", "MemoryPanel.tsx"], hits


def test_the_46vh_cap_is_dropped_when_a_row_is_focused():
    """轮 205 看 390 档的图抓到的缺陷：清单自己是一个 `max-h-[46vh] overflow-y-auto` 的滚动盒，
    `scrollIntoView({block:"center"})` 只能把命中那一行摆进**这个盒子**的中间，而盒子本身在窄屏里
    有一半在视口之外 ⇒ 行排在靠后时，点了「看这一条」仍然看不见它。修法是命中这一趟不打帽子，
    让外层面板体（Companion.tsx:1061 那个 `min-h-0 flex-1 overflow-y-auto`）来当滚动者。
    判据锁的是「条件 + 极性 + 类名」这条关系，不是「max-h-[46vh] 这个串在不在」——
    只查在不在的话，把三元条件反过来写也照样绿。"""
    cap = "max-h-[46vh]"
    assert MEM.count(cap) == 1, f"这顶帽子出现 {MEM.count(cap)} 次（要恰好一处、且被条件罩着）"
    i = MEM.index(cap)
    head = MEM[max(0, i - 160):i]
    assert "focusHit" in head, "46vh 的帽子没被 focusHit 罩着 ⇒ 窄屏点了跳转还是看不见那一行（轮 205 未决 18）"
    seg = head[head.rindex("focusHit"):]
    assert "?" in seg and ":" in seg and seg.index("?") < seg.index(":"), \
        f"命中分支与未命中分支写反了（帽子该在 else 那一侧）：{seg[:140]}"
    assert 'className="max-h-[46vh]' not in MEM, "帽子退回无条件写法了"

