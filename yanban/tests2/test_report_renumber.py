"""报告生成路径接上 `renumber_citations`（轮 155）。

依据：`g158_refine_yield_online.txt` 在线上 12 份真报告的回放下量到 26 句未接地里
**12 句的依据本来就在同一批论文集里**——只是角标挂错了篇。对话路径早就在调
`evidence.renumber_citations`（`mentor.py`），报告路径从来没调过。

这组测试锁四件事：
① 挂错篇的角标会被改指到撑得住的那一篇；
② 改了几处要如实记账（`citations_renumbered`），且必须是从产品生成入口出来的数；
③ 负向：一篇都撑不住的句子**不许**被改成"看起来有依据"，越界角标也不许被洗白成合法编号；
④ 整条报告链路里 `renumber_citations` 只许出现一处调用点（两处就会各自算一套）。
"""
from __future__ import annotations

import inspect
import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import llm
from companion.conversation import report
from companion.intent.model import ResearchIntent
from companion.literature import evidence

# 两篇摘要都刻意短于 80 字 ⇒ `_pool_has_text` 为假，补引用与反证两道闸门不开工。
# 这样 `citations_renumbered` 的读数是单因子的：只可能来自主生成路径那一次清洗。
P1 = {"id": "p1", "title": "Visual Phishing Detection with CLIP", "year": 2022,
      "abstract": "We embed URL strings with CLIP and train a classifier on 50000 pages."}
P2 = {"id": "p2", "title": "A Survey of Multi-Agent LLM Systems", "year": 2024,
      "abstract": "This survey reviews 120 papers on multi-agent LLM protocols."}
POOL = [P1, P2]

WRONG = "这篇用 CLIP 把 URL strings 编码后训练分类器，跑了 50000 个页面 [2]。"
ORPHAN = "这套方法已经在 200 家银行的风控流程里上线运行 [2]。"


def _skeleton(extra: str) -> str:
    return "\n\n".join(f"## {s}\n{extra if s == '方法对比' else '该节与 CLIP 编码 URL strings 的做法有关 [1]。'}\n"
                       for s in report.SECTIONS)


def test_the_fixture_keeps_the_backfill_gates_off():
    """前提自检：夹具若意外满足 `_pool_has_text`，下面那几条记账就不再是单因子的。"""
    assert not report._pool_has_text(POOL)
    assert evidence.renumber_citations(WRONG, POOL)[1] == 1


def test_clean_report_repoints_a_marker_to_the_paper_that_supports_it():
    out = report.clean_report(WRONG, POOL)
    assert "[1]" in out and "[2]" not in out, f"角标没被改指：{out}"
    rep = evidence.grounding_report(out, POOL)
    assert rep["misnumbered"] == 0 and rep["grounded"] == 1


def test_clean_report_ledger_says_how_many_markers_moved():
    text, moved = report.clean_report_with_ledger(WRONG + ORPHAN, POOL)
    assert moved == 1, f"只有一处挂错，记账却报了 {moved}"
    assert "[2]" in text, "撑不住的那句必须原样留着角标"
    same, none_moved = report.clean_report_with_ledger(ORPHAN, POOL)
    assert (same, none_moved) == (ORPHAN, 0), "无药可改时不许动文本，也不许虚报改动"


def test_clean_report_never_launders_an_out_of_range_marker():
    """[9] 是幻觉编号：它该被删掉，而不是被改指到某篇真论文上，那等于把假引用洗白。"""
    out = report.clean_report("这篇用 CLIP 编码 URL strings 与 50000 页面 [9]。", POOL)
    assert "[9]" not in out
    assert "[" not in out, f"越界角标被改成了合法编号：{out}"


def test_clean_report_survives_a_one_paper_pool_without_renumbering():
    """一篇时改指无从谈起（`renumber_citations` 要求 ≥2 篇），但清洗不能崩、也不能留假编号。

    第一版我把期望写成"文本一字不动"，是错的：单篇池里 [2] 本就越界，
    该被 `strip_dangling_citations` 剪掉——这条测试的真实对象是"不崩 + 不虚报改动"。
    """
    one, moved = report.clean_report_with_ledger(WRONG, [P1])
    assert moved == 0
    assert "[2]" not in one and "[" not in one


def test_generation_path_reports_the_fix_through_its_entry_point(tmp_path, monkeypatch):
    """走 `build_report`（不是直接调 `clean_report`）：线上进来的报告就是这条路。"""
    import companion.knowledge as knowledge

    calls: list[str] = []

    async def fake_stream(prompt, **kw):
        calls.append(prompt)
        for chunk in (_skeleton(WRONG)[:400], _skeleton(WRONG)[400:]):
            if chunk:
                yield chunk

    async def no_extra_chat(prompt, **kw):
        raise AssertionError("这条测试不该触发逐节重写：那会让记账变成多因子")

    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "chat", no_extra_chat)
    out = report.build_report("projRn", ResearchIntent(project_id="projRn", topic="钓鱼网站检测"), POOL)

    assert out["ok"] and out["degraded"] is False and len(calls) == 1
    assert out["citations_renumbered"] == 1, out
    body = out["body"]
    assert "跑了 50000 个页面 [1]" in body and "[2]" not in body
    assert evidence.grounding_report(body, POOL)["misnumbered"] == 0


def test_renumber_is_called_exactly_once_in_the_report_module():
    """两处调用点 = 两套口径：逐节清洗与整篇清洗各自挪角标，账就对不上了。"""
    src = inspect.getsource(report)
    assert src.count("renumber_citations(") == 1


def test_clean_report_is_the_only_wash_door_for_generated_text():
    """生成文本必须经 `clean_report` 出门：绕过它 = 绕过角标改指。"""
    for fn in (report._finalize, report.rewrite_section, report._cite_missing_sections,
               report._counter_evidence):
        body_src = inspect.getsource(fn)
        assert "clean_report" in body_src, f"{fn.__name__} 不再经过统一清洗入口"
