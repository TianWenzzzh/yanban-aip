# -*- coding: utf-8 -*-
r"""常驻判据：入口提示词 `docs/新对话提示词-AIC智能体互联.md` 的 §1 现状表**逐格现读对账**。

为什么要接进常驻路径（第十五段三条实测）：
  · 那份提示词是新会话**第一眼**读的东西，§1 每张格子都是"现状"的口径源；
  · 它此前**不在任何判据的读取面内**（`输出/_logs/r312_p422_recheck_outface.log` 现数：全 `tests2` 里
    "新对话提示词"零命中，唯一伸进 `输出/交付` 的门读的是那份能力对照清单）；
  · 于是表里的过期值永远不会变红——第一轮逐格现读就抓到 4 格过期，其中"全量测试"挂着旧 batch 的读数，
    而同一张表的"权威行"已是新数，**两行自相矛盾也没有任何一层看得见**。
`输出/_scratch/r312_p424_reconcile_prompt_s1.py` 是那件事的一次性仪器；本门把同一批判据接进生产路径。

规矩（与 p424 同源，不另起口径）：
  K1 表里的值只当**被检的断言**，绝不当输入；每格都从"它自己的那台仪器"现读：git 子进程／
     RUNTIME_STATE.md 字节／带判定行的日志／盘上 .bak 的 size+sha256／zip 目录项。
  K2 日志只认**带自己判定行的那一档**，同一台仪器取 batch 号最大者（半截日志冒充"最近一次完整产物"吃过亏）。
     这条同时让本门在认证链**跑到的那一刻**自洽：本批日志还没有判定行 ⇒ 取到的是上一批 ⇒ 与表一致。
  K3 取不到就当缺陷，**不许把"没测到"写成"通过"**。
  K4 数格子按**列分隔符位置**，不按 `split("|")`：值格里有内嵌竖线（`CHK|F4 …`），朴素分会把两格读成三格
     ——第十五段就是这样把"第三格被整行替换吃掉"看漏过一次。

腿（每条都另有内存植入；等价改写不红）：
  L1 形状：表体行齐全、每行列分隔符数＝表头、最后一格（怎么复算）非空且带反引号命令形状；
     本机确实没有通道的那几格允许改写成「做不到」声明，但这个出口有上限（INABILITY_CAP），
     否则整张表都能用一句『做不到』免掉复算——上限本身也有一条植入证明它会开火。
  L2 全量测试那格四个数＝最新完整 full 日志（rc 由 L6 逐枚对账：那格写 `G1_FULL_RC=N`
     就必须点名值为 N 的那档日志）。🔴 2026-10-13（第二十一段）拆掉了旧版『rc 必须为 0』那条分支：
     它对『表滞后导致的红跑』无法表达、且会把门**永久锁死**（batch 28 实测：链内全量站红过一次后，
     后续每趟都拿那档红日志当最新档、永远无法自愈），而『认证跑必须绿』由套件自身红度＋复认证流程
     保证、这条腿重复管反而制造死锁；该分支此前**没有配套植入**（属未经验证的分支）。
  L3 权威行那格＝`RUNTIME_STATE.md` 现读那行（**复用 `test_doc_g1_count` 的 `DOC_LINE` 那把尺**，锚定命中必须
     恰好 1 次）。两格之间只判两种真错：全量那趟的分母**超过**权威行分母（已认证的运行不可能比当前采集面还大）、
     两格 deselected 不等（剔除口径是同一个常量）。**不判"相等"**——全量格来自"最近一次完整跑"、权威行格来自
     "此刻的采集面"，加了用例而认证链未重跑的那段窗口里两者本就该差一截；差值由 `generation_gap()` 申报。
  L4 doc＋capability 子集那枚数＝最新完整 doccap 日志的读数。
  L5 普查那格两台仪器的份数＝各自最新完整档的 `n/n 份完好`。
  L6 判定行出处：行内每一枚 `` `XXX_RC=N` `` 必须在**本行点名的那档日志**里现读得到，且该日志在盘上；
     一枚判定行、本行一份日志都没点名＝红（没有落盘凭据的"⇒ RC=0"就是装饰）。
  L7 回滚点那格：逐条 `名字=字节/摘要` 与盘上现算一一对齐；盘上有表里没点名＝红；表里点名盘上没有＝红；
     同名两种断言＝红；声明的"盘上现读 N 份"＝现数份数＝未跟踪里带 .bak 的行数。
  L8 第二副本那格：逐枚 zip 的（条数／未压缩字节／zip 字节）与实物对账，且 `testzip()` 必须返回 None。
  L9 脏项那格：九枚带标签的数逐枚与 git 现读对账（标签**恰好命中 1 次**才作数，少一枚＝这格失去机器可读性）；
     现读侧守恒 跟踪＋未跟踪＋其他＝总行；未跟踪四桶**两两互斥且并集＝全体**（子集相加会超总数）；
     行内 `常驻门 \`glob\` N` 那枚 glob **当场拿 fnmatch 现数**——本轮实测它写着 `tests2/*r312*.py` 却数了
     tests2 全部未跟踪件，标签与数各说各话。
  L10 线上那格不许升格：那台容器本轮没有通道，行内必须同时留着"未部署"与"未复算"两句实话。
  L11 环境那格：Python 与 marked 的三点号版本＝现读。
  地板：表体行数、判定行枚数、.bak 断言枚数、可比 zip 枚数、glob 枚数、脏项标签枚数各设下限——
     防"表被抽薄到只剩两行，于是其余各腿都在空集合上绿灯"。

天花板（写在这里而不是遮掉）：
  · 线上容器现取无通道 ⇒ L10 只判"实话还在不在"，不判那 29020 B 是不是线上实况。
  · `git bundle verify` 与"33 MB／6.4 MB"这类**单位有歧义**的格子不复算；L8 只吃带三条明确数字的 zip 断言。
  · 本门读**工作空间层**的 `docs/`＋`输出/_logs/`（不在 git 仓内）：只用 bundle 恢复出的裸仓会红在
    "入口件不在盘上"——那是声明面缺失，不是产品缺陷；要跑本门就得带上工作空间层。
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent            # code/yanban/tests2
Y = HERE.parent                                           # code/yanban
PKG = Y.parent.parent                                     # 工作空间根
LOGS = PKG / "输出" / "_logs"
PROMPT = PKG / "docs" / "新对话提示词-AIC智能体互联.md"
RT = Y / "docs" / "RUNTIME_STATE.md"

_spec = importlib.util.spec_from_file_location("_g1_ruler", HERE / "test_doc_g1_count.py")
_g1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_g1)
DOC_LINE = _g1.DOC_LINE                                   # 同一把尺：权威行的语法只有一份定义

ROW_NAMES = ("git 分支 / HEAD", "有意未提交的脏项", "全量测试", "权威行", "落盘回读普查",
             "线上（生产）", "回滚点", "第二副本", "环境")
SEP_RE = re.compile(r"(?<![\w])\||\|(?![\w])")
MARKER_RE = re.compile(r"`([A-Z][A-Z0-9_]*_RC)=(\d+)`")
LOG_RE = re.compile(r"[\w\-]*r31[012]_p\d+[A-Za-z0-9_]*\.log")
BAK_CLAIM_RE = re.compile(r"`([^`]+\.bak[^`]*)`=(\d+)/`([0-9a-f]{12})`")
ZIP_CLAIM_RE = re.compile(r"([A-Za-z0-9_]+\.zip)`（(\d+) 条／未压缩 (\d+) B／zip (\d+) B")
GLOB_CLAIM_RE = re.compile(r"常驻门 `([^`]+)` \*{0,2}(\d+)")
FULL4_RE = re.compile(r"\*\*(\d+) passed / (\d+) skipped / (\d+) deselected / (\d+) xfailed")
AUTH_CLAIM_RE = re.compile(r"`G1 pytest=(\d+) passed / (\d+) deselected`")
SUBSET_RE = re.compile(r"子集 (\d+) passed")
CENSUS_RE = re.compile(r"老那台 \*\*(\d+) 份全好.*?新那台 \*\*(\d+) 份全好")
ENV_RE = re.compile(r"Python (\d+\.\d+\.\d+).*?marked (\d+\.\d+\.\d+)")
BAK_COUNT_RE = re.compile(r"盘上现读 \*{0,2}(\d+) 份")
GOOD_FEN_RE = re.compile(r"(\d+) 份完好")

DIRTY_LABELS = (
    ("跟踪改动", r"跟踪改动 \*{0,2}(\d+) 份"),
    ("面内docs", r"面内 `docs/\*\.md` \*{0,2}(\d+) 份"),
    ("未跟踪", r"未跟踪 \*{0,2}(\d+) 份"),
    ("回滚点", r"回滚点 \*{0,2}(\d+)"),
    ("申报清单", r"申报清单 \*{0,2}(\d+)"),
    ("常驻门", r"常驻门 `[^`]+` \*{0,2}(\d+)"),
    ("其余", r"其余 \*{0,2}(\d+)"),
    ("总行", r"porcelain 共 \*{0,2}(\d+) 行"),
    ("其他状态码", r"其他状态码[^｜|]{0,80}?(\d+) 行"),
)
ROW_FLOOR = 8
MARKER_FLOOR = 5
BAK_CLAIM_FLOOR = 9
ZIP_CLAIM_FLOOR = 3
LABELS_EXACT = len(DIRTY_LABELS)
SNAPSHOT_DIRS = ("D:/aic_r312_backup_20261013", "D:/aic_r311_backup_20261012")
# 「怎么复算」那一格的第二个合法形状：本机/本会话确实没有通道时，写明做不到比编一条跑不动的命令诚实。
# 但这个出口必须有上限，否则整张表都会用它躲掉复算。
INABILITY = ("做不到", "无通道", "未复算")
INABILITY_CAP = 2


# ---------------------------------------------------------------- 取数（全部现读）
def read_text(p: pathlib.Path) -> str:
    return p.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def prompt_text() -> str:
    if not PROMPT.is_file():
        raise AssertionError("入口提示词不在盘上：%s——本门的凭据物就是这份文件，缺了不能算通过" % PROMPT)
    return read_text(PROMPT)


def seps(line: str) -> list[int]:
    return sorted(set(m.start() for m in SEP_RE.finditer(line)))


def cells(line: str) -> list[str]:
    """按列分隔符位置切格子（内嵌竖线不算分隔符，见 K4）。"""
    s = seps(line)
    return [line[a + 1:b].strip() for a, b in zip(s, s[1:])]


def section1_rows(text: str) -> tuple[list[tuple[str, int, str]], int, list[str]]:
    """(表体行, 表头列分隔符数, 结构性缺陷)。§1 标题与表头都必须恰好 1 次——两次等于没有权威源。"""
    heads = list(re.finditer(r"^## 1 ", text, re.M))
    if len(heads) != 1:
        return [], 0, ["§1 小节标题应恰好 1 次，实际 %d 次" % len(heads)]
    start = heads[0].start()
    nxt = re.search(r"^## ", text[start + 5:], re.M)
    seg = text[start:start + 5 + (nxt.start() if nxt else len(text))]
    lines = seg.split("\n")
    hdr = [l for l in lines if re.match(r"^\| 项 \|", l)]
    if len(hdr) != 1:
        return [], 0, ["§1 表头应恰好一行，实际 %d 行" % len(hdr)]
    want = len(seps(hdr[0]))
    base = text[:start].count("\n")
    rows = []
    for i, ln in enumerate(lines, base + 1):
        if not ln.startswith("|") or ln == hdr[0] or set(ln.replace("|", "").strip()) <= set("-: "):
            continue
        c = cells(ln)
        rows.append((c[0] if c else "?", i, ln))
    return rows, want, []


_LIVE: dict = {}


def live_porcelain() -> dict:
    if "porcelain" in _LIVE:
        return _LIVE["porcelain"]
    r = subprocess.run(["git", "-C", str(Y), "-c", "core.quotePath=false", "status", "--porcelain"],
                       capture_output=True)
    if r.returncode != 0:
        raise AssertionError("git status 现读不过（RC=%d）——环境红，不是表红" % r.returncode)
    # 只剥尾部换行：porcelain 每行的**前导空格就是状态码**，整串 strip() 会把第一行读成 `M path`
    blob = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n").rstrip("\n")
    pl = [l for l in blob.split("\n") if l.strip()]
    path = lambda l: l[3:].strip().strip('"')
    tracked = [l for l in pl if l.startswith(" M ")]
    untracked = [l for l in pl if l.startswith("?? ")]
    other = [l for l in pl if not l.startswith(" M ") and not l.startswith("?? ")]
    u = [path(l) for l in untracked]
    d = {"total": len(pl), "tracked": len(tracked), "untracked": len(untracked), "other": other,
         "docs_tracked": len([l for l in tracked
                              if path(l).startswith("docs/") and path(l).endswith(".md")]),
         "untracked_paths": u,
         "bak_untracked": sorted(x for x in u if ".bak" in x),
         "decl_untracked": sorted(x for x in u if x.endswith("_DECLS_20261012.txt"))}
    _LIVE["porcelain"] = d
    return d


def live_baks() -> dict:
    if "baks" in _LIVE:
        return _LIVE["baks"]
    skip = {".venv", "node_modules", "__pycache__", ".git", "dist", ".pytest_cache"}
    got = {}
    for dirpath, dirnames, filenames in os.walk(str(Y)):
        dirnames[:] = [x for x in dirnames if x not in skip]
        for fn in filenames:
            if ".bak" in fn:
                p = pathlib.Path(dirpath) / fn
                b = p.read_bytes()
                got[str(p.relative_to(Y)).replace("\\", "/")] = (
                    len(b), hashlib.sha256(b).hexdigest()[:12])
    _LIVE["baks"] = got
    return got


def newest(kind: str, marker: str):
    """K2：只认带自己判定行的那一档，取 batch 号最大者。"""
    key = ("newest", kind)
    if key in _LIVE:
        return _LIVE[key]
    rx = re.compile(r"r312_p\d+_%s_batch(\d+)\.log$" % kind)
    best = None
    if LOGS.is_dir():
        for fn in sorted(os.listdir(str(LOGS))):
            m = rx.match(fn)
            if not m:
                continue
            body = read_text(LOGS / fn)
            if marker not in body:
                continue
            if best is None or int(m.group(1)) > best[0]:
                best = (int(m.group(1)), fn, body)
    _LIVE[key] = best
    return best


def log_body(fname: str):
    p = LOGS / fname
    return read_text(p) if p.is_file() else None


def pytest_summary(body: str) -> dict:
    def g(pat):
        m = re.search(pat, body)
        return int(m.group(1)) if m else None
    return {"passed": g(r"(\d+) passed"), "skipped": g(r"(\d+) skipped"),
            "deselected": g(r"(\d+) deselected"), "xfailed": g(r"(\d+) xfailed")}


def live_readings() -> dict:
    if "readings" in _LIVE:
        return _LIVE["readings"]
    out = {}
    for kind, marker in (("full", "G1_FULL_RC"), ("sweep_old", "SWEEP_RC"),
                         ("sweep_ext", "SWEEPEXT_RC"), ("doccap", "DOCCAP_RC")):
        best = newest(kind, marker)
        if not best:
            out[kind] = None
            continue
        _b, fn, body = best
        if kind == "full":
            sm = pytest_summary(body)
            rc = re.search(r"G1_FULL_RC=(\d+)", body)
            out[kind] = (sm["passed"], sm["skipped"], sm["deselected"], sm["xfailed"],
                         int(rc.group(1)) if rc else None, fn)
        elif kind == "doccap":
            out[kind] = (pytest_summary(body)["passed"], fn)
        else:
            m = GOOD_FEN_RE.search(body)
            out[kind] = (int(m.group(1)) if m else None, fn)
    _LIVE["readings"] = out
    return out


def live_authority() -> dict:
    if "auth" in _LIVE:
        return _LIVE["auth"]
    if not RT.is_file():
        raise AssertionError("RUNTIME_STATE.md 不在盘上：%s" % RT)
    rt = read_text(RT)
    hits = list(DOC_LINE.finditer(rt))
    out = {"hits": len(hits),
           "pair": (int(hits[0].group(1)), int(hits[0].group(2))) if hits else None,
           "line": (rt[:hits[0].start()].count("\n") + 1) if hits else None}
    _LIVE["auth"] = out
    return out


def copy_zip(name: str):
    """第二副本那格点名的 zip：在两个快照目录里找实物 ⇒ (条目数, 未压缩字节, zip 字节, testzip 坏件)。"""
    for base in SNAPSHOT_DIRS:
        q = os.path.join(base, name)
        if os.path.isfile(q):
            with zipfile.ZipFile(q) as z:
                return {"path": q, "entries": len(z.namelist()),
                        "raw": sum(i.file_size for i in z.infolist()),
                        "bytes": os.path.getsize(q), "bad": z.testzip()}
    return None


def live_env() -> tuple:
    if "env" in _LIVE:
        return _LIVE["env"]
    ver = subprocess.run([sys.executable, "--version"], capture_output=True) \
        .stdout.decode("utf-8", "replace").strip()
    marked = "读不到"
    pj = Y / "webui" / "node_modules" / "marked" / "package.json"
    if pj.is_file():
        try:
            marked = str(json.loads(read_text(pj)).get("version"))
        except Exception:
            marked = "解析失败"
    m = re.search(r"(\d+\.\d+\.\d+)", ver)
    out = (m.group(1) if m else ver, marked)
    _LIVE["env"] = out
    return out


# ---------------------------------------------------------------- 断言解析（纯文本，植入可直接喂）
def line_of(rows, name):
    hit = [r for r in rows if r[0] == name]
    return hit[0][2] if len(hit) == 1 else ""


def claims(text: str) -> dict:
    rows, want, structural = section1_rows(text)
    out = {"rows": rows, "want": want, "structural": list(structural), "markers": [], "parsed": {}}
    counts = {n: len([r for r in rows if r[0] == n]) for n in ROW_NAMES}
    out["missing_rows"] = [n for n, k in counts.items() if k == 0]
    out["dup_rows"] = {n: k for n, k in counts.items() if k > 1}

    for name in ROW_NAMES:
        ln = line_of(rows, name)
        for mk, val in MARKER_RE.findall(ln):
            out["markers"].append((name, mk, int(val),
                                   sorted({os.path.basename(x) for x in LOG_RE.findall(ln)})))

    ln = line_of(rows, "有意未提交的脏项")
    vals, miss = {}, []
    for label, pat in DIRTY_LABELS:
        hits = re.findall(pat, ln)
        if len(hits) != 1:
            miss.append("%s（命中 %d 次）" % (label, len(hits)))
        else:
            vals[label] = int(hits[0])
    out["parsed"]["dirty"] = vals
    out["parsed"]["dirty_missing_labels"] = miss
    out["parsed"]["dirty_globs"] = [(g, int(n)) for g, n in GLOB_CLAIM_RE.findall(ln)]

    f4 = FULL4_RE.findall(line_of(rows, "全量测试"))
    out["parsed"]["full4"] = tuple(int(x) for x in f4[0]) if f4 else None

    ln = line_of(rows, "权威行")
    a = AUTH_CLAIM_RE.findall(ln)
    out["parsed"]["auth"] = (int(a[0][0]), int(a[0][1])) if a else None
    s = SUBSET_RE.findall(ln)
    out["parsed"]["subset"] = int(s[0]) if len(s) == 1 else None
    if len(s) != 1:
        out["structural"].append("权威行那格的『子集 N passed』应恰好 1 枚，实际 %d 枚" % len(s))

    c = CENSUS_RE.findall(line_of(rows, "落盘回读普查"))
    out["parsed"]["census"] = (int(c[0][0]), int(c[0][1])) if c else None

    ln = line_of(rows, "回滚点")
    bc = BAK_CLAIM_RE.findall(ln)
    by_name: dict = {}
    for nm, size, dg in bc:
        by_name.setdefault(nm, set()).add((size, dg))
    out["parsed"]["bak_claims"] = {k: next(iter(v)) for k, v in by_name.items() if len(v) == 1}
    out["parsed"]["bak_conflicts"] = sorted(k for k, v in by_name.items() if len(v) > 1)
    out["parsed"]["bak_raw"] = len(bc)
    nb = BAK_COUNT_RE.findall(ln)
    out["parsed"]["bak_declared"] = int(nb[0]) if len(nb) == 1 else None

    out["parsed"]["zips"] = {n: (int(e), int(raw), int(zb))
                             for n, e, raw, zb in ZIP_CLAIM_RE.findall(line_of(rows, "第二副本"))}
    e = ENV_RE.findall(line_of(rows, "环境"))
    out["parsed"]["env"] = e[0] if e else None
    out["parsed"]["online"] = line_of(rows, "线上（生产）")
    return out


# ---------------------------------------------------------------- 逐腿判据（纯函数，返回缺陷句子）
def shape_defects(rows, want, only=None) -> list[str]:
    bad = []
    for name, i, ln in rows:
        if only and name not in only:
            continue
        cl = cells(ln)
        if len(seps(ln)) != want:
            bad.append("%s（第 %d 行）列分隔符 %d ≠ 表头 %d" % (name, i, len(seps(ln)), want))
        if len(cl) != want - 1:
            bad.append("%s（第 %d 行）只取到 %d 格，应有 %d 格" % (name, i, len(cl), want - 1))
        elif not cl[-1]:
            bad.append("%s（第 %d 行）最后一格（怎么复算）是空的" % (name, i))
        elif "`" not in cl[-1] and not any(w in cl[-1] for w in INABILITY):
            bad.append("%s（第 %d 行）最后一格既没有反引号命令形状、也没有『做不到』声明＝这格没写怎么复算"
                       % (name, i))
    return bad


def inability_defects(rows, want) -> list[str]:
    """第二个合法形状必须有上限：只许少数几格写明"本机没有通道"，否则那个出口会变成默认出口，
    整张表都可以用一句『做不到』免掉复算。"""
    users = []
    for name, _i, ln in rows:
        cl = cells(ln)
        if len(cl) == want - 1 and "`" not in cl[-1] and any(w in cl[-1] for w in INABILITY):
            users.append(name)
    if len(users) > INABILITY_CAP:
        return ["用『做不到』免掉复算命令的行有 %d 行（上限 %d）：%s" % (len(users), INABILITY_CAP, users)]
    return []


def dirty_defects(vals: dict, live: dict) -> list[str]:
    want_map = {"跟踪改动": live["tracked"], "面内docs": live["docs_tracked"],
                "未跟踪": live["untracked"], "回滚点": len(live["bak_untracked"]),
                "申报清单": len(live["decl_untracked"]), "总行": live["total"],
                "其他状态码": len(live["other"])}
    out = []
    for k, v in want_map.items():
        if k in vals and vals[k] != v:
            out.append("%s：表里 %d｜现读 %d" % (k, vals[k], v))
    if live["tracked"] + live["untracked"] + len(live["other"]) != live["total"]:
        out.append("现读侧守恒坏了：%d＋%d＋%d ≠ %d"
                   % (live["tracked"], live["untracked"], len(live["other"]), live["total"]))
    return out


def bucket_defects(vals: dict, globs: list, live: dict) -> list[str]:
    u = set(live["untracked_paths"])
    if len(globs) != 1:
        return ["常驻门 glob 断言应有 1 枚，实际 %d 枚 ⇒ 这格没有可复算的写法" % len(globs)]
    gpat = globs[0][0]
    b = frozenset(live["bak_untracked"])
    dcl = frozenset(live["decl_untracked"])
    gset = frozenset(x for x in u if fnmatch.fnmatch(x, gpat))
    buckets = ((".bak", b), ("申报", dcl), ("glob", gset))
    other = frozenset(u) - b - dcl - gset
    out = []
    for i in range(len(buckets)):
        for j in range(i + 1, len(buckets)):
            both = buckets[i][1] & buckets[j][1]
            if both:
                out.append("未跟踪分桶不互斥：%s∩%s=%s（子集相加会把总数算超）"
                           % (buckets[i][0], buckets[j][0], sorted(both)[:3]))
    if (b | dcl | gset | other) != frozenset(u):
        out.append("四桶并集不等于全体未跟踪（缺 %d 件）" % len(frozenset(u) - (b | dcl | gset | other)))
    for label, got in (("回滚点", len(b)), ("申报清单", len(dcl)), ("其余", len(other))):
        if label in vals and vals[label] != got:
            out.append("%s：表里 %d｜按机制现数 %d" % (label, vals[label], got))
    if len(b) + len(dcl) + len(gset) + len(other) != len(u):
        out.append("分桶相加≠未跟踪总数：%d＋%d＋%d＋%d vs %d"
                   % (len(b), len(dcl), len(gset), len(other), len(u)))
    return out


def glob_defects(globs: list, live: dict) -> list[str]:
    u = set(live["untracked_paths"])
    if not globs:
        return ["这格没有 `常驻门 \\`glob\\` N` 形状的可复算断言"]
    out = []
    for gpat, gnum in globs:
        n = len([x for x in u if fnmatch.fnmatch(x, gpat)])
        if n != gnum:
            out.append("声明 `%s`＝%d 枚，但该 glob 现数是 %d 枚（未跟踪共 %d）"
                       "⇒ 标签写的写法与数的口径不是同一件事" % (gpat, gnum, n, len(u)))
    return out


def full_defects(claimed, live: dict) -> list[str]:
    got = live.get("full")
    if got is None:
        return ["没有任何一档 full 日志带 `G1_FULL_RC=` ⇒ 全量那格无现读凭据"]
    p, s, d, x, _rc, fname = got
    # 2026-10-13（第二十一段）拆掉的旧分支：`rc != 0` 即红。它对『表滞后导致的红跑』无法表达，
    # 且会把门锁死（batch 28 实测：链内全量站红过一次后，后续每趟都拿那档红日志当最新档，永不自愈）。
    # rc 的真值由 L6 provenance 逐枚对账（那格写 G1_FULL_RC=N 就必须点名值为 N 的那档日志）。
    if claimed is None:
        return ["全量测试那格没取到四个数"]
    out = []
    if claimed != (p, s, d, x):
        out.append("全量四数：表里 %s｜最新完整档 %s 现读 (%s, %s, %s, %s)" % (claimed, fname, p, s, d, x))
    return out


def auth_defects(claimed, live_auth: dict) -> list[str]:
    if claimed is None:
        return ["权威行那格没取到 `G1 pytest=N passed / M deselected`"]
    if live_auth["hits"] != 1:
        return ["RUNTIME_STATE.md 的 `^G1 pytest=` 锚定命中 %d 次（应恰好 1 次）" % live_auth["hits"]]
    if claimed != live_auth["pair"]:
        return ["权威行：表里 %s｜RUNTIME_STATE.md 现读 %s（第 %s 行）"
                % (claimed, live_auth["pair"], live_auth["line"])]
    return []


def invariant_defects(full4, auth) -> list[str]:
    """两格的关系是**带代际的**：全量那格来自"最近一次完整跑"，权威行那格来自"此刻的采集面"。
    加了新用例而认证链还没重跑的那段时间里，两者本来就该差一截（差＝新用例数），
    所以这里不判"相等"，只判两种真错：
      · 全量那趟的分母**超过**权威行分母（一次已认证的运行不可能比当前采集面还大）；
      · 两格的 deselected 不等（剔除口径是同一个常量，漂了就有人在改 markers 没改文档）。
    "相等"是复认证之后的状态，由 §1 刷新那一步负责对齐（本腿在日志里申报差值，不冒充判定）。"""
    if not full4 or not auth:
        return ["不变式没法判（全量四数或权威行有一格没取到）"]
    p, s, d, x = full4
    ap, ad = auth
    out = []
    if p + s + x > ap:
        out.append("全量那趟的分母比权威行还大：%d＋%d＋%d＝%d ＞ 权威行 %d"
                   "（已认证的运行不可能超过当前采集面）" % (p, s, x, p + s + x, ap))
    if d != ad:
        out.append("deselected 两格不等：全量 %d｜权威行 %d（剔除口径是同一个常量）" % (d, ad))
    return out


def generation_gap(full4, auth):
    """只申报不判定：两格差几枚用例（＝本代新增未复认证的那批）。"""
    if not full4 or not auth:
        return None
    return auth[0] - (full4[0] + full4[1] + full4[3])


def subset_defects(claimed, live: dict) -> list[str]:
    got = live.get("doccap")
    if got is None:
        return ["没有任何一档 doccap 日志带 `DOCCAP_RC=`"]
    n, fname = got
    if claimed is None:
        return ["子集那枚数没取到"]
    return [] if claimed == n else ["doc＋capability 子集：表里 %d｜最新完整档 %s 现读 %d"
                                    % (claimed, fname, n)]


def census_defects(claimed, live: dict) -> list[str]:
    if claimed is None:
        return ["普查那格没取到两台仪器的份数"]
    out = []
    for slot, idx, kind in (("sweep_old", 0, "老"), ("sweep_ext", 1, "新")):
        got = live.get(slot)
        if got is None:
            out.append("%s那台没有任何一档带判定行（不许读成『没测到所以放过』）" % kind)
            continue
        n, fname = got
        if claimed[idx] != n:
            out.append("%s那台：表里 %d 份｜最新完整档 %s 现读 %d 份" % (kind, claimed[idx], fname, n))
    return out


def provenance_defects(markers: list, logs: dict) -> list[str]:
    out = []
    for name, mk, val, named in markers:
        if not named:
            out.append("%s：声明了 `%s=%d` 却一份日志都没点名 ⇒ 这条判定行没有落盘凭据"
                       % (name, mk, val))
            continue
        found, absent = [], []
        for f in named:
            body = logs.get(f)
            if body is None:
                absent.append(f)
            else:
                found += [int(v) for v in re.findall(re.escape(mk) + r"=(\d+)", body)]
        if absent:
            out.append("%s：`%s` 点名的日志不在盘上 %s" % (name, mk, absent))
        if val not in found:
            out.append("%s：表里写 `%s=%d`，但点名的 %s 里现读到 %s"
                       % (name, mk, val, "、".join(named), found or "没有这一行"))
    return out


def bak_defects(parsed: dict, disk: dict, live: dict) -> list[str]:
    cl = parsed["bak_claims"]
    out = ["回滚点：%s 在这格里有两种不同断言" % k for k in parsed["bak_conflicts"]]
    for nm, (size, dg) in sorted(cl.items()):
        hit = [p for p in disk if p.endswith(nm)]
        if not hit:
            out.append("回滚点：表里点名 %s 但盘上找不到" % nm)
            continue
        v = disk[hit[0]]
        if str(v[0]) != size or v[1] != dg:
            out.append("回滚点：%s 表里 %s/`%s`｜盘上 %d/`%s`" % (hit[0], size, dg, v[0], v[1]))
    unlisted = sorted(p for p in disk if not any(p.endswith(n) for n in cl))
    if unlisted:
        out.append("盘上有、表里没点名的 .bak：%s（回滚点这格不再完备）" % unlisted)
    if parsed["bak_declared"] is None:
        out.append("回滚点：没有 `盘上现读 N 份` 这枚可机读的数")
    else:
        if parsed["bak_declared"] != len(disk):
            out.append("回滚点：表里声明 %d 份｜盘上现数 %d 份" % (parsed["bak_declared"], len(disk)))
        if parsed["bak_declared"] != len(live["bak_untracked"]):
            out.append("回滚点：表里声明 %d 份｜未跟踪里带 .bak 的有 %d 行"
                       % (parsed["bak_declared"], len(live["bak_untracked"])))
    return out


def copy_defects(parsed: dict, copies: dict) -> list[str]:
    out = []
    for nm, (e, raw, zb) in sorted(parsed["zips"].items()):
        got = copies.get(nm)
        if got is None:
            out.append("第二副本：表里点名 %s 但快照目录里没有实物" % nm)
            continue
        if got["bad"] is not None:
            out.append("第二副本：%s testzip 报坏件 %s" % (nm, got["bad"]))
        if (e, raw, zb) != (got["entries"], got["raw"], got["bytes"]):
            out.append("第二副本：%s 表里 (%d, %d, %d)｜实物 (%d, %d, %d)"
                       % (nm, e, raw, zb, got["entries"], got["raw"], got["bytes"]))
    return out


def online_defects(line: str) -> list[str]:
    out = []
    if "未部署" not in line:
        out.append("线上那格没了『未部署』——本轮没往那台容器发过东西，不许把这格读成已上线")
    if "未复算" not in line:
        out.append("线上那格没了『未复算』——没有容器通道时，把存档读数升格成线上现状就是 over-claim")
    return out


def env_defects(claimed, live_env) -> list[str]:
    if claimed is None:
        return ["环境那格没取到 Python／marked 的三点号版本"]
    out = []
    if claimed[0] != live_env[0]:
        out.append("Python：表里 %s｜现读 %s" % (claimed[0], live_env[0]))
    if claimed[1] != live_env[1]:
        out.append("marked：表里 %s｜现读 %s" % (claimed[1], live_env[1]))
    return out


def floor_defects(c: dict) -> list[str]:
    p = c["parsed"]
    out = []
    if len(c["rows"]) < ROW_FLOOR:
        out.append("§1 表体只有 %d 行（地板 %d）＝有人整块删格子，此时其余各腿都在空集合上绿灯"
                   % (len(c["rows"]), ROW_FLOOR))
    if len(c["markers"]) < MARKER_FLOOR:
        out.append("§1 判定行断言只剩 %d 枚（地板 %d）＝出处那条腿没了对象"
                   % (len(c["markers"]), MARKER_FLOOR))
    if len(p["bak_claims"]) + len(p["bak_conflicts"]) < BAK_CLAIM_FLOOR:
        out.append("回滚点逐条断言只剩 %d 枚（地板 %d）"
                   % (len(p["bak_claims"]) + len(p["bak_conflicts"]), BAK_CLAIM_FLOOR))
    if len(p["zips"]) < ZIP_CLAIM_FLOOR:
        out.append("第二副本可比 zip 断言只剩 %d 枚（地板 %d；格式必须统一为『N 条／未压缩 X B／zip Y B』，"
                   "少写一段就等于这枚断言没人复算）" % (len(p["zips"]), ZIP_CLAIM_FLOOR))
    if len(p["dirty_globs"]) != 1:
        out.append("脏项那格的 glob 断言应恰好 1 枚，实际 %d 枚" % len(p["dirty_globs"]))
    if len(p["dirty"]) != LABELS_EXACT:
        out.append("脏项那格可机读的标签只剩 %d／应 %d 枚（缺：%s）"
                   % (len(p["dirty"]), LABELS_EXACT, p["dirty_missing_labels"]))
    return out


# ---------------------------------------------------------------- 用例
def test_prompt_section1_sampled_and_floors_hold():
    c = claims(prompt_text())
    bad = floor_defects(c)
    assert not bad, "§1 这张表的可机读数被抽薄：\n" + "\n".join(bad)
    assert not c["missing_rows"] and not c["dup_rows"], \
        "§1 行名不齐或有重名（缺：%s｜重：%s）——本门按行名取格，重名＝没有权威源" % (
            c["missing_rows"], c["dup_rows"])
    assert not c["structural"], c["structural"]
    assert floor_defects(claims(_shrink(prompt_text()))), "植入：删掉两行，地板应当在"


def test_prompt_section1_shape_and_last_cell_filled():
    c = claims(prompt_text())
    bad = shape_defects(c["rows"], c["want"])
    assert not bad, "§1 表格形状有缺陷（列数／最后一格）：\n" + "\n".join(bad)
    rows, want, _d = section1_rows(_drop_last_cell(prompt_text(), "落盘回读普查"))
    assert shape_defects(rows, want, only={"落盘回读普查"}), \
        "植入：整行替换吃掉最后一格（第十五段就是这样漏的）"
    rows2, want2, _d2 = section1_rows(_blank_last_cell(prompt_text(), "环境"))
    assert shape_defects(rows2, want2, only={"环境"}), "植入：最后一格空着应当红"
    rows3, want3, _d3 = section1_rows(_plain_last_cell(prompt_text(), "权威行"))
    assert shape_defects(rows3, want3, only={"权威行"}), "植入：最后一格既没命令也没声明应当红"
    eq_rows, eq_want = _equivalent_rewrite(prompt_text())
    assert not shape_defects(eq_rows, eq_want), "等价改写（去掉一枚加粗星号）不该红＝判据太窄"
    assert not inability_defects(c["rows"], c["want"]), "真表里『做不到』那一格没超上限"
    many = [("| r%d | v | 本轮没有通道，做不到 |" % k, k) for k in range(INABILITY_CAP + 1)]
    many_rows = [("r%d" % k, k, ln) for ln, k in many]
    assert inability_defects(many_rows, c["want"]), \
        "植入：%d 行都用『做不到』免掉复算却不红＝那个出口正在变成默认出口" % (INABILITY_CAP + 1)
    assert not shape_defects(many_rows, c["want"]), "植入本身该只红在上限那一腿（否则两腿混在一起）"


def test_prompt_dirty_cell_matches_live_porcelain():
    c, live = claims(prompt_text()), live_porcelain()
    bad = dirty_defects(c["parsed"]["dirty"], live) + bucket_defects(
        c["parsed"]["dirty"], c["parsed"]["dirty_globs"], live)
    assert not bad, "脏项那格与 git 现读不符：\n" + "\n".join(bad)
    v = dict(c["parsed"]["dirty"])
    v["未跟踪"] = v["未跟踪"] + 1
    assert dirty_defects(v, live), "植入：未跟踪数挪一格不红＝这格是装饰"
    v2 = dict(c["parsed"]["dirty"])
    v2["总行"] = v2["总行"] - 1
    assert dirty_defects(v2, live), "植入：总行数不红＝守恒那条腿没接上"
    fake_live = dict(live, other=list(live["other"]) + [" R renamed.py"])
    assert dirty_defects(c["parsed"]["dirty"], fake_live), "植入：现读侧多一条状态码却不红＝守恒式没开火"


def test_prompt_declared_glob_matches_live_count():
    c, live = claims(prompt_text()), live_porcelain()
    bad = glob_defects(c["parsed"]["dirty_globs"], live)
    assert not bad, "脏项那格的 glob 写法与自己声明的数对不上：\n" + "\n".join(bad)
    _pat, num = c["parsed"]["dirty_globs"][0]
    assert glob_defects([("tests2/*never_exists*.py", num)], live), "植入：glob 指向空集应当红"
    assert glob_defects([("*.bak_r31*", 999)], live), "植入：glob 数与声明数不符应当红"
    baks = live["bak_untracked"]
    assert bucket_defects(c["parsed"]["dirty"], [(baks[0], 1)], live), \
        "植入：把 glob 收成与 .bak 桶同名的那一份（两桶相撞）应当红在互斥或相加"


def test_prompt_full_and_authority_rows_are_consistent():
    c, live, auth = claims(prompt_text()), live_readings(), live_authority()
    bad = (full_defects(c["parsed"]["full4"], live) + auth_defects(c["parsed"]["auth"], auth)
           + invariant_defects(c["parsed"]["full4"], auth["pair"])
           + subset_defects(c["parsed"]["subset"], live))
    assert not bad, "全量／权威行／不变式／子集四格现读对账不过：\n" + "\n".join(bad)
    p, s, d, x = c["parsed"]["full4"]
    ap, ad = auth["pair"]
    assert full_defects((p + 1, s, d, x), live), "植入：全量数挪一格不红"
    assert invariant_defects((ap, s, d, x), auth["pair"]), "植入：全量分母超过权威行不红（那条腿是空的）"
    assert invariant_defects((p, s, d + 1, x), auth["pair"]), "植入：deselected 两格不等不红"
    assert not invariant_defects((p, s, d, x), auth["pair"]), "真表此刻不该红（差值是代际，不是缺陷）"
    gap = generation_gap(c["parsed"]["full4"], auth["pair"])
    assert invariant_defects((ap - s - x, s, ad, x), auth["pair"]) == [], \
        "植入：把两格写成同代（相等）应当被判为不红——这条腿只在超过分母时开火"
    assert gap is not None and gap >= 0, "代际差读不出来＝申报也没了"
    assert auth_defects((ap - 1, ad), auth), "植入：权威行少一枚不红"
    assert subset_defects((c["parsed"]["subset"] or 0) + 1, live), "植入：子集数挪一枚不红"
    dead = dict(live, full=None)
    assert full_defects((p, s, d, x), dead), "植入：没有任何完整档时应当红，不许静默放过"
    live_red = dict(live, full=(p, s, d, x, 1, "合成档（内存植入，不落盘）"))
    assert full_defects((p, s, d, x), live_red) == [], \
        "植入：红跑读数照实申报时不该再被这条腿判红（2026-10-13 拆死锁；rc 真值走 L6 provenance）"


def test_prompt_census_cells_match_latest_complete_logs():
    c, live = claims(prompt_text()), live_readings()
    bad = census_defects(c["parsed"]["census"], live)
    assert not bad, "普查那格与两台仪器最新完整档不符：\n" + "\n".join(bad)
    o, e = c["parsed"]["census"]
    assert census_defects((o + 1, e), live), "植入：老那台份数挪一不红＝腿没接上"
    assert census_defects((o, e - 1), live), "植入：新那台份数挪一不红"
    assert census_defects((o, e), dict(live, sweep_ext=None)), "植入：新那台没有完整档应当红"


def test_prompt_rc_markers_have_landed_logs_with_that_value():
    c = claims(prompt_text())
    assert c["markers"], "本门连一枚判定行都没取到＝这条腿在空集合上"
    logs = {f: log_body(f) for _n, _m, _v, named in c["markers"] for f in named}
    bad = provenance_defects(c["markers"], logs)
    assert not bad, "§1 的判定行没有可核的落盘出处（或值不符）：\n" + "\n".join(bad)
    name, mk, val, named = c["markers"][0]
    assert provenance_defects([(name, mk, val, [])], logs), "植入：判定行不点名任何日志应当红"
    assert provenance_defects([(name, mk, val + 1, named)], logs), "植入：把 RC 写成非零应当红"
    assert provenance_defects([(name, mk, val, ["r312_p999_never_written.log"])],
                              {"r312_p999_never_written.log": None}), "植入：点名的日志不在盘上应当红"


def test_prompt_rollback_cells_match_disk():
    c, disk, live = claims(prompt_text()), live_baks(), live_porcelain()
    bad = bak_defects(c["parsed"], disk, live)
    assert not bad, "回滚点那格与盘上现算不符：\n" + "\n".join(bad)
    nm = sorted(c["parsed"]["bak_claims"])[0]
    size, dg = c["parsed"]["bak_claims"][nm]
    for label, mutated in (("改摘要", (size, "0" * 12)), ("改字节", (str(int(size) + 1), dg))):
        p = dict(c["parsed"], bak_claims=dict(c["parsed"]["bak_claims"], **{nm: mutated}))
        d2 = bak_defects(p, disk, live)
        assert d2 and nm in "\n".join(d2), "植入：%s 没有红在这份文件名下（红的是别的成因）" % label
    p2 = dict(c["parsed"], bak_claims={k: v for k, v in c["parsed"]["bak_claims"].items() if k != nm})
    d3 = bak_defects(p2, disk, live)
    assert d3 and nm in "\n".join(d3), "植入：表里少点名一份盘上确有的 .bak 应当红在它名下"
    p3 = dict(c["parsed"], bak_claims=dict(c["parsed"]["bak_claims"],
                                           **{"companion/never_existed.py.bak_r999": ("1", "1" * 12)}))
    assert bak_defects(p3, disk, live), "植入：表里点名一份盘上不存在的 .bak 应当红"


def test_prompt_second_copy_zips_match_reality():
    c = claims(prompt_text())
    copies = {n: copy_zip(n) for n in c["parsed"]["zips"]}
    bad = copy_defects(c["parsed"], copies)
    assert not bad, "第二副本那格与 zip 实物不符：\n" + "\n".join(bad)
    assert len(c["parsed"]["zips"]) >= ZIP_CLAIM_FLOOR, "可比 zip 断言少于地板＝这条腿没对象"
    nm = sorted(c["parsed"]["zips"])[0]
    e, raw, zb = c["parsed"]["zips"][nm]
    for label, tup in (("条数", (e + 1, raw, zb)), ("zip字节", (e, raw, zb + 1)),
                       ("未压缩", (e, raw + 1, zb))):
        d = copy_defects({"zips": {nm: tup}}, copies)
        assert d and nm in "\n".join(d), "植入：%s 挪一不红" % label
    assert copy_defects({"zips": {"yanban_never_packed.zip": (1, 1, 1)}},
                        {"yanban_never_packed.zip": None}), "植入：点名一份盘上没有的 zip 应当红"
    if copies.get(nm):
        broken = dict(copies, **{nm: dict(copies[nm], bad="member.png")})
        assert copy_defects(c["parsed"], broken), "植入：testzip 报坏件应当红"


def test_prompt_online_cell_keeps_its_honest_caveats():
    c = claims(prompt_text())
    bad = online_defects(c["parsed"]["online"])
    assert not bad, "线上那格的实话被删掉了：\n" + "\n".join(bad)
    assert online_defects("容器 200 OK，线上就是这份字节"), "植入：把存档读数升格成线上现状应当红"
    assert not online_defects(c["parsed"]["online"].replace("⚠️", "")), "去掉一枚表情不该红（等价改写）"


def test_prompt_environment_row_matches_live():
    c = claims(prompt_text())
    env = live_env()
    bad = env_defects(c["parsed"]["env"], env)
    assert not bad, "环境那格与现读不符：%s" % bad
    assert env_defects(("3.12.99", env[1]), env), "植入：Python 版本写错应当红"
    assert env_defects((env[0], "9.9.9"), env), "植入：marked 版本写错应当红"


# ---------------------------------------------------------------- 植入构造（纯字符串，不碰盘）
def _edit_rows(text: str, fn) -> str:
    return "\n".join(fn(l) for l in text.split("\n"))


def _shrink(text: str) -> str:
    return _edit_rows(text, lambda l: "" if l.startswith(("| 线上（生产）", "| 环境")) else l)


def _drop_last_cell(text: str, name: str) -> str:
    def f(l):
        if not l.startswith("| " + name + " |"):
            return l
        s = seps(l)
        return l[:s[-2]] + "|"
    return _edit_rows(text, f)


def _blank_last_cell(text: str, name: str) -> str:
    def f(l):
        if not l.startswith("| " + name + " |"):
            return l
        s = seps(l)
        return l[:s[-2] + 1] + " " + l[s[-1]:]
    return _edit_rows(text, f)


def _plain_last_cell(text: str, name: str) -> str:
    return _edit_rows(text, lambda l: l.replace("`", "") if l.startswith("| " + name + " |") else l)


BOLD_DIRTY = re.compile(r"(跟踪改动 )\*\*(\d+ 份)\*\*")


def _equivalent_rewrite(text: str) -> tuple:
    """等价改写：去掉脏项那格**一枚数**的加粗星号（`跟踪改动 **14 份**` ⇒ `跟踪改动 14 份`）。
    列分隔符、枚数、格子内容都不变 ⇒ 判据不该红。这条证明标签匹配吃得到不带星号的同义写法。
    改写落空（形状又变了）就当场报错，不许留一条不再动任何东西的"控制"冒充验证过。"""
    t, n = BOLD_DIRTY.subn(r"\1\2", text, count=1)
    assert n == 1, "等价改写落空：脏项那格的加粗形状已变，这条控制是空的，请连同判据一起改"
    return section1_rows(t)[:2]


# ---------------------------------------------------------------- L12（第二十段新增）：§2 的台账进度声明
# 为什么现有 11 条腿管不到它：本门读的是 §1 **那张表**（`section1_rows` 只吃 `| 项 | 值 |` 行），
# 而"本轮记到 **未决 N**"这句住在 §2 散文里。第二副本、用例数、普查份数都会刷新，台账编号却
# 是**新增一条台账就 +1** 的活值——第十九段结束时实测 §2 还写着 95、权威台账已到 98，
# 入口件自相矛盾而全仓无一枚判据看得见（同族见台账 96：读取面之外的入口件）。
UN = Y / "docs" / "UNDERMIND_BORROW.md"
LEDGER_CLAIM_RE = re.compile(r"记到 \*\*未决 (\d+)\*\*")
LEDGER_ENTRY_RE = re.compile(r"^(\d+)\. \*\*未决 \1", re.M)


def parse_ledger_claim(text: str) -> dict:
    hits = LEDGER_CLAIM_RE.findall(text)
    return {"hits": len(hits), "value": int(hits[0]) if len(hits) == 1 else None}


def live_ledger() -> dict:
    if "ledger" in _LIVE:
        return _LIVE["ledger"]
    if not UN.is_file():
        out = {"exists": False, "entries": 0, "max": None}
    else:
        nums = [int(m.group(1)) for m in LEDGER_ENTRY_RE.finditer(read_text(UN))]
        out = {"exists": True, "entries": len(nums), "max": max(nums) if nums else None}
    _LIVE["ledger"] = out
    return out


def ledger_defects(claimed: dict, live: dict) -> list[str]:
    if claimed["hits"] == 0:
        return ["§2 没有 `记到 **未决 N**` 这枚可机读的声明 ⇒ 台账进度没人复算"]
    if claimed["hits"] > 1:
        return ["那句声明出现 %d 次 ⇒ 不知道以哪一枚为准（一处进度只许一个数）" % claimed["hits"]]
    if claimed["value"] is None:
        return ["那枚声明取不到数字"]
    if not live.get("exists"):
        return ["权威台账不在盘上：%s ⇒ 不许把『读不到』当『通过』（K3）" % UN]
    if live.get("max") is None:
        return ["权威台账里一条 `N. **未决 N**` 都没解析到＝提取器坏了或台账换了形状"]
    if claimed["value"] != live["max"]:
        return ["§2 写着未决 %d｜UNDERMIND 现读最大未决＝%d（台账共 %d 条）"
                % (claimed["value"], live["max"], live["entries"])]
    return []


def test_prompt_ledger_claim_matches_authority_ledger():
    live = live_ledger()
    c = parse_ledger_claim(prompt_text())
    bad = ledger_defects(c, live)
    assert not bad, "§2 那句台账进度与权威台账不等：%s" % bad
    assert ledger_defects({"hits": 1, "value": live["max"] - 1}, live), "植入：进度少记一条不红＝这腿是装饰"
    assert ledger_defects({"hits": 1, "value": live["max"] + 5}, live), "植入：进度虚报五枚不红＝同上"
    assert ledger_defects({"hits": 0, "value": None}, live), "植入：整句声明消失应当红"
    assert ledger_defects({"hits": 2, "value": live["max"]}, live), "植入：同句出现两枚应当红"
    assert ledger_defects({"hits": 1, "value": live["max"]},
                          {"exists": False, "entries": 0, "max": None}), "植入：台账不在盘上却绿灯＝K3 被违反"
    assert ledger_defects({"hits": 1, "value": live["max"]},
                          {"exists": True, "entries": 0, "max": None}), "植入：提取器一条都没解析到应当红"
    same = c["hits"] == 1 and ledger_defects(c, live) == []
    assert same or live["max"] != c["value"], "正向对照不成立：现读与声明不等却没红"

