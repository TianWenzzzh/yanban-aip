"""`RUNTIME_STATE.md` 里那行 `G1 pytest=N passed` 必须等于**当场采集到的用例数**。

为什么要机器盯：这一行是文件自己指名的「以本行为准」的那一行，而它连着两轮漂过
（轮 117 前后写着 260、真实 285；轮 128 补写着 290、真实 291）。人加测试时不会想起改它。

判据用 `--collect-only` 而不是跑完整份：跑完整份要 60 秒。代价是采集数**不减 skip/fail**，
所以红有两种成因（改了用例数 / 有用例不再跑），报错必须把两种都摊开让人判一次。
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "RUNTIME_STATE.md"

DOC_LINE = re.compile(r"^G1 pytest=(\d+) passed / (\d+) deselected", re.M)
COLLECT = re.compile(r"(\d+)/(\d+) tests? collected \((\d+) deselected")
COLLECT_PLAIN = re.compile(r"(\d+) tests? collected")


def parse_doc_line(text: str) -> tuple[int, int]:
    """文档里那份 (passed, deselected)。没有这行、或有两行，都必须红：
    出现两次就等于没有权威源。"""
    hits = DOC_LINE.findall(text)
    if len(hits) != 1:
        raise AssertionError(f"「G1 pytest=」权威行应恰好 1 次，实际 {len(hits)} 次：{hits}")
    return int(hits[0][0]), int(hits[0][1])


def parse_collect_summary(out: str) -> tuple[int, int]:
    """采集结果的 (参与数, 被 deselect 数)。pytest 两种措辞都要认：
    只认带 deselect 的那一种，会在「这一代恰好没有 deselect」时恒不命中（恒绿型缺陷）。"""
    m = COLLECT.search(out)
    if m:
        return int(m.group(1)), int(m.group(3))
    m = COLLECT_PLAIN.search(out)
    if m:
        return int(m.group(1)), 0
    raise AssertionError(f"没在 --collect-only 输出里找到采集行，末 15 行：{_tail(out)}")


def compare(doc: tuple[int, int], collected: tuple[int, int]) -> str:
    """一致返回 ""，不一致返回要印出来的那句话（判据与措辞同处，负向用例才测得到话本身）。"""
    (doc_passed, doc_deselected), (n, deselected) = doc, collected
    if doc_deselected != deselected:
        return (f"文档写「{doc_deselected} deselected」，本代采集是 {deselected}"
                f" ⇒ 剔除口径（markers）变了，这一条要人判一次再改文档")
    if doc_passed != n:
        return (f"文档写 {doc_passed} passed，本代采集到 {n} 条。两种成因都要看清："
                f"(a) 有人加了/删了测试没改这行 ⇒ 改文档；"
                f"(b) 这一代里有 skip/fail（采集数不减它们）⇒ 不是文档的错，去修那条测试")
    return ""


def collect_counts() -> tuple[int, int]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})
    if proc.returncode != 0:
        raise AssertionError(f"--collect-only RC={proc.returncode}（采集都过不去，不是数字漂）："
                             f"\n{_tail(proc.stdout + proc.stderr)}")
    return parse_collect_summary(proc.stdout)


def _tail(text: str, n: int = 15) -> str:
    return text.splitlines()[-n:]


def test_doc_authoritative_g1_line_equals_this_collection():
    doc = parse_doc_line(DOC.read_text(encoding="utf-8"))
    problem = compare(doc, collect_counts())
    assert problem == "", problem


def test_shifted_number_in_the_doc_must_be_caught():
    """负对照：把「与采集一致」的那一份故意挪一格，判据必须红、且红在说得出成因的那句话上。
    没有这条，`compare` 可能恒返回 ""（上面那条活用例也就跟着恒真）。
    这里只喂 `collect_counts()` 出来的真值做基准，不读文档 —— 文档漂着的时候这条仍要能自证。"""
    n, deselected = collect_counts()
    assert compare((n, deselected), (n, deselected)) == ""
    assert compare((n + 1, deselected), (n, deselected)) != ""
    assert compare((n, deselected - 1), (n, deselected)) != ""


def test_parser_recognises_both_collect_phrasings():
    assert parse_collect_summary("291/296 tests collected (5 deselected) in 0.97s\n") == (291, 5)
    assert parse_collect_summary("296 tests collected in 0.97s\n") == (296, 0)
