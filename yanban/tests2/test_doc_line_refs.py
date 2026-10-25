# -*- coding: utf-8 -*-
r"""文档里「`文件:行号`」这一类引用：越界零容忍、指到空行与找不到目标文件走**逐条申报制**（反向也要判）。

为什么是这一族（台账 86 的落地）：主门 `test_doc_evidence_paths.py` 判的是「写成受管语法的**路径**在不在盘上」，
申报族 `test_doc_evidence_extra_exts.py` 判的是「不受管扩展名那条路径在不在盘上」——**两把尺都只看路径，不看后面的 `:行号`**。
所以「路径真在盘上、但那一行是空行／早已越界」这种引用，从前没有任何一层看过。轮 312 现测（`输出/_logs/r312_p195_measure_line_ref_roots_v2.log`）：
扫描面 23 份文档共 **291** 条形如 `xxx.py:NN` 的引用，其中可解析 **272**（指到非空行 251｜指到空行 21｜越界 0），
解析不到 **19**。本门把「空行 21＋找不到 19」登记成清单（`docs/LINEREF_DECLS_20261012.txt`，由测量本身生成，非手抄），
之后任何一条新的漂移当场红。

🔴 本门**不声称**的事（先说清楚，免得下一场把它当内容匹配用）：
 ① 它只看「那一行是不是空行／有没有越界」。**代码往上插一行后，引用往往落在另一行"有字的代码"上**——那种静默错位这一维看不见，
    要的是"引用的行里有没有文档声称的那个符号"（内容匹配），属另一台门；台账 86 ④ 记的就是这条边界。
 ② 解析根是我定的四级次序（受管前缀→本仓相对→包根相对→**本仓内** basename 唯一），
    兄弟仓（`code/team-repo`、`code/ACPs…`）的同名文件**不参与**歧义判定：本仓文档写裸名时指的就是本仓那个文件。
    早先一支探针把索引建在整棵工作空间，于是 159 条被误判成"歧义"——那是仪器选宽了根，不是文档坏（读数 `输出/_logs/r312_p193_measure_line_ref_roots.log`）。
 ③ 扫描面仍只有 `code/yanban/docs/*.md`，工作空间层那份交接件不在内（与主门同一张面，别读成"全仓引用都对"）。
 ④ 清单里那 21 条「指到空行」是**现状登记**，不是"已经修好"：改文档里的数字、还是当初就不该那样引，要人判一次（同 g99 那一格）。
"""
from __future__ import annotations

import functools
import importlib.util
import os
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
ROOT = _HERE.parents[0]                                     # code/yanban
PKG = ROOT.parents[1]                                        # 迁移包根
DOCS = ROOT / "docs"
DECLS = DOCS / "LINEREF_DECLS_20261012.txt"

# 与主门/申报族同一套前缀与扩展名：行号引用可以指向受管扩展名，也可以指向申报族那些
_spec = importlib.util.spec_from_file_location("_gate_for_lineref", _HERE / "test_doc_evidence_paths.py")
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)
_spec2 = importlib.util.spec_from_file_location("_extra_for_lineref", _HERE / "test_doc_evidence_extra_exts.py")
_extra = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(_extra)

EXTS = sorted(set(_extra.MANAGED_EXTS) | set(_extra.EXTRA_EXTS.split("|")))
REF_RE = re.compile(r"([A-Za-z0-9_./\\\-一-鿿]+?\.(?:" + "|".join(re.escape(e) for e in EXTS) + r")):(\d{1,6})")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}

HEAD_RE = re.compile(
    r"^# 条数＝(\d+)｜blank＝(\d+)｜missing＝(\d+)｜range 上限＝0｜undecodable＝(\d+)$", re.M)
DECL_RE = re.compile(
    r"^# 申报｜文档＝(\S+)｜文档行＝(\d+)｜引用＝(\S+?):(\d+)｜档＝(blank|missing)｜理由＝(\S.*)$", re.M)

# 地板：2026-10-12 现测（v2 根）总数 291／RUNTIME_STATE 70／UNDERMIND_BORROW 146／ITERATION_LOG 39
TOTAL_FLOOR = 240
DOC_FLOORS = {"RUNTIME_STATE.md": 60, "UNDERMIND_BORROW.md": 120, "ITERATION_LOG.md": 30}


def _index(roots):
    idx = {}
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                idx.setdefault(fn, []).append(os.path.join(dirpath, fn).replace("\\", "/"))
    return idx


@functools.lru_cache(maxsize=1)
def _repo_index():
    return _index([str(ROOT)])


def resolve_ref(p):
    """四级解析根（顺序即优先级）。返回绝对路径或 None（None＝本门判不了这条的形状，落 missing）。"""
    if os.path.isabs(p) and os.path.exists(p):
        return p.replace("\\", "/")
    if p.startswith(("输出/", "docs/", "code/")):
        q = _gate.resolve(p)
        return str(q) if q.exists() else None
    for cand in (os.path.join(str(ROOT), p), os.path.join(str(PKG), p)):
        if os.path.exists(cand):
            return cand.replace("\\", "/")
    hits = _repo_index().get(os.path.basename(p), [])
    return hits[0] if len(hits) == 1 else None               # 本仓内同名多处 ⇒ 不硬猜


@functools.lru_cache(maxsize=4096)
def read_lines(abs_path):
    try:
        raw = open(abs_path, "rb").read().decode("utf-8")
    except UnicodeDecodeError:
        return None
    return raw.replace("\r\n", "\n").split("\n")


def doc_texts():
    return _gate.doc_texts()


def classify(docs, reader=None):
    """纯函数：docs 是 {文档名: 文本}，reader 是可注入的「绝对路径→行列表」。返回 (分档 dict, 总数)。"""
    reader = reader or read_lines
    live = {"ok": set(), "blank": set(), "range": set(), "missing": set(), "undecodable": set()}
    total = 0
    for name, text in sorted(docs.items()):
        for m in REF_RE.finditer(text):
            total += 1
            key = (name, m.group(1), int(m.group(2)))
            abs_p = resolve_ref(key[1])
            if abs_p is None:
                live["missing"].add(key)
                continue
            ls = reader(abs_p)
            if ls is None:
                live["undecodable"].add(key)
            elif key[2] > len(ls):
                live["range"].add(key)
            elif not ls[key[2] - 1].strip():
                live["blank"].add(key)
            else:
                live["ok"].add(key)
    return live, total


def parse_decls(text):
    m = HEAD_RE.findall(text)
    if len(m) != 1:
        raise ValueError("申报头必须是恰好 1 行（现读 %d 行）" % len(m))
    head = {"条数": int(m[0][0]), "blank": int(m[0][1]), "missing": int(m[0][2]),
            "undecodable": int(m[0][3])}
    decls = {}
    for doc, docline, path, num, kind, why in DECL_RE.findall(text):
        key = (doc, path, int(num))
        if key in decls:
            raise ValueError("同一件事重复申报两条：%s" % (key,))
        if not why.strip():
            raise ValueError("申报行没有理由：%s" % (key,))
        decls[key] = {"档": kind, "文档行": int(docline), "理由": why}
    if len(decls) != head["条数"]:
        raise ValueError("申报条数与实列不符：头 %d／实 %d" % (head["条数"], len(decls)))
    for bucket in ("blank", "missing"):
        got = sum(1 for d in decls.values() if d["档"] == bucket)
        if got != head[bucket]:
            raise ValueError("%s 档数与头行不符：头 %d／实 %d" % (bucket, head[bucket], got))
    return head, decls


def problems(live, head, decls):
    """两向判据：实况里的坏引用必须逐条申报；申报的那条此刻必须真的还坏在那个档上。"""
    bad = []
    for k in sorted((live["blank"] | live["missing"]) - set(decls)):
        kind = "空行" if k in live["blank"] else "找不到"
        bad.append("未申报的%s引用：%s → %s:%d" % (kind, k[0], k[1], k[2]))
    for k in sorted(live["range"]):
        bad.append("越界（这一档零容忍，清单里不许有）：%s → %s:%d" % (k[0], k[1], k[2]))
    for k in sorted(live["undecodable"]):
        bad.append("目标文件解不开（本门没法判它的行）：%s → %s:%d" % (k[0], k[1], k[2]))
    for k in sorted(set(decls) - (live["blank"] | live["missing"])):
        d = decls[k]
        where = ("已消失：文档现在不再这样引了" if k not in (live["ok"] | live["blank"] | live["missing"]
                                                          | live["range"] | live["undecodable"])
                 else "实况已不是申报的那个档")
        bad.append("申报与实况不符（%s）：%s → %s:%d｜申报档＝%s" % (where, k[0], k[1], k[2], d["档"]))
    # 这一条腿是植入 L4 逼出来的：上面两腿都只看「在不在坏集合里」，**没比过档**——
    # 把一条空行申报改成「找不到」，两腿都不开火（键仍在坏集合里、也仍在申报里），清单就悄悄说错了话。
    for k in sorted(set(decls) & (live["blank"] | live["missing"])):
        real = "blank" if k in live["blank"] else "missing"
        if decls[k]["档"] != real:
            bad.append("申报档与实况档不一致：%s → %s:%d｜申报＝%s／实况＝%s"
                       % (k[0], k[1], k[2], decls[k]["档"], real))
    for k in sorted(set(decls) & live["ok"]):
        bad.append("申报成坏、其实指得着了（该把这条从清单删掉）：%s → %s:%d" % (k[0], k[1], k[2]))
    for k in sorted(set(decls) & live["range"]):
        bad.append("申报的是 %s 档、实况却是越界：%s → %s:%d" % (decls[k]["档"], k[0], k[1], k[2]))
    if len(live["undecodable"]) != head["undecodable"]:
        bad.append("解不开的目标数与头行不符：头 %d／实 %d" % (head["undecodable"], len(live["undecodable"])))
    return bad


def anchor_problems(docs, decls):
    """第三向（轮 312 第九段第三批补的腿）：**申报行里那列 `文档行` 必须真指着那条引用**。

    为什么原来两向不够：`problems()` 比的是 (文档, 路径, 行号) 这个**键**在不在坏集合里、以及档对不对，
    `文档行` 只是给人看的落点坐标，没有任何一层判过它。于是台账每插一行、它下面所有申报行的坐标就集体漂一位，
    而门照绿——轮 312 实测漂了 2 行（`输出/_logs/r312_p218_land_entry89.log` 的「MOVE|」行逐条打印过）。
    这条腿把"给人看的坐标"也变成机器状态：漂了就点名，且点名到具体是哪一行现在指着什么。
    """
    bad = []
    for key, d in sorted(decls.items()):
        doc, path, num = key
        text = docs.get(doc)
        if text is None:
            continue
        lines = text.replace("\r\n", "\n").split("\n")
        needle = "%s:%d" % (path, num)
        ln = d["文档行"]
        if not 1 <= ln <= len(lines):
            bad.append("申报的文档行越界（该文档现有 %d 行）：%s → %s" % (len(lines), doc, needle))
        elif needle in lines[ln - 1]:
            continue
        else:
            here = "空行" if not lines[ln - 1].strip() else "另一行内容"
            bad.append("申报的文档行没指着那条引用（第 %d 行是%s）：%s → %s" % (ln, here, doc, needle))
    return bad


def _live():
    docs = doc_texts()
    text = DECLS.read_text(encoding="utf-8")
    head, decls = parse_decls(text)
    live, total = classify(docs)
    return docs, live, total, head, decls


# ————————————————————————————— 用例 —————————————————————————————

def test_instrument_is_attached_to_the_family():
    """地板三条：总数、扫描面里各主要文档的行号引用数——防「正则坏掉」「某份文档悄悄没被读」。"""
    docs, live, total, head, decls = _live()
    assert total >= TOTAL_FLOOR, "行号引用总数只有 %d（地板 %d）⇒ 提取器或扫描面坏了" % (total, TOTAL_FLOOR)
    per = {}
    for name, text in docs.items():
        n = len(REF_RE.findall(text))
        if n:
            per[name] = n
    for doc, floor in DOC_FLOORS.items():
        assert per.get(doc, 0) >= floor, "%s 的行号引用数=%s 低于地板 %d" % (doc, per.get(doc), floor)
    names = list(live)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            both = live[names[i]] & live[names[j]]
            assert not both, "同一条引用落进两档（%s/%s）：%s" % (names[i], names[j], sorted(both)[:2])
    keys = sum(len(v) for v in live.values())
    assert keys <= total, "分档键数 %d > 出现次数 %d ⇒ 分档里混进了没出现过的东西" % (keys, total)


def test_every_bad_line_ref_is_declared_and_stays_bad():
    docs, live, total, head, decls = _live()
    bad = problems(live, head, decls)
    assert not bad, "；".join(bad[:6])


def test_declared_doc_line_anchor_points_at_the_citation():
    """第三向：每条申报的 `文档行` 必须真指着那条引用（坐标漂了就点名，且点名到那一行现在是什么）。"""
    docs, _live_, _t, _h, decls = _live()
    bad = anchor_problems(docs, decls)
    assert not bad, "；".join(bad[:6])


def test_range_bucket_is_zero_tolerance():
    """越界不许有申报档：DECL_RE 只认 blank|missing，越界行连解析都过不去；实况里有越界就必红。"""
    fake = ("# 条数＝1｜blank＝0｜missing＝0｜range 上限＝0｜undecodable＝0\n"
            "# 申报｜文档＝X.md｜文档行＝1｜引用＝a.py:9｜档＝range｜理由＝想给越界开后门\n")
    try:
        parse_decls(fake)
    except ValueError as exc:
        assert "条数" in str(exc) or "档数" in str(exc), exc
    else:
        raise AssertionError("档＝range 的申报行被解析器收了 ⇒ 越界那一档其实没被钉死")
    _docs, live, _t, _h, _d = _live()
    assert not live["range"], "今天就有越界引用：%s" % sorted(live["range"])[:3]


def test_resolution_roots_order_and_no_guessing():
    """解析根要能被复算：受管前缀走主门那条；裸名先本仓；本仓内同名多处 ⇒ 不猜（返回 None）。"""
    assert resolve_ref("docs/RUNTIME_STATE.md").replace("\\", "/").endswith("code/yanban/docs/RUNTIME_STATE.md")
    assert resolve_ref("companion/intent/extractor.py") is not None
    hits = _repo_index().get("__init__.py", [])
    assert len(hits) > 1, "本仓内 __init__.py 只有 %d 处，这条就没测到『歧义不硬猜』" % len(hits)
    assert resolve_ref("__init__.py") is None
    assert resolve_ref("definitely_not_a_real_file_zzz.py") is None


# ————— 六枚内存植入：这台判据必须会开火，且只在「申报跟不上实况」这类事上开火 —————

def _live_copy(live):
    return {k: set(v) for k, v in live.items()}


def test_L1_undeclared_bad_ref_is_red():
    """凭空多出一条「没申报的空行」与一条「没申报的找不到」：主判据必须逐条点名。"""
    _docs, live, _t, head, decls = _live()
    ghost_blank = ("某文档.md", "ghost.py", 9)
    ghost_missing = ("某文档.md", "no_such_dir/no_such.py", 3)
    moved = _live_copy(live)
    moved["blank"].add(ghost_blank)
    moved["missing"].add(ghost_missing)
    bad = problems(moved, head, decls)
    hits = [b for b in bad if "未申报的" in b]
    assert len(hits) == 2, "两条植入应各点一次，实际：%s" % bad[:3]
    assert any("ghost.py" in b for b in hits) and any("no_such.py" in b for b in hits), hits


def test_L2_declared_but_now_resolvable_is_red():
    """申报成「指到空行」的那条，若实况里已经指得着 ⇒ 必须红（清单不会自己过期）。"""
    _docs, live, _t, head, decls = _live()
    key = next(k for k, d in decls.items() if d["档"] == "blank")
    moved = {**live, "blank": live["blank"] - {key}, "ok": live["ok"] | {key}}
    bad = problems(moved, head, decls)
    assert any("申报成坏、其实指得着了" in b for b in bad), bad[:2]


def test_L3_drop_a_decl_line_is_red():
    """摘掉一行申报：either 那条变成「未申报」，either 头行条数与实列不符 ⇒ 两种都必须咬住。"""
    text = DECLS.read_text(encoding="utf-8").splitlines()
    victim = next(i for i, ln in enumerate(text) if ln.startswith("# 申报｜"))
    del text[victim]
    try:
        parse_decls("\n".join(text) + "\n")
    except ValueError as exc:
        assert "条数" in str(exc) or "档数" in str(exc), exc
    else:
        raise AssertionError("摘掉一行申报后头行还守恒 ⇒ 守恒式是假的")


def test_L4_flip_bucket_of_a_decl_is_red():
    """把一条 blank 申报改成 missing（头行的分档数一起改，避开守恒腿）：实况仍是空行 ⇒ 必须红。"""
    text = DECLS.read_text(encoding="utf-8")
    key, d = next((k, v) for k, v in parse_decls(text)[1].items() if v["档"] == "blank")
    old = "# 申报｜文档＝%s｜文档行＝%d｜引用＝%s:%d｜档＝blank" % (key[0], d["文档行"], key[1], key[2])
    new = "# 申报｜文档＝%s｜文档行＝%d｜引用＝%s:%d｜档＝missing" % (key[0], d["文档行"], key[1], key[2])
    assert text.count(old) == 1, "锚点不唯一，植入没打进我要的那一行"
    head_m = HEAD_RE.search(text)
    head_old = head_m.groups()
    head_new = "# 条数＝%s｜blank＝%d｜missing＝%d｜range 上限＝0｜undecodable＝%s" % (
        head_old[0], int(head_old[1]) - 1, int(head_old[2]) + 1, head_old[3])
    planted = text.replace(old, new, 1).replace(head_m.group(0), head_new, 1)
    head2, decls2 = parse_decls(planted)                  # 头行改过，所以守恒腿不该先拦
    assert decls2[key]["档"] == "missing"
    _docs, live, _t, head, _d = _live()
    bad = problems(live, head2, decls2)
    assert any("申报档与实况档不一致" in b and key[1] in b for b in bad), \
        "改档没被比出来（这条腿就是 L4 存在的理由）：%s" % bad[:2]


def test_L5_out_of_range_ref_is_red_even_if_declared():
    _docs, live, _t, head, decls = _live()
    key = next(iter(live["ok"]))
    moved = {**live, "ok": live["ok"] - {key}, "range": live["range"] | {key}}
    bad = problems(moved, head, decls)
    assert any("越界" in b for b in bad), bad[:2]


def test_L6_drift_to_a_blank_line_is_red():
    """把某个「此刻指得着」的引用所在的真实行换成空行 ⇒ 必须当场变红（这才是它承重的证明）。"""
    docs, live, total, head, decls = _live()
    key = next((k for k in live["ok"] if k[1].endswith(".py") and k[2] > 5), None)
    assert key is not None, "样本里没有 .py 的行号引用可用来做漂移植入"
    abs_p = resolve_ref(key[1])
    real = read_lines(abs_p)

    def reader(p):
        if p.replace("\\", "/") != abs_p.replace("\\", "/"):
            return read_lines(p)
        ls = list(real)
        ls[key[2] - 1] = ""                                    # 真行换成空行＝引用漂到空行的那种后果
        return ls

    moved, total2 = classify(docs, reader)
    bad = problems(moved, head, decls)
    assert key in moved["blank"], "植入的那条没被判成空行（植入没落到被测读取的那份状态上）"
    assert any("未申报的" in b for b in bad), bad[:2]
    assert total2 == total, "植入不该改变引用总数：%d→%d" % (total, total2)


def test_L7_shifted_doc_line_anchor_is_red():
    """植入 L7：把一条申报的 `文档行` 挪一位 ⇒ 这条腿必须点名（它存在的理由就是"坐标漂了没人看见"）。"""
    docs, _lv, _t, _h, decls = _live()
    key, d = next((k, v) for k, v in sorted(decls.items()) if docs[k[0]])
    moved = dict(decls)
    moved[key] = dict(d, 文档行=d["文档行"] + 1)
    bad = anchor_problems(docs, moved)
    hits = [x for x in bad if key[0] in x and "%s:%d" % (key[1], key[2]) in x]
    assert len(hits) == 1, "改了一条坐标，应点名一次，实际：%s" % bad[:3]
    assert anchor_problems(docs, decls) == [], "基线就不干净，上面那次开火说明不了问题"


def test_L8_inserting_one_line_drifts_every_anchor_below_it():
    """植入 L8：给某份文档**最上面插一行**（＝台账每插一行的真实后果）⇒
    旧的两向判据照绿（这证明这条新腿不多余），新腿必须把那份文档下面所有申报坐标逐条点名。"""
    docs, live, _t, head, decls = _live()
    name = max({k[0] for k in decls}, key=lambda n: sum(1 for k in decls if k[0] == n))
    mine = [k for k in decls if k[0] == name]
    shifted = dict(docs)
    shifted[name] = "\n" + docs[name]
    live2, total2 = classify(shifted)
    assert not problems(live2, head, decls), "旧两向本该看不见坐标漂移（它红了说明 L8 混进了别的事）"
    assert total2 == sum(1 for _ in REF_RE.finditer("".join(shifted.values())))
    bad = anchor_problems(shifted, decls)
    named = [x for x in bad if "：%s →" % name in x]
    assert len(named) == len(bad), "点名点到了别的文档：%s" % bad[:2]
    assert len(named) >= max(1, len(mine) - 2), \
        "插一行只点出 %d 条（该文档申报 %d 条）⇒ 这条腿的分辨力不够" % (len(named), len(mine))


def scanned_docs():
    """本门行尾自查的那张面＝抽行号引用的那张面（`doc_texts()` 的键），不另立名单——
    两处各写一份「哪些文档算数」，早晚会漂开（漂开的方向是绿的：新文档没人查）。"""
    return sorted(doc_texts())


SCANNED_DOCS = scanned_docs()


def eol_problems(names, raw_of):
    """两套读法必须给同一套行号：磁盘按 `\\n` 切 vs Python 通用换行读。

    防的是本轮那种真实缺陷（`输出/_logs/r312_p242_measure_doc_eol_bytes.log`）：台账每行行尾是
    `\\r\\r\\n`，于是 2627 行的文件被通用换行读成 5252 行——**任何**按行号谈的东西当场失去意义，
    而申报清单那列 `文档行` 是磁盘口径，门把自己的视图喂给它时 39 条只命中 18 条
    （`输出/_logs/r312_p244_diag_decl_basis.log`）。
    🔴 每份文档最多记一条：植入 L9 靠「逐份点名一次」判这条腿的分辨力。
    """
    bad = []
    for name in sorted(names):
        b = raw_of(name)
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            bad.append("%s：不是 UTF-8，行号无从谈起" % name)
            continue
        lf, cr, crlf = text.count("\n"), text.count("\r"), text.count("\r\n")
        if cr != crlf:
            bad.append("%s：%d 个 CR 落在 CRLF 之外（`\\r\\r\\n` 或落单 `\\r`）⇒ 通用换行会把行数翻倍"
                       % (name, cr - crlf))
            continue
        disk = lf + 1
        uni = len(text.splitlines())
        if uni not in (disk, disk - 1):
            bad.append("%s：按 `\\n` 是 %d 行、通用换行读到 %d 行 ⇒ 两套行号口径不等，`文档行` 那列会漂"
                       % (name, disk, uni))
    return bad


def test_scanned_docs_agree_on_line_numbering():
    """被本门按行号引用的一切文档，两套读法必须给同一套行号——否则 `文档行` 那列没有意义。
    🔴 这里**不判**「行尾风格统一」：现测这张面里 6 份纯 LF、1 份 CRLF/LF 混用，它们两套读法一致，
    按风格判会在第一跑就红在非问题上。面本身也不能是空的——空集上的合流是真的，也是没用的。"""
    assert len(SCANNED_DOCS) >= 3, "扫描面只有 %d 份，这条腿没有分辨力" % len(SCANNED_DOCS)
    _docs, _lv, _t, _h, decls = _live()
    missing_face = sorted({k[0] for k in decls} - set(SCANNED_DOCS))
    assert not missing_face, "申报清单点名的文档不在行尾自查面内：%s" % missing_face
    bad = eol_problems(SCANNED_DOCS, lambda name: (DOCS / name).read_bytes())
    assert not bad, "；".join(bad[:4])


def test_L9_doubled_line_endings_are_caught():
    """植入 L9：把整张面的字节换成 `\\r\\r\\n` 行尾（本轮真实缺陷的形状）⇒ 自查必须逐份点名。"""
    def bad_raw(name):
        return re.sub(rb"\r*\n", b"\r\r\n", (DOCS / name).read_bytes())
    assert not eol_problems(SCANNED_DOCS, lambda name: (DOCS / name).read_bytes()), \
        "基线就不干净，下面那次开火说明不了问题"
    bad = eol_problems(SCANNED_DOCS, bad_raw)
    assert len(bad) == len(SCANNED_DOCS), \
        "整张面 %d 份都被改成坏行尾，自查只点出 %d 条：%s" % (len(SCANNED_DOCS), len(bad), bad[:2])
    named = {x.split("：", 1)[0] for x in bad}
    assert named == set(SCANNED_DOCS), "点名的文档与扫描面不符：%s" % sorted(named - set(SCANNED_DOCS))[:2]


def test_equivalent_rewrite_does_not_redden():
    """申报行的顺序与理由里的措辞不参与判据：打乱顺序、把理由换成同义长句，判据仍应零红话。"""
    text = DECLS.read_text(encoding="utf-8")
    head, decls = parse_decls(text)
    lines = [ln for ln in text.splitlines() if ln.startswith("# 申报｜")]
    shuffled = list(reversed(lines))
    rewrote = [ln.replace("理由＝", "理由＝（改写不影响判据）") for ln in shuffled]
    new_text = "\n".join([ln for ln in text.splitlines() if not ln.startswith("# 申报｜")] + rewrote) + "\n"
    head2, decls2 = parse_decls(new_text)
    # 等价改写只该动「顺序」与「理由里的字」——判据看的是 (文档, 路径, 行号, 档)，所以比这四件，不比理由原文
    assert {(k, d["档"]) for k, d in decls2.items()} == {(k, d["档"]) for k, d in decls.items()}, "改写后申报集变了"
    docs, live, _t, _h, _d = _live()
    assert not problems(live, head2, decls2)
