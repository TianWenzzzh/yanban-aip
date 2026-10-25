"""B5 PDF 段落级定位（对标 undermind U7 的可用子集）：引用片段必须报得出「第几页 · 哪一节」，
报不出来必须明说报不出来。

为什么"找不到"比"找到"更要紧：页码是一个看起来像事实的东西。定位不到时若默认第 1 页，
前台就摆出一格「第 1 页」，用户以为程序知道——这正是本项目红线要挡的那类伪装。
所以 `locate` 的失败形状（found=False / page=None）与成功形状同等重要，两边都有用例。

本轮**没有**做整页渲染、缩放、书签（那是阅读器的活）；页码跳转是把 `#page=N` 交给
浏览器自带的 PDF 视图，我们只负责"这个 N 是怎么来的"。
"""
from __future__ import annotations

import os
import pathlib

os.environ["LLM_MOCK"] = "1"

import pymupdf
import pytest
from fastapi.testclient import TestClient

import companion.db as db_module
import companion.knowledge as knowledge
from companion.memory import paper_memory
from companion.paper import parser

TSX = pathlib.Path(__file__).resolve().parents[1] / "webui/src/pages/Companion.tsx"

INTRO = "This paper studies photovoltaic power forecasting with weather inputs."
EXP = "We compare three baselines over two years of measured data on 96 sites."
WRAP_A = "We compare three baselines over"
WRAP_B = "two years of measured data here."


def _write_pdf(path: pathlib.Path) -> None:
    """三页夹具：第 1 页 Introduction，第 2 页无新标题（仍属 Introduction），第 3 页 Experiments。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 90), "Introduction", fontsize=14)
    p1.insert_text((72, 130), INTRO)
    p2 = doc.new_page()
    p2.insert_text((72, 130), WRAP_A)
    p2.insert_text((72, 150), WRAP_B)
    p3 = doc.new_page()
    p3.insert_text((72, 90), "Experiments", fontsize=14)
    p3.insert_text((72, 130), EXP)
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def parsed(tmp_path):
    path = tmp_path / "pdf" / "p1" / "paper.pdf"
    _write_pdf(path)
    return parser.parse_pdf(str(path))


def test_locate_reports_the_page_the_span_is_actually_on(parsed):
    assert parser.locate(parsed, EXP) == {"found": True, "page": 3, "section": "experiments"}
    assert parser.locate(parsed, INTRO)["page"] == 1


def test_locate_survives_a_line_break_in_the_pdf(parsed):
    """PDF 里被排成两行的一句，照样要定位到第 2 页：折的是空白，比的是内容。"""
    got = parser.locate(parsed, f"{WRAP_A} {WRAP_B}")
    assert got == {"found": True, "page": 2, "section": "introduction"}, got


def test_page_without_its_own_heading_inherits_the_last_started_one(parsed):
    """第 2 页没有标题行 ⇒ 属上一节（introduction），不是"没有章节"也不是重新算一遍。"""
    assert parsed["page_sections"] == {"1": "introduction", "2": "introduction",
                                       "3": "experiments"}


def test_locate_says_not_found_instead_of_guessing_page_one(parsed):
    """🔴 失败形状：定位不到时 page 必须是 None，绝不能退成 1。

    退成 1 的话，前台那一格会摆出一个从没被量过的页码——比空着更糟，因为它看起来像事实。
    """
    got = parser.locate(parsed, "A sentence that appears nowhere in this document at all.")
    assert got == {"found": False, "page": None, "section": None}


def test_a_short_span_is_not_allowed_to_claim_a_page(parsed):
    """太短的串（如一个词）几乎每页都有，拿它报"第 N 页"是假精确。"""
    assert parser.locate(parsed, "baselines")["found"] is False


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(knowledge, "KB_ROOT", tmp_path / "kb")
    from companion.paper import downloader
    monkeypatch.setattr(downloader, "PDF_DIR", tmp_path / "pdf")
    db_module._CONN = None
    from leader import app
    return TestClient(app), tmp_path / "pdf"


def _cite(client, pid, claim, index=1, filename="paper.pdf"):
    r = client.post("/companion/cite/context",
                    json={"project_id": pid, "claim": claim, "paper_index": index,
                          "filename": filename})
    assert r.status_code == 200, r.text
    return r.json()


def test_cite_context_carries_page_and_section_for_a_pdf_span(client):
    client, pdf_dir = client
    _write_pdf(pdf_dir / "b5a" / "paper.pdf")
    paper_memory.save_many("b5a", [{"id": "a1", "title": "Photovoltaic forecasting study",
                                    "abstract": "", "pdf_file": "paper.pdf"}],
                           status="recommended")
    body = _cite(client, "b5a", "We compare three baselines over two years of measured data on 96 sites.")
    assert body["status"] == "grounded", body
    span = body["spans"][0]
    assert (span["found"], span["page"], span["section"]) == (True, 3, "experiments"), span


def test_cite_context_without_a_cached_pdf_says_so_instead_of_a_page(client):
    """没缓存 PDF：片段只能来自题录，页码必须报 found=False（不是 0、不是 1、不是"未知页"）。"""
    client, _ = client
    paper_memory.save_many("b5b", [{"id": "b1", "title": "Grid tied PV plants",
                                    "abstract": "An early survey of grid connected PV plants "
                                                "with limited meteorological coverage."}],
                           status="recommended")
    body = _cite(client, "b5b", "An early survey of grid connected PV plants coverage")
    assert body["spans"], "题录摘要该能给出片段（否则这条判据什么都没量到）"
    assert all(s["found"] is False and s["page"] is None for s in body["spans"]), body["spans"]


def test_abstract_only_span_stays_unlocated_even_with_a_pdf_cached(client):
    """摘要来自 API、不在这份 PDF 的页面上 ⇒ 有 PDF 也不许硬给一个页码。

    这一条是上一条的对照：两次的差别只在"有没有缓存 PDF"，两次的页码都必须是 None。
    """
    client, pdf_dir = client
    _write_pdf(pdf_dir / "b5c" / "paper.pdf")
    paper_memory.save_many("b5c", [{"id": "c1", "title": "Photovoltaic forecasting study",
                                    "abstract": "An early survey of grid connected PV plants "
                                                "with limited meteorological coverage.",
                                    "pdf_file": "paper.pdf"}], status="recommended")
    body = _cite(client, "b5c", "An early survey of grid connected PV plants coverage")
    assert body["spans"], body
    assert all(s["found"] is False for s in body["spans"]), body["spans"]


def test_frontend_shows_both_the_page_and_the_missing_page():
    """呈现层两条分支都要在：定位到给「第 N 页 · 节名」，定位不到明说"没定位到页"。

    反向对照：不许出现写死的第 1 页——那是把默认值伪装成读数。
    🔴 这条首跑就红过一次，红因不是缺陷而是**撞词**：我自己写的注释里有"绝不默认第 1 页"。
       处理是改注释措辞（判据保持严格），不是把 needle 放宽。
    """
    text = TSX.read_text(encoding="utf-8")
    assert 'data-testid="cite-page"' in text, "定位成功那支不在了"
    assert 'data-testid="cite-page-missing"' in text, "🔴 定位失败那支被删了 ⇒ 界面会静默不提页码"
    assert "没定位到页" in text
    assert "第 1 页" not in text, "写死的第 1 页＝把默认值当读数摆出来"


def test_cite_jump_passes_the_measured_page_to_the_drawer():
    """「看这页」必须把后端量到的那个页码交给抽屉，抽屉再交给浏览器 PDF 视图。

    这条单独存在（不并进上面那条）是为了让植入"跳转丢了页码"能红在自己名下。
    """
    text = TSX.read_text(encoding="utf-8")
    assert "openPdf(p, s.page ?? null)" in text, "「看这页」没把后端给的页码传给抽屉"
    assert text.count("src={drawerPage ? `${pdfUrl}#page=${drawerPage}` : pdfUrl}") == 1, \
        "抽屉的 iframe 没按 drawerPage 拼 #page=（或拼了两份）"
