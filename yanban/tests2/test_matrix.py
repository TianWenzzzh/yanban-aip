"""跨论文对比表（轮 19）：落地校验、缓存、自定义列与 API。"""
from __future__ import annotations

import asyncio
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
from companion.literature import matrix

P1 = {"id": "p1", "title": "Visual Phishing Detection with CLIP", "year": 2022, "doi": "10.1/x",
      "abstract": "We use CLIP to embed URLs and train a classifier on 50000 phishing pages, "
                  "reaching 96.2% accuracy on the held-out set."}
P2 = {"id": "p2", "title": "A Survey of Multi-Agent LLM Systems", "year": 2024,
      "abstract": "This survey reviews 120 papers on multi-agent LLM systems and lists open challenges."}


@pytest.fixture()
def tmp_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "MATRIX_DIR", tmp_path / "matrix")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    db_module._CONN = None
    from leader import app
    return TestClient(app)


# ── 落地校验：只拦最危险的两类幻觉（数值 / 专名），允许中文改写 ──────────
def test_fabricated_blocks_invented_numbers_and_terms():
    src = matrix.source_text(P1)
    assert matrix.fabricated("准确率 98.7%", src) is True          # 摘要里没有的数字
    assert matrix.fabricated("用 BERT 嵌入 URL", src) is True      # 摘要里没有的专名
    assert matrix.fabricated("用 CLIP 嵌入 URL 训练分类器", src) is False
    assert matrix.fabricated("准确率 96.2%", src) is False
    assert matrix.fabricated("未知", src) is False


def test_cells_without_evidence_fall_back_to_unknown(tmp_matrix, monkeypatch):
    async def fake_chat_json(prompt, **kw):
        return [
            {"id": "p1", "method": "用 CLIP 嵌入 URL 训练分类器", "dataset": "50000 个钓鱼页面",
             "metric": "准确率 98.7%", "limitation": "未做真实流量实验"},
            {"id": "p2", "method": "综述 120 篇多智能体论文", "dataset": "未知",
             "metric": "未知", "limitation": "未知"},
        ]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))

    out = matrix.build_matrix("projA", [P1, P2])
    rows = {r["id"]: r["cells"] for r in out["rows"]}
    assert rows["p1"]["method"].startswith("用 CLIP")            # 有依据 → 保留原样（含大小写）
    assert rows["p1"]["dataset"] == "50000 个钓鱼页面"
    assert rows["p1"]["metric"] == matrix.UNKNOWN                # 编造数字 → 剔除
    assert rows["p1"]["limitation"] == "未做真实流量实验"
    assert rows["p2"]["dataset"] == matrix.UNKNOWN
    assert out["cached"] is False
    assert out["rows"][0]["unknown_count"] == 1


def test_llm_missing_paper_keeps_row_with_all_unknown(tmp_matrix, monkeypatch):
    async def only_one(prompt, **kw):
        return [{"id": "p1", "method": "用 CLIP 嵌入 URL"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", only_one)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    rows = matrix.build_matrix("projB", [P1, P2])["rows"]
    assert len(rows) == 2 and rows[1]["title"] == P2["title"]
    assert all(v == matrix.UNKNOWN for v in rows[1]["cells"].values())


def test_basis_and_fulltext_grounding(tmp_matrix, monkeypatch):
    """PDF 正文要进落地校验依据；全文出现后旧缓存必须失效重抽（轮 22 的主目标：填充率）。"""
    calls = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        return [{"id": "p1", "method": "CLIP 加残差网络", "dataset": "36800 张图像",
                 "metric": "F1 0.94", "limitation": "未知"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))

    first = matrix.build_matrix("projF", [P1])
    assert first["fulltext_rows"] == 0
    assert first["rows"][0]["cells"]["metric"] == matrix.UNKNOWN   # 摘要里没有 0.94 → 判编造

    monkeypatch.setattr(matrix, "full_text",
                        lambda pid, p: "We train on 36800 images and report F1 0.94 with a ResNet backbone.")
    second = matrix.build_matrix("projF", [P1])
    assert second["cached"] is False and len(calls) == 2           # 全文出现 → 旧缓存键失效
    assert second["fulltext_rows"] == 1
    assert second["rows"][0]["cells"]["metric"] == "F1 0.94"       # 同一个数值现在站得住
    assert second["rows"][0]["basis"] == "全文"
    assert matrix.collect_sources("projF", [P1])["p1"][1] == "全文"


def test_extraction_runs_at_zero_temperature(tmp_matrix, monkeypatch):
    """抽取不是创作：同一批论文重复抽，线上实测填充率极差 25pp，所以温度锁 0。"""
    seen = {}

    async def fake(prompt, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    matrix.build_matrix("projT", [P1])
    assert seen.get("temperature") == 0.0


def test_verbatim_evidence_is_required_and_checked(tmp_matrix, monkeypatch):
    async def fake(prompt, **kw):
        return [{"id": "p1",
                 "method": {"a": "用 CLIP 嵌入 URL", "e": "We use CLIP to embed URLs"},
                 "dataset": {"a": "50000 个页面", "e": "这段片段是我编的 not in the source"},
                 "metric": {"a": "准确率 99%", "e": "reaching 96.2% accuracy"},
                 "limitation": "未知"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    row = matrix.build_matrix("projE", [P1])["rows"][0]
    cells, ev = row["cells"], row["evidence"]
    assert cells["method"].startswith("用 CLIP") and ev["method"].startswith("We use CLIP")
    assert cells["dataset"] == matrix.UNKNOWN       # 证据不是原文逐字子串 → 整格作废
    assert cells["metric"] == matrix.UNKNOWN        # 出处是真的，但答案里的 99% 原文没有
    assert cells["limitation"] == matrix.UNKNOWN and ev["limitation"] == ""


def test_plain_string_answer_falls_back_to_the_number_and_term_heuristic(tmp_matrix, monkeypatch):
    async def fake(prompt, **kw):
        return [{"id": "p1", "method": "用 CLIP 嵌入 URL 训练分类器"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    row = matrix.build_matrix("projE2", [P1])["rows"][0]
    assert row["cells"]["method"] == "用 CLIP 嵌入 URL 训练分类器"
    assert row["evidence"]["method"] == ""


def test_prompt_asks_for_verbatim_span(tmp_matrix):
    prompt = matrix._prompt([P1], matrix.BASE_COLUMNS, {"p1": (matrix.source_text(P1), "摘要")})
    assert "一字不差" in prompt and '"e"' in prompt
    assert P1["abstract"][:30] in prompt


def test_no_content_answers_still_count_as_unknown(tmp_matrix):
    src = matrix.source_text(P1)
    assert matrix._cell({"a": "未提供具体数值", "e": "reaching 96.2% accuracy"}, src) == (matrix.UNKNOWN, "")
    assert matrix._cell({"a": "准确率 96.2%", "e": "reaching 96.2% accuracy"}, src)[0] == "准确率 96.2%"


def test_sourced_ratio_is_reported_as_a_first_class_metric(tmp_matrix, monkeypatch):
    async def fake(prompt, **kw):
        return [{"id": "p1", "method": {"a": "用 CLIP 嵌入 URL", "e": "We use CLIP to embed URLs"},
                 "dataset": "用 CLIP 做分类"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    out = matrix.build_matrix("projR", [P1])
    assert (out["filled"], out["sourced"], out["sourced_ratio"]) == (2, 1, 0.5)


def test_second_build_hits_cache(tmp_matrix, monkeypatch):
    calls = []

    async def fake(prompt, **kw):
        calls.append(prompt)
        return [{"id": "p1", "method": "用 CLIP 嵌入 URL"}]

    monkeypatch.setattr(llm, "LLM_MOCK", False)
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "run_async", lambda coro: asyncio.run(coro))
    first = matrix.build_matrix("projC", [P1])
    second = matrix.build_matrix("projC", [P1])
    assert len(calls) == 1 and second["cached"] is True
    assert first["rows"] == second["rows"]


def test_mock_mode_makes_no_network_call_and_yields_unknown_table(tmp_matrix):
    out = matrix.build_matrix("projD", [P1, P2])
    assert len(out["rows"]) == 2
    assert all(v == matrix.UNKNOWN for r in out["rows"] for v in r["cells"].values())


def test_custom_columns_are_capped_and_keyed_by_backend():
    cols = matrix.normalize_columns([{"label": "是否用了 CLIP"}, {"label": "开源了吗"},
                                     {"label": "第三列"}, {"label": "第四列"}])
    assert cols[:4] == matrix.BASE_COLUMNS
    assert cols[4:] == [("c1", "是否用了 CLIP"), ("c2", "开源了吗"), ("c3", "第三列")]
    assert matrix.normalize_columns([{"label": "   "}]) == matrix.BASE_COLUMNS


def test_prompt_forbids_prior_knowledge_and_lists_columns(tmp_matrix):
    prompt = matrix._prompt([P1], matrix.normalize_columns([{"label": "开源了吗"}]))
    assert "只依据每篇给出的文本" in prompt and "未知" in prompt
    assert "c3" not in prompt and "c1＝开源了吗" in prompt
    assert "该去抄" in prompt and "accuracy" in prompt      # 每列都告诉模型该抄什么，否则温度 0 下只填一列
    assert P1["abstract"][:40] in prompt          # 真摘要进上下文，不是只给标题


# ── API ───────────────────────────────────────────────────────────────
def test_matrix_endpoint_lists_recommended_papers(client, tmp_matrix):
    pid = client.post("/companion/project", json={"name": "对比表"}).json()["project_id"]
    empty = client.post(f"/companion/literature/{pid}/matrix", json={})
    assert empty.status_code == 200 and empty.json()["rows"] == []

    from companion.memory import paper_memory
    paper_memory.save_many(pid, [P1, P2])
    r = client.post(f"/companion/literature/{pid}/matrix",
                    json={"columns": [{"label": "有没有代码"}]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert [k for k, _ in data["columns"]] == ["method", "dataset", "metric", "limitation", "c1"]
    assert {row["id"] for row in data["rows"]} == {"p1", "p2"}
    assert data["rows"][0]["cells"]["c1"] == matrix.UNKNOWN


def test_matrix_endpoint_rejects_unknown_project(client, tmp_matrix):
    r = client.post("/companion/literature/nope/matrix", json={})
    assert r.status_code == 200 and r.json()["rows"] == []   # 无论文 → 空表，而不是 500
