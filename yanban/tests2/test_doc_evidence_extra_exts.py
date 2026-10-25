"""常驻判据：把「同前缀、但扩展名不在 `PATH_RE` 那六个里」这一族证据路径接进检查，**只允许逐条申报**消红。

为什么单独开这一台而不去改 `test_doc_evidence_paths.py`：那台门文件的 `:57`、`:135` 两处行号被两份文档钉着
引用（轮 312 实测：RUNTIME_STATE 2 处＋UNDERMIND_BORROW 5 处），往里加用例会挪行号；所以新判据另立一档，
但**提取与解析路径复用那台门自己的函数**（同一把尺，不养第二份口径——台账 未决 83 那条同源腿的教训）。

判据形状（五腿）：
  1 仪器接上了：本仓 docs/*.md 里这一族的**对数**过地板（轮 312 现读 148 处／去重 131 条，地板取 120）。
  2 主判据：每条「不在盘上、也不在豁免名单里」的 extra 路径，都必须在申报清单里逐条出现（少一条申报＝红）。
  3 反向判据：申报清单里每条**此刻必须仍不在盘上**——真出现了就红在「申报与实况不符」，不许静默放过。
  4 分档守恒：头行声明的条数／typo 数必须与实列逐条相符（少列、多列、重复列都红），且 **typo 档不许超过上限**
     （typo＝文档把名字写错，那是缺陷不是豁免；涨一条就红一次，逼人来修文档而不是往清单里塞）。
  5 不许挪族绕过：申报的路径若带**受管六扩展名**之一（本该由主门管），判红——否则「把 .md 挪到这份清单里」
     就成了绕过主门的后门。两族的扩展名集合必须互斥（同一用例里断言）。
下面另有六枚**内存植入**（E1–E6）逐腿证明"这条判据真的会红"，不碰盘上任何文件；等价改写不红。
其中 E6 证的是这份清单**在承重**：把申报全摘掉，现读那 15 条必须一条条变红——
一份"摘掉也不影响结局"的清单就是装饰，不是判据。

现读来源（可复算）：`输出/_logs/r312_p162_measure_extra_for_decls.log`，探针 `输出/_scratch/r312_p122_measure_extra_fixed.py`。
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_gate_for_extra", _HERE / "test_doc_evidence_paths.py")
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)

DECLS_PATH = _gate.ROOT / "docs" / "EVIDENCE_EXTRA_DECLS_20261012.txt"

# 与主门 `PATH_RE` 同形，只差两点：扩展名换成**不受管**的那一族，且补**词尾边界** `(?![\\w.])`。
#   边界不是装饰：轮 312 第一版探针没有它，把 `…jsonl` 截成 `…json`、把 `yanban.db.bak_…` 截成 `yanban.db`，
#   凭空造出两条「点了却不存在」的假路径（台账 未决 84 ③a）。
EXTRA_EXTS = r"txt|json|csv|tsv|yaml|yml|html|htm|docx|bundle|zip|sqlite|db"
EXTRA_RE = re.compile(r"(?:输出|docs|code)/[\w./\-一-鿿]+\.(?:" + EXTRA_EXTS + r")(?![\w.])")
MANAGED_EXTS = ("py", "mjs", "log", "png", "jsonl", "md")     # 与主门那六个同步；不同步由用例 E5 抓

PAIR_FLOOR = 120            # 轮 312 现读 148 处（先量后置，留 ~19% 余量给删文档）
DECL_COUNT = 15             # 申报清单现读
TYPO_CAP = 1                # 「已知错引」上限：只许 g99 那一条，多一条就红

HEAD_RE = re.compile(r"^# 申报头｜条数＝(\d+)｜typo＝(\d+)｜typo上限＝(\d+)", re.M)
DECL_RE = re.compile(r"^# 申报｜路径＝(\S+)｜档＝(clean|typo)｜理由＝(.+?)｜出处＝(.+)$", re.M)


def extra_hits(docs: dict[str, str]) -> list[tuple[str, str]]:
    """(文档, 路径) 对；每份文档内部去重（与主门 `evidence_paths` 同一口径）。"""
    return [(name, p) for name, text in sorted(docs.items()) for p in sorted(set(EXTRA_RE.findall(text)))]


def missing_pairs(pairs: list[tuple[str, str]], exists, exempt) -> list[tuple[str, str]]:
    return [(n, p) for n, p in pairs if not exists(p) and p not in exempt]


def parse_decls(text: str) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    """申报文本 ⇒ (头行声明数, 路径 → 该行字段)。畸形/重复/与头行不符一律抛。

    抛而不是跳过：跳过等于那条申报从来没存在过——守恒式会把它当「没申报」判红一份本来写全了的清单，
    或反过来让「漏写一整行」读成正常（主门那边 `sweep_decls` 同一道理）。
    """
    heads = HEAD_RE.findall(text)
    if len(heads) != 1:
        raise ValueError("申报头应恰好 1 行，实读 %d 行 ⇒ 判据没有分母" % len(heads))
    head = {"条数": int(heads[0][0]), "typo": int(heads[0][1]), "typo上限": int(heads[0][2])}
    decls: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.startswith("# 申报｜"):
            continue
        m = DECL_RE.match(line)
        if not m:
            raise ValueError("畸形申报行（键名漂了或少字段）：%s" % line[:70])
        path, bucket, reason, source = m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()
        if not reason or not source:
            raise ValueError("申报行理由或出处为空：%s" % path)
        if path in decls:
            raise ValueError("同一条路径申报了两次：%s" % path)
        decls[path] = {"档": bucket, "理由": reason, "出处": source}
    if len(decls) != head["条数"]:
        raise ValueError("头行申报 %d 条，实列 %d 条 ⇒ 守恒式不成立" % (head["条数"], len(decls)))
    typo_n = sum(1 for d in decls.values() if d["档"] == "typo")
    if typo_n != head["typo"]:
        raise ValueError("头行 typo＝%d 与实列 %d 条不符" % (head["typo"], typo_n))
    return head, decls


def problems(missing: set[str], decls: dict[str, dict[str, str]], head: dict[str, int], exists) -> list[str]:
    """两向对账的**纯函数**判据（吃集合与 exists，不吃文件 ⇒ 植入能在内存里造）。"""
    bad: list[str] = []
    for p in sorted(missing - set(decls)):
        bad.append("有一条「不在盘上」的 extra 路径没申报：%s" % p)
    for p in sorted(set(decls) - missing):
        if exists(p):
            bad.append("申报「此刻不在盘上」的这条现在在盘上（申报与实况不符）：%s" % p)
        else:
            bad.append("申报了一条此刻并不在文档里缺失的路径（清单漂了，要么删要么改档）：%s" % p)
    for p, d in sorted(decls.items()):
        ext = p.rsplit(".", 1)[-1]
        if ext in MANAGED_EXTS:
            bad.append("申报里混进了受管扩展名（该由主门管，挪到这里＝绕过）：%s" % p)
        if d["档"] not in ("clean", "typo"):
            bad.append("未知档：%s｜档＝%s" % (p, d["档"]))
    typo_n = sum(1 for d in decls.values() if d["档"] == "typo")
    if typo_n > head["typo上限"]:
        bad.append("typo 档 %d 条超过上限 %d ⇒ 又多一处「文档把名字写错」，这条必须被看见而不是被申报"
                   % (typo_n, head["typo上限"]))
    return bad


def _live():
    docs = _gate.doc_texts()
    pairs = extra_hits(docs)
    missing = {p for _n, p in missing_pairs(pairs, _gate.on_disk, _gate.swept_paths())}
    head, decls = parse_decls(DECLS_PATH.read_text(encoding="utf-8"))
    return docs, pairs, missing, head, decls


def test_instrument_is_attached_to_the_family():
    """先证明仪器连上了被测物：这一族确实提到东西（地板），且申报清单解析得动。
    没有这条，正则漂了或 docs glob 坏了会让下面四条全部恒绿。"""
    _docs, pairs, missing, head, decls = _live()
    assert len(pairs) >= PAIR_FLOOR, f"这一族只提到 {len(pairs)} 处（地板 {PAIR_FLOOR}）⇒ 提取器或文档格式变了，先修仪器"
    assert head["条数"] == DECL_COUNT, f"申报清单条数漂了：头行 {head['条数']}（本轮建表时 {DECL_COUNT}）"
    assert missing, "这一族今天一条都不缺 ⇒ 申报清单没有主体，该整台撤掉而不是留着当装饰"


def test_every_absent_extra_path_is_declared_and_stays_absent():
    """主判据＋反向判据（真盘上的两向）。红话里带逐条路径，不写「有问题」这种查不动的话。"""
    _docs, _pairs, missing, head, decls = _live()
    bad = problems(missing, decls, head, _gate.on_disk)
    assert not bad, "；".join(bad)


def test_decls_have_a_source_for_every_path():
    """每条申报都要说得出「是哪份文档点了它」——申报的出处字段必须真能在文档里找到那条路径。"""
    docs, _pairs, missing, _head, decls = _live()
    named_in: dict[str, set[str]] = {}
    for name, text in docs.items():
        for p in EXTRA_RE.findall(text):
            named_in.setdefault(p, set()).add(name)
    bad = []
    for p, d in sorted(decls.items()):
        who = named_in.get(p, set())
        if not who:
            bad.append("申报的这条现在没有任何文档点名（清单过期）：%s" % p)
        elif not all(n in d["出处"] for n in who):
            bad.append("出处没写全（点名它的有 %s，出处栏是「%s」）：%s" % (sorted(who), d["出处"], p))
    assert not bad, "；".join(bad)


def test_extension_sets_are_disjoint():
    """两族扩展名互斥，且 EXTRA_RE 真的不吃受管那六个（否则同一份路径两把尺都管，谁红说不清）。"""
    assert not set(MANAGED_EXTS) & set(EXTRA_EXTS.split("|")), "两族扩展名重叠 ⇒ 豁免面被扩"
    for ext in MANAGED_EXTS:
        assert not EXTRA_RE.findall("输出/_logs/sample." + ext), "EXTRA_RE 把受管扩展名 %s 也吃了" % ext
    assert EXTRA_RE.findall("输出/_scratch/x.json") and EXTRA_RE.findall("code/yanban/docs/evidence/y.txt")


def test_E1_plant_undeclared_absent_path_is_red():
    """植入 E1：凭空塞一条没申报的不存在路径 ⇒ 主判据必须点名它（否则「漏申报」这件事没人看）。"""
    _docs, _pairs, _missing, head, decls = _live()
    ghost = "输出/_scratch/r999_planted_ghost.json"
    bad = problems({ghost}, decls, head, lambda p: False)
    assert any(ghost in b for b in bad), f"植入没被抓住：{bad}"
    assert any("没申报" in b for b in bad), "红话要说得出成因"


def test_E2_plant_declared_path_reappearing_is_red():
    """植入 E2：把某条已申报路径当成「此刻在盘上」⇒ 必须红在「申报与实况不符」。
    这一腿是清单的自我约束：它防的是"申报Once、永远不再回头看"。"""
    _docs, _pairs, missing, head, decls = _live()
    target = sorted(missing)[0]
    bad = problems(missing - {target}, {**decls, target: dict(decls[target])}, head,
                   lambda p, t=target: p == t)
    assert any("申报与实况不符" in b for b in bad), f"反向腿没开火：{bad}"


def test_E3_dropping_one_declaration_line_is_red():
    """植入 E3：申报文本里删掉一整行 ⇒ 要么守恒式先红（头行与实列不符），要么主判据红（少申报）。
    两条路都算抓住；**静默通过**才算这台仪器是装饰。"""
    text = DECLS_PATH.read_text(encoding="utf-8")
    victim = [ln for ln in text.splitlines() if ln.startswith("# 申报｜")][0]
    trimmed = text.replace(victim + "\n", "", 1)
    try:
        head, decls = parse_decls(trimmed)
    except ValueError as exc:
        assert "守恒" in str(exc) or "条" in str(exc)
        return
    _docs, _pairs, missing, _h, _d = _live()
    bad = problems(missing, decls, head, lambda p: False)
    assert bad, "删掉一整行申报后既没守恒红也没主判据红 ⇒ 这条申报从来没被数过"


def test_E4_typo_bucket_cannot_grow_in_silence():
    """植入 E4：把一条 clean 改成 typo（typo 数超上限）⇒ 必须红在「typo 超过上限」。
    上限写的是「多一处文档写错名字就必须多一次解释」，不是把缺陷折进豁免。"""
    _docs, _pairs, missing, head, decls = _live()
    grown = dict(decls)
    key = sorted(k for k, d in decls.items() if d["档"] == "clean")[0]
    grown[key] = {**decls[key], "档": "typo"}
    bad = problems(missing, grown, head, lambda p: False)
    assert any("typo 档" in b for b in bad), f"typo 上限没开火：{bad}"


def test_E5_managed_extension_cannot_be_smuggled_in():
    """植入 E5：申报里混进一条带受管扩展名（.md）的路径 ⇒ 判红。
    没这条的话，「把难解释的那条挪进这份清单」就是绕过主门的后门（主门只认那六个，这里也能"申报"掉）。"""
    _docs, _pairs, missing, head, decls = _live()
    smuggled = {**decls, "docs/某份不存在的手记.md": {"档": "clean", "理由": "想绕过主门", "出处": "UNDERMIND_BORROW.md"}}
    bad = problems(missing, smuggled, head, lambda p: False)
    assert any("受管扩展名" in b for b in bad), f"挪族绕过没被抓住：{bad}"


def test_E6_emptying_the_declaration_list_reddens_every_item():
    """植入 E6：这份清单必须**在承重**——把申报全摘掉，现读每一条缺失都要各自变红。
    一条清单若"摘掉也不改变结局"，它就是装饰；这也是台账 84 里"最便宜的消红动作是放宽断言"的反面保险。"""
    _docs, _pairs, missing, head, _decls = _live()
    head0 = {"条数": 0, "typo": 0, "typo上限": 0}
    bad = problems(missing, {}, head0, _gate.on_disk)
    n = sum(1 for b in bad if "没申报" in b)
    assert n == len(missing) and n >= 1, f"摘光申报只报出 {n} 条，而现读缺失 {len(missing)} 条 ⇒ 有条目没被数进判据"


def test_equivalent_rewrite_does_not_redden():
    """等价对照：把申报清单原样重解析一次、缺失集也原样给一遍 ⇒ 必须**零红**。
    没有这条，`problems` 可能写成恒返回非空（那 E1–E5 全绿也证明不了它会红在对的事上）。"""
    _docs, _pairs, missing, head, decls = _live()
    assert problems(missing, decls, head, _gate.on_disk) == []
