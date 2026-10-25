# -*- coding: utf-8 -*-
"""轮 312 追加：年份窗必须是**按现年推导**的，不是一张写死 2026 的表。

为什么这一族值得单独立尺（实测，不是推测）：
`companion/intent/extractor.py` 的 `_YEAR_HINTS` 把「近三年」解析成**绝对**的 `(2023, 2026)`。
轮 312 之前伙伴入口根本不看这个窗（只把年份词从主题里剥掉），所以上界过期只是网页侧一句
"从 2023 年起"的口径；本轮把窗接到 `sources.multi_source_search` 之后，伙伴会**按窗丢弃**条目
（`lo <= y <= hi`）⇒ 到 2027 年，用户说「近三年」会被硬性排除掉 2027 当年的论文，
而且产物上写的是"年份 2023—2026"，看着像按用户的话筛过、其实筛的是去年那张表。

判据（一台只钉一件事）：
  W1 相对词解析出的窗**跟着现年走**（把"今天"钉成 2027 复算）——本轮动手前这一条是红的；
  W2 任何相对窗的上界不许早于现年（真实"今天"，永不自腐的不变式）；
  W3 用户**明写**的年份区间保持绝对，不许被顺手改成相对（反向对照）；
  W4 网页侧 `extract_update` 与伙伴侧 `year_range` 同源同值（不造第二把尺，队长 §1.3）；
  W5 偏移量一字不许改：窗宽逐值＝现年减偏移（本轮只换"怎么算"，不换"算几年"）。

用例不许连外网、不许花模型配额：W4 传的 intent 已带 topic（`extract_update` 里
`if not intent.topic` 那一支会调 `llm_topic`，那是模型调用）。
"""
from __future__ import annotations

import datetime
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from companion.intent import extractor                       # noqa: E402
from companion.intent.model import ResearchIntent            # noqa: E402

# 偏移量＝这张表历来讲的年数（近两年 2、近三年 3、近五年 5、最新/这两年 2）
RELATIVE = {"近两年": 2, "最近两年": 2, "近三年": 3, "近五年": 5, "最近五年": 5, "最新": 2, "这两年": 2}


def _today_year() -> int:
    return datetime.date.today().year


# ── W1：把"今天"钉成 2027，窗必须跟着走（动手前红：表里是写死的 2026）──────────
def test_W1_relative_windows_follow_the_current_year(monkeypatch):
    monkeypatch.setattr(extractor, "_today_year", lambda: 2027)
    assert extractor.year_range("检索钓鱼网页检测近三年的论文 3 篇") == (2024, 2027)
    assert extractor.year_range("想看近两年的综述") == (2025, 2027)
    assert extractor.year_range("近五年的多智能体协作文献") == (2022, 2027)
    assert extractor.year_range("最新的强化学习文献") == (2025, 2027)


# ── W2：不变式——相对窗的上界永远是现年（这一条不会随日期自腐）──────────────────
def test_W2_a_relative_window_never_ends_in_the_past():
    y = _today_year()
    for word, back in RELATIVE.items():
        win = extractor.year_range(f"关于钓鱼网页检测的{word}文献")
        assert win is not None, f"「{word}」没解析出窗"
        assert win[1] == y, f"「{word}」的上界是 {win[1]}，现年是 {y} ⇒ 窗过期了会把当年的新论文整批丢掉：{win}"
        assert win[0] == y - back, f"「{word}」的窗宽变了（应为 {back} 年偏移）：{win}"


# ── W3：反向对照——用户明写的区间是绝对值，不许被改成相对 ────────────────────────
def test_W3_an_explicit_year_range_stays_absolute(monkeypatch):
    monkeypatch.setattr(extractor, "_today_year", lambda: 2030)
    assert extractor.year_range("2024-2026 年的钓鱼网页检测文献") == (2024, 2026), \
        "用户点名的区间被换成相对窗＝替他改了条件"


# ── W4：网页侧与伙伴侧同源同值（只有一把尺）─────────────────────────────────────
def test_W4_the_web_side_update_uses_the_same_window(monkeypatch):
    monkeypatch.setattr(extractor, "_today_year", lambda: 2027)
    text = "检索钓鱼网页检测近三年的论文 3 篇"
    got = extractor.extract_update(text, ResearchIntent(project_id="p-r312-w4", topic="钓鱼网页检测"))
    assert got["paper_preferences.year_from"] == extractor.year_range(text)[0]
    assert got["paper_preferences.year_to"] == extractor.year_range(text)[1]


# ── W5：2026 年内逐值不许变（本轮只改"怎么算"，网页端行为不变）─────────────────
def test_W5_this_year_resolves_to_exactly_what_the_old_table_said():
    y = _today_year()
    if y != 2026:
        # 这一条钉的是"轮 312 动手前后逐值相同"，只对 2026 年成立的对照。
        # 年份一过就**必须**显式报出来，不许静默假绿（假绿的那一格正是它要防的）。
        raise AssertionError(f"这一条的对照基准是 2026 年（本轮认证时的读数），现年 {y} ⇒ 换基准再钉")
    assert extractor.year_range("近三年") == (2023, 2026)
    assert extractor.year_range("近两年") == (2024, 2026)
    assert extractor.year_range("近五年") == (2021, 2026)
    assert extractor.year_range("最新") == (2024, 2026)
