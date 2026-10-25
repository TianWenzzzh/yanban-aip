"""跟进卡在窄屏必须真的看得见（轮 167）——同一组件、两种摆位，但只有一份状态。"""
import os

os.environ["LLM_MOCK"] = "1"

import pathlib

CARD = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
        / "components" / "CommitmentCard.tsx").read_text(encoding="utf-8")
PAGE = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "src"
        / "pages" / "Companion.tsx").read_text(encoding="utf-8")


def test_card_supports_two_placements_with_one_state():
    assert 'variant = "panel"' in CARD and 'variant?: "panel" | "inline";' in CARD
    assert 'const shell = variant === "inline" ? "lg:hidden" : "hidden lg:block";' in CARD
    assert 'data-variant={variant}' in CARD
    assert PAGE.count("onAssumption={onAssumption}") == 2      # 侧栏一份 + 对话流一份
    assert "followupInline" in PAGE


def test_inline_copy_is_rendered_inside_the_message_flow():
    """摆位要对：内联那份必须在消息列表之后、进行中步骤之前，不能又塞进右栏。"""
    at = PAGE.index("{followupInline}")
    assert PAGE.index("{messages.map(") < at < PAGE.index("{busy && steps.length > 0")
    assert PAGE.count("{followupInline}") == 1
    assert PAGE.count("{progress}{followup}") == 4             # 四个面板仍各挂侧栏那份


def test_no_third_placement_and_no_duplicated_state():
    assert PAGE.count("<CommitmentCard") == 2
    assert PAGE.count("useState<Ledger | null>") == 1
    assert PAGE.count("if (d.commitments !== undefined) setLedger(d.commitments);") == 1
