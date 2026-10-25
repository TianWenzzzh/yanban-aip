"""对话状态机：DISCOVER → CLARIFY → FORMULATE → SEARCH → SCREEN → READ → REFINE → DELIVER"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    DISCOVER = "DISCOVER"
    CLARIFY = "CLARIFY"
    FORMULATE = "FORMULATE"
    SEARCH = "SEARCH"
    SCREEN = "SCREEN"
    READ = "READ"
    REFINE = "REFINE"
    DELIVER = "DELIVER"


# 合法转移表（dialogue_manager 用 transition() 走这张表，非法转移不改状态）
# 补齐了真实会发生的回退：筛选/精读之后可能要继续追问或换词再查；
# 再查一次要重新组织检索式，所以 DELIVER/REFINE 也能回到 FORMULATE。
# 学生随时可以要一份文档，所以还在追问/组织检索式时也能直接到 DELIVER。
TRANSITIONS: dict[State, list[State]] = {
    State.DISCOVER: [State.CLARIFY, State.FORMULATE],
    State.CLARIFY: [State.CLARIFY, State.FORMULATE, State.DELIVER],
    State.FORMULATE: [State.FORMULATE, State.SEARCH, State.DELIVER],
    State.SEARCH: [State.SCREEN, State.DELIVER, State.SEARCH],
    State.SCREEN: [State.READ, State.REFINE, State.DELIVER, State.CLARIFY, State.SEARCH],
    State.READ: [State.REFINE, State.DELIVER, State.CLARIFY, State.SEARCH],
    State.REFINE: [State.SEARCH, State.CLARIFY, State.DELIVER, State.FORMULATE],
    State.DELIVER: [State.CLARIFY, State.SEARCH, State.REFINE, State.FORMULATE, State.SCREEN],
}


def can_transition(cur: State, nxt: State) -> bool:
    return nxt in TRANSITIONS.get(cur, [])
