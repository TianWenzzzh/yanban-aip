"""Phase D PDF 管线测试：自建 PDF → 下载缓存 → 解析 → QA（全部离线）。"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ["LLM_MOCK"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import companion.db as db_module
from companion.paper.parser import parse_pdf


@pytest.fixture()
def sample_pdf(tmp_path) -> pathlib.Path:
    """用 PyMuPDF 自造一份带章节的测试 PDF。"""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Abstract\nWe propose a visual approach for phishing detection using deep learning.",
                     fontsize=11)
    page.insert_text((72, 200), "1 Introduction\nPhishing websites frequently imitate legitimate sites.",
                     fontsize=11)
    page.insert_text((72, 330), "4 Experiments\nWe evaluate on three public datasets with high accuracy.",
                     fontsize=11)
    out = tmp_path / "sample.pdf"
    doc.save(str(out))
    doc.close()
    return out


def test_parse_pdf_pages_and_sections(sample_pdf):
    parsed = parse_pdf(str(sample_pdf))
    assert len(parsed["pages"]) == 1
    assert "Abstract" in parsed["pages"][0]["text"]
    sections = parsed["sections"]
    assert "abstract" in sections
    assert "introduction" in sections
    assert "experiments" in sections
    assert "visual approach" in sections["abstract"]


def test_parse_pdf_sections_text_nonempty(sample_pdf):
    parsed = parse_pdf(str(sample_pdf))
    assert "phishing" in parsed["sections"]["introduction"].lower()


def test_qa_uses_sections_mock(sample_pdf):
    from companion.paper.qa import answer_with_paper
    r = answer_with_paper("这篇论文的方法是什么？", str(sample_pdf), project_id=None)
    assert r["answer"].startswith("[MOCK]")
    assert r["sections_used"]  # 命中了相关章节
