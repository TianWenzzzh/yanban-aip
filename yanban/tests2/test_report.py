"""一键综述报告（轮 20）：硬闸门、知识库落盘、双链回指与 API。"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
import llm
from companion.conversation import mentor, report
from companion.intent.model import ResearchIntent
from companion.memory import paper_memory

PAPERS = [
    {"id": "p1", "title": "Visual Phishing Detection with CLIP", "year": 2022, "doi": "10.1/x",
     "abstract": "We use CLIP to embed URLs and train a classifier on 50000 phishing pages."},
    {"id": "p2", "title": "A Survey of Multi-Agent LLM Systems", "year": 2024,
     "abstract": "This survey reviews 120 papers on multi-agent LLM systems."},
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


def _intent() -> ResearchIntent:
    return ResearchIntent(project_id="projX", topic="多模态钓鱼检测")


PAPERS2 = [{"title": "Graph anomaly survey",
            "abstract": "This survey reviews graph anomaly detection methods on large attributed "
                        "networks and benchmarks them over 40 datasets with unified protocols."},
           {"title": "Contrastive anomaly detection",
            "abstract": "We contrast normal and abnormal subgraphs and report state-of-the-art "
                        "recall on four public graph anomaly benchmarks."}]
BODY2 = ("## 研究背景与问题\n这一节讲的是背景，没有任何出处。\n\n"
         "## 方法对比\ngraph anomaly detection 覆盖 40 datasets [1]。\n")


def test_cite_gate_wont_fabricate_citations_against_an_empty_pool(monkeypatch):
    """库里没有摘要时闸门必须收手：没有原文可对，"补引用"就是逼模型编角标。"""
    import llm
    from companion.conversation import report

    async def boom(prompt, **kw):
        raise AssertionError("池子里没原文就不该再调模型")

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", boom)
    no_abs = [{"title": "Graph anomaly survey", "abstract": ""}]
    body, fixed = report._cite_missing_sections(None, no_abs, BODY2)
    assert fixed == [] and body == BODY2


def test_cite_gate_skips_llm_in_mock_mode(monkeypatch):
    """降级路径不花钱也不改内容：MOCK 下闸门必须原样返回。"""
    import llm
    from companion.conversation import report

    def boom(*a, **k):
        raise AssertionError("MOCK 模式不该调用模型")

    monkeypatch.setattr(llm, "LLM_MOCK", True)
    monkeypatch.setattr(llm, "chat", boom)
    body, fixed = report._cite_missing_sections(None, PAPERS2, BODY2)
    assert body == BODY2 and fixed == []


def test_cite_gate_replaces_only_sections_without_citations(monkeypatch):
    """没挂引用的节被单独重写；已经挂了角标的那节一个字都不许动。"""
    import llm
    from companion.conversation import report

    seen = {}

    async def fake_chat(prompt, **kw):
        seen["prompt"] = prompt
        return "graph anomaly detection 覆盖 40 datasets [1]。"

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", fake_chat)
    body, fixed = report._cite_missing_sections(None, PAPERS2, BODY2)
    assert fixed == ["研究背景与问题"]
    assert "[1]" in body.split("## 方法对比")[0]
    assert body.split("## 方法对比")[1].count("[1]") == 1, "已合格的节不该被重写第二遍"
    assert "研究背景与问题" in seen["prompt"]


def test_cite_gate_refuses_to_trust_the_model(monkeypatch):
    """模型说补了但接地判定还是 0 → 不许记成已修好（闸门要测它会不会假绿）。"""

    import llm
    from companion.conversation import report

    async def fake_chat(prompt, **kw):
        return "这一节仍然是一句没有出处的话。"

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", fake_chat)
    body, fixed = report._cite_missing_sections(None, PAPERS2, BODY2)
    assert fixed == []
    assert "没有任何出处" in body, "补不上就保留原文，不能把没做到的事记成做完了"


def test_report_generation_path_actually_runs_the_cite_gate(tmp_path, monkeypatch):
    """🔴 线上回归：闸门只挂在 `_finalize` 的非降级分支上，而那条分支在 MOCK 测试里永远早退。

    结果 `intent` 没传进 `_finalize` 的 NameError 把线上 `/report` 打成 500，G1 却全绿。
    所以这条测试走**完整的 `build_report` 产品路径**（不是直接调私有函数），
    并断言模型真的被叫去补引用、补上的节如实记进 `sections_cited`。
    """
    import llm
    from companion.conversation import report
    from companion.intent.model import ResearchIntent

    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    calls = []
    skeleton = "\n\n".join(f"## {s}\n这一节讲的是背景，没有任何出处，需要先补上可核对的依据。"
                           for s in report.SECTIONS)

    async def fake(prompt, **kw):
        if "这一节现在的样子" in prompt:          # 逐节重写的提示词
            calls.append(prompt)
            return "graph anomaly detection 覆盖 40 datasets [1]。"
        return skeleton                          # 兜底：万一有人把生成改回非流式

    async def fake_stream(prompt, **kw):         # 整篇综述走流式，必须一起替身
        for chunk in (skeleton[:300], skeleton[300:]):
            if chunk:
                yield chunk

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", fake)
    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    out = report.build_report("projGate", ResearchIntent(project_id="projGate", topic="图异常检测综述"),
                              PAPERS2)
    assert out["ok"] and out["degraded"] is False
    assert len(calls) == 2, "非降级路径必须真的叫模型补引用（最多 2 节），不许静默跳过"
    assert out["sections_cited"], "补上的节要如实上报，不能只改正文不留痕迹"
    for head in out["sections_cited"]:
        assert "[1]" in dict(report.split_sections(out["body"]))[head]


def test_report_prompt_requires_a_citation_per_section():
    """综述提示词必须明确要求每节挂角标——实测 12 节里只有 2 节有引用（轮 101）。

    这条断言不是为了锁文案，是因为「节里没有引用」是呈现层的诚信问题，
    而它的成因就是提示词压根没提过角标。
    """
    from companion.conversation import report
    from companion.intent.model import ResearchIntent

    prompt = report.build_prompt(ResearchIntent(project_id="p", topic="多智能体协作综述"),
                                 [{"title": "A paper", "abstract": "abstract text here"}])
    assert "每节至少挂一条" in prompt and "[编号]" in prompt
    assert "只能用这些" in prompt


def test_clean_report_drops_invented_titles_and_out_of_range_anchors():
    text = "这篇工作用了 CLIP [1]。《不存在的论文标题》非常厉害 [2]。顺带提一下 [9]。"
    out = report.clean_report(text, PAPERS)
    assert "不存在的论文标题" not in out          # 整句剔除（宁可少说，不可编造）
    assert "[1]" in out                          # 范围内的角标保留，能跳到论文卡
    assert "[9]" not in out                      # 只有 2 篇，越界角标是幻觉


def test_system_prompt_forbids_invention_and_fixes_anchor_format():
    assert "禁止编造" in report.SYSTEM and "[n]" in report.SYSTEM
    assert "不要 emoji" in report.SYSTEM


def test_mock_report_is_degraded_marked_and_links_every_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    out = report.build_report("projX", _intent(), PAPERS)
    assert out["ok"] and out["degraded"] and out["papers"] == 2
    assert out["body"].startswith(mentor.DEGRADE_PREFIX)
    for p in PAPERS:
        assert f"[[论文：{p['title']}]]" in out["body"]
    assert set(report.SECTIONS) <= set(out["sections"])


def test_report_lands_in_vault_and_paper_note_backlinks_it(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    out = report.build_report("projX", _intent(), PAPERS)
    assert knowledge.get_note("projX", out["title"]) is not None
    assert out["title"] in knowledge.backlinks("projX", f"论文：{PAPERS[0]['title']}")
    assert pathlib.Path(tmp_path / "kb" / "projX" / f"{out['note_name']}.md").exists()


def test_report_without_papers_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    out = report.build_report("projEmpty", _intent(), [])
    assert out["ok"] is False and "论文" in out["note"]


def test_prompt_carries_matrix_block_and_section_contract():
    matrix = {"columns": [("method", "方法"), ("limitation", "局限")],
              "rows": [{"title": PAPERS[0]["title"], "cells": {"method": "用 CLIP 嵌入 URL", "limitation": "未知"}}]}
    prompt = report.build_prompt(_intent(), PAPERS, matrix)
    assert "跨论文对比表" in prompt and "用 CLIP 嵌入 URL" in prompt
    assert "方法对比" in prompt and "研究空白" in prompt
    assert PAPERS[1]["title"] in prompt            # 论文原文一起进上下文
    assert report.matrix_block(None) == ""


def test_normalize_sections_renames_near_miss_and_reports_missing():
    body = ("## 研究背景与问题\n钓鱼网站…\n\n## 工作分类\n两类…\n\n"
            "## 方法对比\n差异…\n\n## 引用论文\n- [[论文：X]]")
    out, missing = report.normalize_sections(body)
    assert "## 相关工作分类" in out                      # 模型简写的小节名被改回契约名
    assert missing == ["研究空白与可切入方向", "下一步建议"]  # 真缺的两节如实上报
    assert "重写这节" in out                              # 占位句指向上真有的那个按钮
    assert "需要补" not in out                             # 「这节没写出来，需要补」是内部话术
    assert "## 引用论文" in out                          # 非契约小节不动


def test_normalize_sections_reorders_and_never_duplicates_a_head():
    """`g74` 情景 2 复现：模型把「相关工作分类」写成「工作相关分类」，子串认不回，

    旧版于是同时留着跑偏节 + 补一节同名占位——学生在一份报告里看到两个相近小节。
    另一半要钉的是：契约名与跑偏名同时在正文里时宁可不改，也不许改名改出两个同名节。
    """
    body = "## 工作相关分类\n两类…\n\n## 方法对比\n差异…\n"
    out, missing = report.normalize_sections(body)
    assert "## 相关工作分类" in out and "工作相关分类" not in out
    assert missing == ["研究背景与问题", "研究空白与可切入方向", "下一步建议"]

    both = "## 相关工作分类\n正的…\n\n## 工作相关分类\n又写了一遍…\n"
    out2, _ = report.normalize_sections(both)
    assert out2.count("## 相关工作分类") == 1             # 不许造出第二个同名节
    assert "又写了一遍" in out2                            # 正文一个字都不丢


def test_report_endpoint_requires_papers(client):
    pid = client.post("/companion/project", json={"name": "空报告"}).json()["project_id"]
    r = client.post(f"/companion/report/{pid}")
    assert r.status_code == 400 and "论文" in r.json()["detail"]


def test_report_endpoint_writes_note(client):
    pid = client.post("/companion/project", json={"name": "综述"}).json()["project_id"]
    client.post("/companion/chat", json={"user_text": "我想做多模态钓鱼网站检测，第一次做科研", "project_id": pid})
    paper_memory.save_many(pid, PAPERS)
    r = client.post(f"/companion/report/{pid}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"].startswith("综述：") and data["degraded"] is True
    assert data["used_matrix"] is False            # 没先跑对比表 → 如实报告没用上
    assert knowledge.get_note(pid, data["title"]) is not None


def test_report_endpoint_reuses_cached_matrix_without_new_llm_call(client, tmp_path, monkeypatch):
    from companion.literature import matrix
    monkeypatch.setattr(matrix, "MATRIX_DIR", tmp_path / "matrix")
    pid = client.post("/companion/project", json={"name": "综述+表"}).json()["project_id"]
    client.post("/companion/chat", json={"user_text": "我想做多模态钓鱼网站检测", "project_id": pid})
    paper_memory.save_many(pid, PAPERS)
    assert matrix.build_matrix(pid, PAPERS)["rows"]      # 先建表（MOCK → 全「未知」，但已入缓存）
    r = client.post(f"/companion/report/{pid}")
    assert r.status_code == 200 and r.json()["used_matrix"] is True


def test_report_endpoint_404_for_unknown_project(client):
    assert client.post("/companion/report/does-not-exist").status_code == 404


# ── 逐节重写（轮 25）──────────────────────────────────────────────────
def test_split_and_join_roundtrip():
    body = "前言\n\n## 方法对比\nA段\n\n## 引用论文\n- x\n"
    secs = report.split_sections(body)
    assert [h for h, _ in secs] == ["", "方法对比", "引用论文"]
    assert report.join_sections(secs) == body


def _use_real_llm(monkeypatch, reply):
    async def fake(prompt, **kw):
        fake.prompt = prompt
        return reply

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    return fake


def test_rewrite_section_touches_only_that_section(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    built = report.build_report("projR", _intent(), PAPERS)          # MOCK 骨架
    fake = _use_real_llm(monkeypatch, "两条线各有取舍 [1]，第二条更省算力。")

    out = report.rewrite_section("projR", _intent(), PAPERS, built["title"], "方法对比", "更具体一点")
    assert out["ok"] and out["degraded"] is False
    assert "只重写「方法对比」" in fake.prompt and "更具体一点" in fake.prompt
    assert out["text"].startswith("两条线")
    assert "（降级骨架，未调用 LLM）" in out["body"]                  # 其余节原样
    assert "方法对比" not in out["untouched"] and "下一步建议" in out["untouched"]
    assert out["text"] in knowledge.get_note("projR", built["title"])   # 真的落回笔记


def test_rewrite_section_still_runs_the_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    built = report.build_report("projR2", _intent(), PAPERS)
    _use_real_llm(monkeypatch, "如《不存在的论文》所示 [1]，另有 [9]。")
    out = report.rewrite_section("projR2", _intent(), PAPERS, built["title"], "方法对比")
    assert "不存在的论文" not in out["body"] and "[9]" not in out["body"]
    assert out["removed_titles"] == ["《不存在的论文》"]


def test_locked_missing_and_unknown_targets_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    built = report.build_report("projR3", _intent(), PAPERS)
    locked = report.rewrite_section("projR3", _intent(), PAPERS, built["title"], "引用论文")
    assert locked["ok"] is False and "自动生成" in locked["note"]
    missing = report.rewrite_section("projR3", _intent(), PAPERS, built["title"], "不存在的小节")
    assert missing["ok"] is False and "不存在的小节" in missing["note"]
    nofile = report.rewrite_section("projR3", _intent(), PAPERS, "综述：没这篇", "方法对比")
    assert nofile["ok"] is False and "没有找到笔记" in nofile["note"]


def test_report_stream_endpoint_deltas_then_done(client):
    pid = client.post("/companion/project", json={"name": "流式综述"}).json()["project_id"]
    client.post("/companion/chat", json={"user_text": "我想做多模态钓鱼网站检测", "project_id": pid})
    paper_memory.save_many(pid, PAPERS)
    events, payloads = [], []
    with client.stream("POST", f"/companion/report/{pid}/stream") as r:
        assert r.status_code == 200
        cur = ""
        for line in r.iter_lines():
            if line.startswith("event:"):
                cur = line.split(":", 1)[1].strip()
                events.append(cur)
            elif line.startswith("data:"):
                payloads.append((cur, json.loads(line.split(":", 1)[1].strip())))
    assert events[0] == "step" and "delta" in events and events[-1] == "done"
    done = dict(payloads[-1][1])
    assert done["ok"] is True and done["state"] == "DELIVER" and done["degraded"] is True
    assert "".join(p["text"] for e, p in payloads if e == "delta")     # 真的逐段吐过字
    assert knowledge.get_note(pid, done["title"]) is not None


def test_report_stream_endpoint_404(client):
    with client.stream("POST", "/companion/report/ghost/stream") as r:
        assert r.status_code == 404


def test_section_endpoint(client, tmp_path, monkeypatch):
    pid = client.post("/companion/project", json={"name": "逐节重写"}).json()["project_id"]
    client.post("/companion/chat", json={"user_text": "我想做多模态钓鱼网站检测", "project_id": pid})
    paper_memory.save_many(pid, PAPERS)
    title = client.post(f"/companion/report/{pid}").json()["title"]

    ok = client.post(f"/companion/report/{pid}/section",
                     json={"title": title, "section": "方法对比", "instruction": "带上数据集规模"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["section"] == "方法对比" and ok.json()["degraded"] is True

    bad = client.post(f"/companion/report/{pid}/section",
                      json={"title": title, "section": "引用论文"})
    assert bad.status_code == 400 and "自动生成" in bad.json()["detail"]
    assert client.post("/companion/report/ghost/section",
                       json={"title": title, "section": "方法对比"}).status_code == 404
