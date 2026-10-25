# -*- coding: utf-8 -*-
r"""常驻判据：`输出/_scratch` 里本轮工装的「判定行 ⇄ 进程退出码」同向性。

为什么常驻（本段自己踩出来的，不是理论风险）：第二十一段首跑 `r312_p500_snapshot_worktree_batch19.py`
时，进程 `EC=1` 而日志最后一行印着 `SNAP19_RC=0`——克隆 p479 时末行写成了 `sys.exit(0 if not ok else 1)`。
危害不在那趟快照本身（认证链取的是**进程 rc**，它会红），而在**看日志判成败**的那一步：交接纪律写明
"只信日志里自己那份 `XXX_RC=` 判定行"，判定行与 rc 一分家，绿字就把红读成了绿。全库同批扫描
（`输出/_scratch/r312_p501_measure_rc_exitcode_agreement.py`，读数 run3 留档）当时 158 枚里 70 枚在判定面内，
唯一一枚红就是 p500。

判读面（机检可定罪的两种，其余一律不判红）：
  · 同一条件 `C`：判定行取 0 当 C 真，而 `sys.exit` 取 0 当 C 假（或反之）＝**反向**；
  · 判定行是条件式，而 `sys.exit` 是**常量 0/1**（失败分支上判定行会红而 rc 恒绿）。
`print("X_RC=%d" % ec, flush=True)` 这类**转发子进程 rc、自身不 exit** 的历史形状不进判定面
（它们的消费约定是读日志行）——本腿不为它们改判据、也不静默放过：它们落进"无形状"桶，由读数申报计数。
K（负控三件，内存合成，不碰盘）：反向必须红、同向不得误伤、常量 exit(0) 必须红——三件逐枚钉死判定。
地板：扫描面 ≥120 枚 .py、判定面（绿＋红）≥60 枚，低于任一＝扫描器或工装风格已漂到认不出，判红。
"""
from __future__ import annotations

import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent            # code/yanban/tests2
Y = HERE.parent                                           # code/yanban
PKG = Y.parent.parent                                     # 工作空间根
SC = PKG / "输出" / "_scratch"

RC_PRINT_RE = re.compile(r'print\(\s*"([A-Za-z0-9_]*_RC=(?:%d|%s))"\s*%\s*\((.*)\)\s*\)\s*$')
EXIT_RE = re.compile(r"sys\.exit\((.*)\)\s*$")
TERN_RE = re.compile(r"^\(?\s*(\d)\s+if\s+(.+?)\s+else\s+(\d)\s*\)?$")


def strip_paren(c: str) -> str:
    c = c.strip()
    while c.startswith("(") and c.endswith(")"):
        inner = c[1:-1].strip()
        if inner.count("(") != inner.count(")"):
            break
        c = inner
    return re.sub(r"\s+", " ", c)


def polarity(expr: str):
    """("pos", cond)＝cond 真时取 0；("neg", cond)＝cond 真时取 1；解不出返回 None。"""
    m = TERN_RE.match(strip_paren(expr))
    if not m:
        return None
    a, cond, b = m.group(1), strip_paren(m.group(2)), m.group(3)
    if (a, b) == ("0", "1"):
        return ("pos", cond)
    if (a, b) == ("1", "0"):
        return ("neg", cond)
    return None


def norm_cond(c: str):
    neg = c.startswith("not ")
    return (neg, strip_paren(c[4:] if neg else c))


def judge(src: str):
    """返回 (verdict, note)。verdict ∈ 红／黄／绿／无。"""
    lines = src.split("\n")
    rc_idx = [i for i, l in enumerate(lines) if RC_PRINT_RE.search(l)]
    if not rc_idx:
        return ("无", "找不到单占位符 `<MARK>_RC=` 判定行（形状不认）")
    i = rc_idx[-1]
    m = RC_PRINT_RE.search(lines[i])
    expr = m.group(2)
    p_rc = polarity(expr)
    ex_idx = [j for j in range(i, len(lines)) if EXIT_RE.match(lines[j].strip())]
    if not ex_idx:
        return ("黄", "第 %d 行有判定行，其后没有 sys.exit(...)＝进程 rc 不承载判定（读日志行）" % (i + 1))
    j = ex_idx[0]
    raw = lines[j].strip()
    inner = raw[len("sys.exit("):-1].strip()
    p_ex = polarity(inner)
    if p_rc is None:
        return ("黄", "判定行 `%s` 第 %d 行解不出极性（%s）" % (m.group(1), i + 1, strip_paren(expr)[:60]))
    if p_ex is None:
        if re.fullmatch(r"[01]", inner):
            return ("红", "第 %d 行 exit 是常量 %s，判定行却是条件式（%s）＝同一条失败分支上判定行与 rc 分家"
                    % (j + 1, inner, strip_paren(expr)[:40]))
        return ("黄", "第 %d 行 `sys.exit(...)` 解不出三目极性：%s" % (j + 1, raw[:60]))
    kind_rc, cond_rc = p_rc
    kind_ex, cond_ex = p_ex
    neg_rc, body_rc = norm_cond(cond_rc)
    neg_ex, body_ex = norm_cond(cond_ex)
    if body_rc != body_ex:
        return ("黄", "判定行与 exit 的布尔体不同名（`%s` vs `%s`）＝rc 不是由那行派生，需人看"
                % (body_rc[:50], body_ex[:50]))
    if ((kind_rc == "pos") != neg_rc) == ((kind_ex == "pos") != neg_ex):
        return ("绿", "同向：判定行与 sys.exit 都由 `%s` 派生且 0/1 分派一致（第 %d／%d 行）"
                % (body_rc[:40], i + 1, j + 1))
    return ("红", "反向：同一条件 `%s`，判定行『真→0』而 exit『真→1』（或反之）" % body_rc[:60])


# K｜负控三件：逐枚钉死判定值，不钉"有几条可判"。
FIXTURES = (
    ("反向", 'print("X_RC=%d" % (0 if ok else 1))\nsys.exit(0 if not ok else 1)\n', "红"),
    ("同向", 'print("X_RC=%d" % (0 if ok else 1))\nsys.exit(0 if ok else 1)\n', "绿"),
    ("常量exit", 'print("X_RC=%d" % (0 if ok else 1))\nsys.exit(0)\n', "红"),
)

FILE_FLOOR = 120
JUDGED_FLOOR = 60


def test_scratch_rc_markers_agree_with_exit_codes():
    for name, src, expected in FIXTURES:
        v, note = judge(src)
        assert v == expected, "负控『%s』判成 %s（应 %s）：%s" % (name, v, expected, note)

    files = sorted(p for p in SC.glob("r312_*.py") if p.is_file())
    assert len(files) >= FILE_FLOOR, "扫描面只有 %d 枚 .py（地板 %d）＝目录读错或工装被挪" % (len(files), FILE_FLOOR)

    reds, judged, declared, unshaped, undecoded = [], 0, [], [], []
    for p in files:
        try:
            src = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            undecoded.append(p.name)
            continue
        v, note = judge(src)
        if v == "红":
            reds.append("%s｜%s" % (p.name, note))
        elif v == "绿":
            judged += 1
        elif v == "黄":
            declared.append(p.name)
        else:
            unshaped.append(p.name)

    assert judged >= JUDGED_FLOOR, (
        "判定面只剩 %d 枚（地板 %d）＝扫描器没跟住工装风格，零红不能当通过" % (judged, JUDGED_FLOOR))
    assert not reds, (
        "判定行与进程退出码分家 %d 枚（黄档申报 %d 枚、无形状 %d 枚、未解码 %d 枚）：\n%s"
        % (len(reds), len(declared), len(unshaped), len(undecoded), "\n".join(reds)))
