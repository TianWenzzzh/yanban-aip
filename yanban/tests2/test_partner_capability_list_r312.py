# -*- coding: utf-8 -*-
r"""交付件「轮 311 能力对照清单」末尾那节**轮 312 增量**的常驻四条腿。

为什么要有这门（台账 93）：增量节里写的每件事都是会过期的那三种——函数名、用例枚数、表格形状。
而仓里现有三台文档门都看不见它：`tests2/test_doc_line_refs.py` 文件头自己写着「扫描面仍只有
`code/yanban/docs/*.md`，工作空间层交接件与交付件不在内」，且它「只看那一行是不是空行／有没有越界，
**不做内容匹配**」；`tests2/test_doc_evidence_paths.py` 与扩展名申报族同样只管路径在不在盘上。
于是「抄了个行号，代码往上插了两百行」这种漂移没有任何一层会咬——轮 312 第十二段实测：
本清单里指向 `literature_partner.py`／`sources.py`／`extractor.py` 的坐标有 26 处（去重 25 条）已经指错地方，
三台门照样全绿。本门把判据接进生产路径，并配一条会开火的植入自证。
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]          # code/yanban
PKG = ROOT.parents[1]                                        # 工作空间根
DOC = PKG / "输出" / "交付" / "研伴2.0_轮311_能力对照清单.md"
PARTNER = (ROOT / "literature_partner.py").read_text(encoding="utf-8")
SOURCES = (ROOT / "sources.py").read_text(encoding="utf-8")

SECTION_HEAD = "## 轮 312 增量（2026-10-13，只追加、不改上面任何一行）"
HEADER_CELL = "表里那一行"

# 增量节点名"现在还做得到"的那批函数：改名／删掉任何一个，这一格必须红（＝行号门不管的那一维）。
# 地板 >= 10 是防"抽薄到只剩两个名字"——那种清单查什么都算通过。
NAMED_FUNCTIONS = (
    "_fulfil", "_insufficient", "_sessions", "_session_store", "_resolve_turn",
    "_limit_for", "_summarize", "_failure_line", "_topic_hint",
    "multi_source_search", "_fmt", "on_continue", "on_start", "_search",
)
FUNC_FLOOR = 10
ROW_FLOOR = 10
COUNT_FLOOR = 3

DIGIT_REF = re.compile(r"\.(?:py|tsx|ts|mjs|js)\s*[:：]\s*\d")
ITEM_RE = re.compile(r"([A-Za-z0-9_]+\.py)`（(\d+) 项")


def read_doc() -> str:
    if not DOC.is_file():
        raise AssertionError(f"交付件不在盘上：{DOC}（本门的凭据物就是这份文件，缺了不能算通过）")
    return DOC.read_text(encoding="utf-8")


def increment_section(text: str) -> str:
    """取「轮 312 增量」那一节到文件末尾。标题不是恰好一次就红——两次等于没有权威源。"""
    hits = text.count(SECTION_HEAD)
    if hits != 1:
        raise AssertionError(f"增量节标题应恰好 1 次，实际 {hits} 次：{SECTION_HEAD}")
    return text[text.index(SECTION_HEAD):]


def table_rows(section: str) -> tuple[list[str], list[str], int]:
    """（表体行, 全部竖线行, 表头列数即竖线根数）。"""
    pipes = [l for l in section.split("\n") if l.startswith("|")]
    header = [l for l in pipes if HEADER_CELL in l]
    if len(header) != 1:
        raise AssertionError(f"表头应恰好一行，实际 {len(header)} 行")
    body = [l for l in pipes if l is not header[0] and not _is_separator(l)]
    return body, pipes, header[0].count("|")


def _is_separator(line: str) -> bool:
    return bool(line) and set(line.replace("|", "").strip()) <= set("-: ")


def count_mismatches(declared: list[tuple[str, str]], counts: dict[str, int]) -> list[str]:
    """判据本体（纯函数，植入自证直接喂它）：逐条比"文档写的枚数"与"当场采集到的枚数"。"""
    return [f"{fname} 文档写 {claimed}｜现读 {counts.get(fname, '采集里没有这个文件')}"
            for fname, claimed in declared if str(counts.get(fname)) != claimed]


_COLLECTED: dict[str, int] = {}


def live_counts(fnames: list[str]) -> dict[str, int]:
    """一次 `--collect-only` 拿到逐文件枚数（按用例 id 的行前缀分组，不用每枚文件起一个进程）。
    只跑一遍是刻意的：整份额外子进程在这台机器上值十几秒，而逐文件分组并没有丢掉分辨力。"""
    if _COLLECTED:
        return _COLLECTED
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only", "tests2"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    if proc.returncode != 0:
        raise AssertionError(f"tests2 采集不过（RC={proc.returncode}）：末 8 行 "
                             f"{proc.stdout.splitlines()[-8:]}")
    for line in proc.stdout.splitlines():
        m = re.match(r"tests2[/\\]([A-Za-z0-9_]+\.py)::", line.strip())
        if m:
            _COLLECTED[m.group(1)] = _COLLECTED.get(m.group(1), 0) + 1
    missing = [f for f in fnames if f not in _COLLECTED]
    if missing:
        raise AssertionError(f"采集输出里没有这些测试件（文档却在点名它们）：{missing}")
    return _COLLECTED


def test_increment_section_is_there_and_carries_no_numeric_coordinates():
    section = increment_section(read_doc())
    bad = DIGIT_REF.findall(section)
    assert not bad, (f"增量节里有 {len(bad)} 处 `文件.py:数字`。这一节是轮 311 那张表的**增量**，"
                     f"上面那 26 处旧坐标已因两次整体位移指错地方；再抄数字＝把同一个坑再挖一次。"
                     f"改成功能名＋`grep -n` 现读。样例：{bad[:3]}")


def test_declared_case_counts_equal_this_collection():
    section = increment_section(read_doc())
    declared = ITEM_RE.findall(section)
    assert len(declared) >= COUNT_FLOOR, (
        f"增量节里带枚数的测试件只有 {len(declared)} 条（地板 {COUNT_FLOOR}）＝这一腿没有分辨力")
    counts = live_counts([f for f, _ in declared])
    wrong = count_mismatches(declared, counts)
    assert not wrong, "枚数是抄进文档的第二份真相，加用例不会顺手改它：" + "；".join(wrong)


def test_named_functions_still_exist_in_product_code():
    assert len(NAMED_FUNCTIONS) >= FUNC_FLOOR, (
        f"钉着的函数名只剩 {len(NAMED_FUNCTIONS)} 枚（地板 {FUNC_FLOOR}）＝清单被抽薄，不是改名")
    missing = [f for f in NAMED_FUNCTIONS if f not in PARTNER and f not in SOURCES]
    assert not missing, (f"增量节点名了产品码里已经不存在的名字：{missing}"
                         "（改名／删除要同时改那一节，别让文档替代码说谎）")


def test_increment_table_shape_is_uniform():
    body, pipes, cols = table_rows(increment_section(read_doc()))
    ragged = [l[:36] for l in body if l.count("|") != cols]
    assert not ragged, f"有行的列数与表头（{cols} 根竖线）不符：{ragged}"
    assert len(body) >= ROW_FLOOR, (
        f"表体只有 {len(body)} 行（地板 {ROW_FLOOR}，竖线行共 {len(pipes)}）＝有人整块删格子；"
        "要减格子请在正文里写明为什么")


def test_implants_are_caught():
    """负控：四条判据各自喂一枚植入，必须都开火。没有这条，上面四条可能恒真。"""
    section = increment_section(read_doc())
    # 植入 1（数字坐标）：抓得到，且不误伤正常写法
    assert DIGIT_REF.findall("见 `sources.py:241`"), "坐标提取器连植入都抓不到＝仪器坏了"
    assert DIGIT_REF.findall("literature_partner.py：616"), "全角冒号那一支没接住"
    assert not DIGIT_REF.findall("函数名 `_fmt`、`-32001`、无冒号数字"), "提取器太宽会误伤正常文本"
    # 植入 2（枚数）：同一台判据喂"挪一格"与"归零"的申报，各必须正好红一条
    declared = ITEM_RE.findall(section)
    counts = live_counts([f for f, _ in declared])
    fname, claimed = declared[0]
    assert count_mismatches(declared, counts) == [], "真文档不该红却红了＝判据坏了"
    shifted = [(fname, str(int(claimed) + 1))] + declared[1:]
    assert count_mismatches(shifted, counts) == [f"{fname} 文档写 {int(claimed) + 1}｜现读 {counts[fname]}"], \
        "枚数判据挪一格不红＝它是装饰"
    assert len(count_mismatches([(fname, "0")] + declared[1:], counts)) == 1
    # 植入 2b：采集里根本没有的文件名要认出来，不能"两边都没有所以相等"
    assert len(count_mismatches([("test_not_in_repo.py", "3")], counts)) == 1
    # 植入 3（函数名）：不存在的名字必须被判红，现役名字必须在
    assert "_fulfil" in PARTNER
    fake = [f for f in ("_fulfil_never_existed", "_renamed_away_last_round")
            if f in PARTNER or f in SOURCES]
    assert fake == [], f"植入名竟然在产品码里（{fake}）＝这条负控是假的"
    assert [f for f in NAMED_FUNCTIONS if f not in PARTNER and f not in SOURCES] == []
    # 植入 4（列数）：造一根**少一格**的行——先确认它真的比表头少一根竖线，再确认现役每行都齐
    body, _pipes, cols = table_rows(section)
    ragged_row = "| 格 " * (cols - 2) + "| 格"
    assert ragged_row.count("|") == cols - 1, f"植入本身有 {ragged_row.count('|')} 根竖线，表头 {cols}＝负控是假的"
    assert body and all(l.count("|") == cols for l in body)
