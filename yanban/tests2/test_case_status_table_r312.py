# -*- coding: utf-8 -*-
r"""轮 312 第十八段·常驻门：对外交付件「十四条用例状态表」两张表的形状、编号、状态口径与证据路径。

为什么要有这台门（缺陷是实测到的，不是设想）：一次性探针
`输出/_scratch/r312_p462_measure_case_table_shape.py`（读数 `输出/_logs/r312_p462_measure_case_table_shape.log`，
判定行 `SHAPE462_RC=1`）量到：轮 312 增量那张表表头声明 3 列（`#｜这一格现在的事实｜证据`），
而 `# 12` 那一行带 4 格——多出来那枚"已测（离线·分发层）；线上重发/Continue 用法仍未核"落在**没有表头**的列位上，
渲染时溢出或被丢，读表的人看不见它。此前没有任何常驻判据读过这份文件的列形状
（`test_doc_evidence_paths` 只扫 `code/yanban/docs/*.md`；`test_doc_prompt_state_r312` 只看工作空间层 §1）。

六条腿，每腿一枚内存植入：
  S1 形状：每条数据行的列分隔符数 == 该表表头（按分隔符位置数，不用 split(竖线)——值格里有内嵌竖线）。
  S2 主表覆盖：编号恰好 01..14，不重不漏（地板 14）。
  S3 主表状态口径：状态格以 已测／部分／未实现／未验证 开头，且不含完成性越口径词。
  S4 增量表：编号唯一且都在主表里；状态格非空、口径内、不含越口径词。
  S5 证据路径：表内反引号点名的用例/日志/交付件逐枚查盘（裸文件名按限定目录索引解析）。
  S6 地板：整张表看不见 ⇒ 红（空集合不许绿灯）。
"同一把尺"：`seps`／`cells` 直接复用 `test_doc_prompt_state_r312`，两处数格子口径不会各说各话。
"""
import importlib.util
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
_ws = os.path.normpath(os.path.join(PKG, os.pardir, os.pardir))
DOC = os.path.join(_ws, "输出", "交付", "研伴2.0_轮311_十四条用例状态表.md")
SIBLING = os.path.join(HERE, "test_doc_prompt_state_r312.py")

_spec = importlib.util.spec_from_file_location("_prompt_gate", SIBLING)
_pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pg)
seps = _pg.seps
cells = _pg.cells

STATUS_WORDS = ("已测", "部分", "未实现", "未验证")
INCR_STATUS_OK = STATUS_WORDS + ("本地实现＋线上未核",)
OVERREACH = ("已验证通过", "全部通过", "已完成", "全绿", "线上已核", "验收通过")
MAIN_IDS = ["%02d" % k for k in range(1, 15)]
MAIN_FLOOR = 14
INCR_FLOOR = 5
PATH_RE = re.compile(r"^(?:[\w./\\\-一-鿿]+\.(?:py|md|txt|log|json|db|sh|zip))"
                     r"(?::\d+(?:-\d+)?(?:/\d+)?)?$")
SEP_ROW = re.compile(r"^\s*\|(\s*---\s*\|)+\s*$")
_INDEX = {}


def doc_text():
    with open(DOC, "rb") as f:
        return f.read().decode("utf-8", "replace")


def index():
    """裸文件名也是一种写法，不等于文件不在——限定目录建一次索引，同一次跑里复用。"""
    if not _INDEX:
        for base in (HERE, os.path.join(PKG, "docs"), os.path.join(_ws, "输出", "_logs"),
                     os.path.join(_ws, "输出", "_scratch"), os.path.join(_ws, "输出", "交付"),
                     os.path.join(_ws, "docs"), os.path.join(PKG, os.pardir, "ACPs-community")):
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in (".venv", "__pycache__", "node_modules")]
                for fn in filenames:
                    _INDEX.setdefault(fn, os.path.join(dirpath, fn))
    return _INDEX


def tables(text):
    """⇒ [(表头格 list, [(行号, 整行) ...])]，只认「| … |」行＋紧跟的分隔行。"""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and SEP_ROW.match(lines[i + 1]):
            rows, r = [], i + 2
            while r < len(lines) and lines[r].lstrip().startswith("|"):
                rows.append((r + 1, lines[r]))
                r += 1
            out.append((cells(lines[i]), rows))
            i = r
        else:
            i += 1
    return out


def set_cell(ln, k, val):
    """按**位置**替换第 k 格（0 起）——按字符串 replace 会先撞上别处同字的散文。"""
    pos = list(seps(ln))          # seps() 给的已经是位置整数（与 §1 那把尺同一个口径）
    return ln[:pos[k] + 1] + " " + val + " " + ln[pos[k + 1]:]


def plain(cell):
    """markdown 强调星号不是状态的一部分：`**部分**（…）` 与 `部分（…）` 必须同样作数。"""
    return cell.strip().strip("*").strip()


def shape_defects(pair):
    head, rows = pair
    want = len(head) + 1
    bad = []
    for n, ln in rows:
        got = len(seps(ln))
        if got != want:
            cl = cells(ln)
            bad.append("第 %d 行（# %s）列分隔符 %d ≠ 表头 %d ⇒ 有一格落在没有表头的列位上"
                       % (n, cl[0] if cl else "?", got, want))
    return bad


def main_defects(pair):
    _head, rows = pair
    ids = [cells(ln)[0].strip() for _n, ln in rows]
    bad = []
    if len(ids) < MAIN_FLOOR:
        bad.append("主表只剩 %d 行（地板 %d）＝有人整块删行，其余各腿会在空集合上绿灯" % (len(ids), MAIN_FLOOR))
    if sorted(ids) != MAIN_IDS:
        缺 = [k for k in MAIN_IDS if k not in ids]
        重 = sorted({k for k in ids if ids.count(k) > 1})
        bad.append("主表编号应恰好 01..14：缺 %s｜重复 %s" % (缺 or "无", 重 or "无"))
    return bad


def status_defects(pair):
    _head, rows = pair
    bad = []
    for n, ln in rows:
        cl = cells(ln)
        st = plain(cl[-1])
        if not st.startswith(STATUS_WORDS):
            bad.append("第 %d 行（# %s）状态格开头不在口径词表：%r" % (n, cl[0], st[:24]))
        for w in OVERREACH:
            if w in st:
                bad.append("第 %d 行（# %s）状态格出现越口径完成词 %r" % (n, cl[0], w))
    return bad


def incr_defects(main, pair):
    _head, rows = pair
    main_ids = {cells(ln)[0].strip() for _n, ln in main[1]}
    ids = [cells(ln)[0].strip() for _n, ln in rows]
    bad = []
    if len(ids) < INCR_FLOOR:
        bad.append("增量表只剩 %d 行（地板 %d）" % (len(ids), INCR_FLOOR))
    for n, ln in rows:
        cl = cells(ln)
        i, st = plain(cl[0]), plain(cl[-1])
        if i not in main_ids:
            bad.append("增量表第 %d 行编号 %r 不在主表里" % (n, i))
        if not st:
            bad.append("增量表第 %d 行（# %s）状态格是空的" % (n, i))
        elif not st.startswith(INCR_STATUS_OK):
            bad.append("增量表第 %d 行（# %s）状态格开头不在口径词表：%r" % (n, i, st[:24]))
        for w in OVERREACH:
            if w in st:
                bad.append("增量表第 %d 行（# %s）状态格出现越口径完成词 %r" % (n, i, w))
    重 = sorted({k for k in ids if ids.count(k) > 1})
    if 重:
        bad.append("增量表编号重复：%s" % 重)
    return bad


def path_defects(text):
    idx = index()
    bad = []
    for tok in sorted({t.strip() for t in re.findall(r"`([^`\n]+)`", text) if PATH_RE.match(t.strip())}):
        p = tok.split(":")[0].replace("\\", "/")
        if os.path.isfile(os.path.join(PKG, p)) or os.path.isfile(os.path.join(_ws, p)):
            continue
        if os.path.basename(p) in idx:
            continue
        bad.append("点名的证据不在盘上：%s" % tok)
    return bad


# ───────────────────────────────── 用例
def test_case_table_present_and_two_tables():
    assert os.path.isfile(DOC), "状态表不在盘上：%s（本门的凭据物就是这份文件）" % DOC
    tbs = tables(doc_text())
    assert len(tbs) == 2, "应读到 2 张表（主表＋轮 312 增量表），实际 %d 张" % len(tbs)
    assert [h[0] for h, _ in tbs] == ["#", "#"], "两张表首列都应是编号：%s" % [h for h, _ in tbs]


def test_case_table_shape_both_tables():
    tbs = tables(doc_text())
    bad = shape_defects(tbs[0]) + shape_defects(tbs[1])
    assert not bad, "有数据行的列数与自己的表头不符：\n" + "\n".join(bad)
    head, rows = tbs[1]
    n, ln = rows[0]
    # 植入先自证"列数真的变了"——把行尾竖线挪位（[:-1] + " |"）是 no-op，红不出来还看着像测过
    assert len(seps(ln[:-1])) == len(seps(ln)) - 1, "植入没减掉一列＝这条植入是空的"
    one_short = (head, [(n, ln[:-1])])
    assert shape_defects(one_short), "植入：行尾少一列却不红＝这条腿是空的"
    one_extra = (head, [(n, ln + " 多出来的一格 |")])
    assert len(seps(one_extra[1][0][1])) == len(seps(ln)) + 1, "植入没多出一列＝这条植入是空的"
    assert shape_defects(one_extra), "植入：多一格（本轮实测到的那个形状）却不红"
    assert not shape_defects((head, [(k, ln.replace("**", "")) for k, ln in rows])), \
        "等价改写（去掉加粗星号）不该红＝判据太窄"


def test_case_table_main_ids_complete():
    tbs = tables(doc_text())
    assert not main_defects(tbs[0]), main_defects(tbs[0])
    head, rows = tbs[0]
    assert main_defects((head, rows[1:])), "植入：删掉一行（少一条用例）应当红"
    assert main_defects((head, rows + [(rows[6][0], rows[6][1])])), "植入：同一编号出现两次不红＝没在判重复"
    seven = [(n, set_cell(ln, 0, "7") if cells(ln)[0].strip() == "07" else ln) for n, ln in rows]
    assert main_defects((head, seven)), "植入：把 07 写成 7（编号口径不齐）不红＝没在对齐编号"


def test_case_table_status_vocabulary():
    tbs = tables(doc_text())
    bad = status_defects(tbs[0])
    assert not bad, "主表状态列越口径：\n" + "\n".join(bad)
    head, rows = tbs[0]
    over = [(n, set_cell(ln, len(cells(ln)) - 1, "已验证通过")) for n, ln in rows]
    assert status_defects((head, over)), "植入：状态格改成『已验证通过』不红＝完成性表述没人拦"
    void = [(n, set_cell(ln, len(cells(ln)) - 1, "")) for n, ln in rows]
    assert status_defects((head, void)), "植入：状态格留空不红＝这列只是装饰"
    ok = [(n, set_cell(ln, len(cells(ln)) - 1, cells(ln)[-1].strip() + "（离线）")) for n, ln in rows]
    assert not status_defects((head, ok)), "加限定语（离线）不该红＝判据太窄"


def test_case_table_increment_rows_consistent():
    tbs = tables(doc_text())
    main, incr = tbs[0], tbs[1]
    bad = incr_defects(main, incr)
    assert not bad, "轮 312 增量表有缺陷：\n" + "\n".join(bad)
    head, rows = incr
    stray = rows + [(rows[0][0] + 1, "| 99 | 编的一条 | 没有证据 | 已测 |")]
    assert incr_defects(main, (head, stray)), "植入：增量表出现主表没有的编号却不红"
    dup = rows + [(rows[0][0], rows[0][1])]
    assert incr_defects(main, (head, dup)), "植入：增量表编号重复却不红"
    n, ln = rows[0]
    done = [(n, set_cell(ln, len(cells(ln)) - 1, "线上已核"))] + rows[1:]
    assert incr_defects(main, (head, done)), "植入：增量表写『线上已核』不红＝越口径表述没人拦"
    blank = [(k, set_cell(l, len(cells(l)) - 1, "")) for k, l in rows]
    assert incr_defects(main, (head, blank)), "植入：增量表状态格全部留空却不红"


def test_case_table_evidence_paths_exist():
    text = doc_text()
    bad = path_defects(text)
    assert not bad, "表里点名的证据不在盘上（读表的人按名找不到凭据）：\n" + "\n".join(bad)
    assert len(re.findall(r"test_partner_echo_layering_r311\.py", text)) >= 1, \
        "植入件的前提没了（这枚点名用例不在文中），下面两条植入会假过"
    fake = text.replace("test_partner_echo_layering_r311.py", "test_partner_echo_layering_r999.py", 1)
    assert len(path_defects(fake)) == 1, "植入：把一份点名用例改成不存在的名字，应恰好报 1 条"
    fake_log = text.replace("r311_p1_topic_echo.log", "r311_p1_topic_echo_absent.log", 1)
    assert len(path_defects(fake_log)) == 1, "植入：把一枚点名日志改成不存在的，应恰好报 1 条"


def test_case_table_floors_against_thinning():
    tbs = tables(doc_text())
    assert main_defects((tbs[0][0], [])), "植入：主表整块看不见却不红＝地板是装饰"
    assert incr_defects(tbs[0], (tbs[1][0], [])), "植入：增量表整块看不见却不红"
    assert len(tbs[0][1]) >= MAIN_FLOOR and len(tbs[1][1]) >= INCR_FLOOR, \
        "现役两表的行数已低于地板——先查是不是有人删了整段"
