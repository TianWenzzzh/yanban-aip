"""本仓 `docs/*.md` 里、写成**受管语法**（带前缀＋下面 `PATH_RE` 那六个扩展名之一）的每个证据路径，必须真的在盘上。

为什么开这条闸门：那些文档每轮都写着「读数见 输出/_logs/xxx.log」「图见 docs/screenshots/yyy.png」。
但**文件名是最容易写错、也最容易凭空写对不起来的东西**——2026-09 那次我差点把三个根本不存在的探针
文件名写进结论，靠落盘前 `ls` 才发现。人写的证据路径没有机器对账过，就等于「说做了」没有「验了」。

判据形状：
- 🔴 **受管语法是闭集**：只认带 `输出/`、`docs/`、`code/` 前缀**且**扩展名在 `PATH_RE` 那六个（`py/mjs/log/png/jsonl/md`）里的路径串；`docs/` 一律指本仓（`code/yanban/docs`），引用别的仓的证据必须写全 `code/<仓>/…`（轮 196 实测：裸 `docs/` 按本仓解析把邻居仓两份**真在盘上**的文件报成"缺"，是仪器漏了语法不是文档说谎，补齐语法＋写成仓限定路径两头都保住）。
- 🔴 因此本门绿只等于「**受管语法内**没有坏指针」，**不等于「文档里所有路径都对」**。轮 312 现读（加词尾边界后的 `输出/_logs/r312_p123_measure_extra_fixed.log`）：本仓 23 份＋工作空间层那份交接件里另有 148 处点名同前缀但扩展名不受管（`txt/json/db/zip/…`）的路径（去重 131 条），其中 **16 处不在盘上（去重 15 条）**，门对它们一次都没看过；这 15 条里有 **1 条是真缺陷**而非历史清理：`ITERATION_LOG.md` 第 4030 行写的那份 `…/docs/evidence/g99_plan_ab_n24.txt` 不在盘上，而同目录同名后缀只有 `g100_plan_ab_n24.txt`（同一文档第 4085 行用的就是它）⇒ 编号错一格，正是本门的扩展名盲区放过去的。台账 84。
- 🔴 **扫描面只有 `code/yanban/docs/*.md`**，工作空间层那份交接件不在扫描面内；直接把它扩进来会假红一处——它 §2 拿一句话断言本仓 `docs/` 下没有 `HANDOFF.md`，而本门把"被点名"一律当"应当存在"（实测 `输出/_logs/r312_p85_measure_handoff_paths.log`）。补法＝**申报制负清单**（逐条路径＋理由＋出处，反向也要判：真出现了就红在"申报与实况不符"），不是把文件剔出扫描面、也不是放宽判据。
- 同一道门的盲区按两维各记一次：轮 196 记的是**前缀**维（不带前缀的裸文件名提不到，实测去重 48 个 ⇒ 约定"证据路径一律带前缀"）；轮 312 记的是**扩展名**维（上面那两条）。
- 地板先量后置（见下面的实测值）：文档份数、总提取条数、以及证据链主要住着的几份各一条，
  分别防「glob 坏掉」「提取器坏掉」「某一份悄悄没被读」这三种假绿。
"""
from __future__ import annotations

import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]          # code/yanban
PKG = ROOT.parents[1]                                        # 迁移包根（输出/、code/<其它仓>/ 在那一层）
DOCS = ROOT / "docs"

PATH_RE = re.compile(r"(?:输出|docs|code)/[\w./\-一-鿿]+\.(?:py|mjs|log|png|jsonl|md)")

# 轮 196 实测种群：docs/*.md 共 23 份、合计 215 条；
# RUNTIME_STATE 90｜ITERATION_LOG 54｜UNDERMIND_BORROW 51｜YANBAN2_V2_UPGRADE_REPORT 7｜YANBAN_2_FINAL_REPORT 4
DOC_FLOOR = 20
TOTAL_FLOOR = 180
FLOORS = {
    "RUNTIME_STATE.md": 60,
    "ITERATION_LOG.md": 35,
    "UNDERMIND_BORROW.md": 35,
    "YANBAN2_V2_UPGRADE_REPORT.md": 5,
    "YANBAN_2_FINAL_REPORT.md": 3,
}


def doc_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(DOCS.glob("*.md"))}


def evidence_paths(text: str) -> list[str]:
    return sorted(set(PATH_RE.findall(text)))


def resolve(p: str) -> pathlib.Path:
    return (ROOT / p) if p.startswith("docs/") else (PKG / p)


def missing_paths(text: str) -> list[str]:
    return [p for p in evidence_paths(text) if not resolve(p).exists()]


def on_disk(p: str) -> bool:
    """存在性的**布尔**谓词（`trim_problems` 的 `exists` 形参要的就是 bool）。
    🔴 直接把 `resolve` 传进去会静默失效：它返回 Path，`not Path(...)` 恒 False ⇒
    「申报过期」那一支永不报，而正向断言照样绿（轮 282 首跑就是靠下面那条反向自证咬住的）。"""
    return bool(resolve(p).exists())


# 🔴 2026-10-03 21:29→21:51 之间，用户手工清理了 `输出/_scratch/`（整个目录）与 `输出/_logs/`（全部内容）
#    ⇒ 数百条历史证据指针当场失效。处置＝**逐条冻结的名单豁免**，不是放宽判据：
#    名单＝那一刻本闸门自己判红时打印的 missing 集（生成器 `输出/scripts/sweep_inventory_r264.py`，
#    来源日志 `输出/_logs/r264_docpaths.log`），落在 git 受管的 `docs/EVIDENCE_SWEEP_20261003.txt`。
#    豁免只认**逐字出现在名单里**的那一条：这两个前缀下轮 264 之后新增的任何文件名都不在名单里，
#    照样判红 ⇒ 闸门对"未来的读数"不降级。名单自身的形状由下面两条新用例守。
SWEEP = DOCS / "EVIDENCE_SWEEP_20261003.txt"
SWEEP_PREFIXES = ("输出/_logs/", "输出/_scratch/")
SWEEP_FLOOR = 300


def swept_paths() -> set[str]:
    return {ln.strip() for ln in SWEEP.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def unexempted(paths: list[str]) -> list[str]:
    """豁免只发生在这一层，且**只认逐条命中名单**的那一条通道：主判据与下面那条对照用例
    都走这里（轮 264 的植入 I3 抓到过一次：对照若直接测 `missing_paths`，就把"豁免有没有扩面"
    测成了"提取器有没有报缺失"，改成按前缀豁免也不会红）。"""
    swept = swept_paths()
    return [p for p in paths if p not in swept]


# 🔴 未决 52 的落点（轮 282）：豁免名单**自己删过条目**这件事，过去只写在散文注释和提交信息里，
#    于是「名单讲不全自己的来历」（实测 308−303＝5 而注释只申报 3）。下面两个解析器把申报行升成
#    机器可读形状，配对判据＝`test_sweep_trim_declarations_are_exact_and_alive`。
SRC_FROZEN_RE = re.compile(
    r"^# 源集冻结｜条数＝(\d+)｜来源日志＝(\S+)｜来源 sha256\[:12\]＝([0-9a-f]{12})")
DECL_PREFIX = "# 修剪申报｜"
DECL_HEAD_RE = re.compile(
    r"^# 修剪申报｜轮 (\d+)｜被删 (\d+) 条＝([^｜]+)｜成因＝([^｜]+)｜盘上证据＝([^｜]+)")


def sweep_decls(text: str) -> tuple[dict[str, str], list[str]]:
    """解析 `# 修剪申报｜…` ⇒ (被删路径 → 该行原文, 申报的盘上证据路径)。

    畸形形状（键名漂了、`被删 K 条` 与实列条数不符、同一条申报两次）**一律抛**，不静默跳过：
    静默跳过等于那条申报从来没存在过，守恒式会把它当成"没申报"而判红一份本来写全了的名单，
    或反过来让"漏写一整行"读成正常。"""
    decls: dict[str, str] = {}
    evidence: list[str] = []
    for ln in text.splitlines():
        if not ln.startswith(DECL_PREFIX):
            continue
        m = DECL_HEAD_RE.match(ln)
        if not m:
            raise ValueError("畸形申报行（形状不匹配）：%s" % ln[:70])
        k, path_str, ev_str = int(m.group(2)), m.group(3), m.group(5)
        ps = [s.strip() for s in path_str.split("、") if s.strip()]
        if len(ps) != k:
            raise ValueError("「被删 %d 条」与实列 %d 条不符：%s" % (k, len(ps), ln[:70]))
        for p in ps:
            if p in decls:
                raise ValueError("同一条路径被申报删掉两次：%s" % p)
            decls[p] = ln
        evidence += [s.strip() for s in ev_str.split("、") if s.strip()]
    return decls, evidence


def trim_problems(items: set[str], declared: dict[str, str], frozen: int, exists) -> list[str]:
    """守恒式与申报真实性的**纯函数判据**（吃集合不吃文件，好让用例能自己造变异体）。"""
    bad: list[str] = []
    if len(items) + len(declared) != frozen:
        bad.append("守恒式不成立：名单 %d ＋ 已申报被删 %d ＝ %d ≠ 源集冻结 %d"
                   % (len(items), len(declared), len(items) + len(declared), frozen))
    for p in sorted(declared):
        if p in items:
            bad.append("既在名单里又被申报删掉：%s" % p)
        if not p.startswith(SWEEP_PREFIXES):
            bad.append("申报删掉的一条不在两个前缀下（豁免面被扩）：%s" % p)
        if not exists(p):
            bad.append("申报「已删出名单」的那条此刻不在盘上（申报过期）：%s" % p)
    return bad


def test_docs_are_read_and_yield_a_real_population():
    """先证明仪器连上了被测物：读得到 23 份、总量过地板、地板点名的每一份都过自己的地板。
    没有这条，glob 或提取器坏掉会让下面那条恒绿。"""
    texts = doc_texts()
    assert len(texts) >= DOC_FLOOR, f"只读到 {len(texts)} 份 docs/*.md（地板 {DOC_FLOOR}）⇒ 目录或 glob 变了，先修仪器"
    absent = sorted(set(FLOORS) - set(texts))
    assert not absent, f"地板点名的文档不在盘上了：{absent}"
    total = sum(len(evidence_paths(t)) for t in texts.values())
    assert total >= TOTAL_FLOOR, f"全部文档只提取到 {total} 条证据路径（地板 {TOTAL_FLOOR}）⇒ 提取器或文档格式变了，先修仪器"
    for name, floor in sorted(FLOORS.items()):
        got = len(evidence_paths(texts[name]))
        assert got >= floor, f"{name} 只提取到 {got} 条（地板 {floor}）⇒ 这一份没真被读到"


def test_every_evidence_path_named_in_the_docs_exists():
    bad = {n: unexempted(missing_paths(t)) for n, t in doc_texts().items()}
    bad = {n: m for n, m in bad.items() if m}
    assert not bad, "文档点了不存在的证据文件（这就是'说做了没验了'）：" + "；".join(
        f"{n} → {'、'.join(m)}" for n, m in sorted(bad.items()))


def test_checker_would_fire_on_a_renamed_path_and_stays_quiet_on_good_ones():
    """负向 + 正向对照放同一条：只喂合成文本，不碰真文档。
    没有它，`missing_paths` 可能恒返回 []（上一条也就跟着恒真）。"""
    good = next(p for t in doc_texts().values() for p in evidence_paths(t) if p.startswith("输出/"))
    assert missing_paths(f"读数见 {good}") == [], "真存在的路径被判成缺失 ⇒ 解析/根目录错了，不是文档的错"
    d = pathlib.PurePosixPath(good)
    bogus = f"{d.parent}/r000_这条本来不存在.{d.suffix.lstrip('.')}"
    assert missing_paths(f"读数见 {bogus}") == [bogus], "改个名都没抓到 ⇒ 这条闸门是装饰"


def test_cross_repo_path_resolves_against_the_named_repo_and_still_bites():
    """`code/<仓>/…` 按迁移包根解析：存在就放过，改个名就咬。
    没有这条，扩进语法的 `code/` 前缀可能实际一条都提不到，而存在性判据跟着假绿。"""
    good = next((p for t in doc_texts().values() for p in evidence_paths(t) if p.startswith("code/")), None)
    assert good is not None, "没有任何一份文档写跨仓 `code/…` 证据路径 ⇒ 语法又漂了，先确认这条判据还连得上东西"
    assert resolve(good).exists(), f"{good} 解析不到盘上（根目录拼错了？）"
    assert missing_paths(f"读数见 {good}") == []
    d = pathlib.PurePosixPath(good)
    bogus = f"{d.parent}/r000_这条本来不存在.{d.suffix.lstrip('.')}"
    assert missing_paths(f"读数见 {bogus}") == [bogus], "跨仓路径改个名没抓到 ⇒ 那一支语法是装饰"


def test_sweep_exemption_is_narrow_and_load_bearing():
    """清扫豁免自己得被守住——它是一条**会让闸门少判红**的口子，所以三件事都得有牙：
      甲 形状：名单非空且过地板、每条都在两个前缀下、且**此刻确实不在盘上**
         （名单里出现一个还存在的路径 ⇒ 它在宣布没丢的东西丢了；出现前缀外的一条 ⇒ 豁免在偷偷扩面）。
      乙 承重：从名单里拿掉任意一条，那一条必须重新判红（证明闸门真的在读这份名单，不是摆设）。
      丙 不扩面：同样在 `输出/_logs/` 下、但**不在名单里**的一个不存在文件名，必须照旧判红
         （证明豁免是"逐条"而不是"按前缀"——轮 264 之后的新读数仍然受这条闸门保护）。
    """
    swept = swept_paths()
    assert len(swept) >= SWEEP_FLOOR, f"名单只有 {len(swept)} 条（地板 {SWEEP_FLOOR}）⇒ 名单被截断或读错文件"
    outside = sorted(p for p in swept if not p.startswith(SWEEP_PREFIXES))
    assert not outside, f"名单里有 {len(outside)} 条不在两个前缀下（前 3：{outside[:3]}）⇒ 豁免面被扩大，先收窄"
    alive = sorted(p for p in swept if resolve(p).exists())
    assert not alive, f"名单里有 {len(alive)} 条此刻又在盘上（前 3：{alive[:3]}）⇒ 名单过期，把回来的那些删掉"

    listed = sorted(swept)[0]
    assert missing_paths(f"读数见 {listed}") == [listed], "名单里这一条没被判成缺失 ⇒ 提取器与名单不同形，豁免无从生效"
    assert unexempted([listed]) == [], "名单内这条没被豁免掉 ⇒ 豁免没接上"
    test_every_evidence_path_named_in_the_docs_exists()   # 乙 承重：名单在盘上却判红 ⇒ 主判据没在用豁免
    d = pathlib.PurePosixPath(listed)
    unlisted = f"{d.parent}/r000_这条本来不存在.log"
    assert unlisted not in swept, "合成的那条恰好撞进名单 ⇒ 换名字再测"
    assert missing_paths(f"读数见 {unlisted}") == [unlisted], "名单外的路径连提取都没抓到 ⇒ 后面那步无从判起"
    assert unexempted(missing_paths(f"读数见 {unlisted}")) == [unlisted], \
        "前缀内、名单外的路径没被判红 ⇒ 豁免按前缀生效了（判据已废）"


def test_sweep_trim_declarations_are_exact_and_alive():
    """**守恒式闸门**（未决 52 的落点，轮 282）：名单每一次「修剪」都必须留下能被算回来的申报。

    为什么不是"再读一遍名单"：轮 281 实测 源集 308 − 名单 303 ＝ 5，而注释只申报了轮 274 那 3 条，
    轮 275 删的 2 条只写在提交信息 `572d9ed` 里 ⇒ **文件讲不全自己的来历**，且没有任何一层会红。
    判据（跑前写死，全部只读受管文件＋盘上存在性，不依赖那份不受 git 管的源日志）：
      甲 守恒：`名单条数 ＋ 已申报被删条数 ＝＝ 「# 源集冻结｜」那行写的源集条数`（少申报／多申报都红）；
      乙 每条申报的路径**此刻必须在盘上**（它被删出名单的理由就是"它回来了"，不回来＝申报过期）；
      丙 一条不可能既在名单里又被申报删掉；申报路径必须落在 `SWEEP_PREFIXES` 两个前缀下（不扩面）；
      丁 权威行恰好 1 条、申报行形状可解析（`轮 N｜被删 K 条＝…｜成因＝…｜盘上证据＝…`，K 要等于实列条数，
         且键名漂了／条数不符／重复申报都必须抛），申报引用的盘上证据文件此刻在盘上（"说留了档其实没档"是同族事故）；
      戊 源日志在盘上时校它的 sha256[:12]（它不是 git 受管物 ⇒ 不在盘上只申报不判，在场就必须对得上）。
    三枚自证（都在本用例内，不另起文件）：拿掉一条申报 ⇒ 甲必红；把申报路径换成没回盘的名 ⇒ 乙必红；
    只改排版（`、` → `、 `）⇒ 解析集合与判定结果都不许变（提取器不许按排版取数）。
    """
    text = SWEEP.read_text(encoding="utf-8")
    items = swept_paths()
    frozen_line = [ln for ln in text.splitlines() if SRC_FROZEN_RE.match(ln)]
    assert len(frozen_line) == 1, \
        f"「# 源集冻结｜…」权威行命中 {len(frozen_line)} 条（要求恰好 1）⇒ 守恒式没有唯一权威值"
    fm = SRC_FROZEN_RE.match(frozen_line[0])
    frozen, src_log, src_sha = int(fm.group(1)), fm.group(2), fm.group(3)
    declared, evidence = sweep_decls(text)
    assert trim_problems(items, declared, frozen, on_disk) == [], "甲/乙/丙 判据不通过"
    for ev in evidence:
        assert resolve(ev).exists(), f"申报里写的盘上证据不在盘上：{ev}（说留了档其实没档）"
    sp = resolve(src_log)
    if sp.exists():
        assert hashlib.sha256(sp.read_bytes()).hexdigest()[:12] == src_sha, \
            "来源日志被改写/换掉而「源集冻结」那行没同轮换 ⇒ 守恒式的权威值已过期"
    else:
        assert src_log.startswith(SWEEP_PREFIXES), f"申报的源日志 {src_log} 不在两个前缀下"

    victim = sorted(declared)[0]
    k_victim = len([s for s in DECL_HEAD_RE.match(declared[victim]).group(3).split("、") if s.strip()])

    d = pathlib.PurePosixPath(victim)
    ghost = f"{d.parent.as_posix()}/r999_这条没回盘.log"
    assert ghost not in items and not resolve(ghost).exists(), "合成的那条恰好撞进名单或在盘上 ⇒ 换名字再测"
    assert (on_disk(victim), on_disk(ghost)) == (True, False), \
        "存在性谓词本身没分辨力 ⇒ 乙那支会恒不报（Path 当 bool 用的同族事故）"

    fewer = {k: v for k, v in declared.items() if k != victim}
    pb = trim_problems(items, fewer, frozen, on_disk)
    assert pb and any("守恒式" in s for s in pb), f"承重不成立：拿掉一条申报没让甲红 ⇒ 这条判据是装饰：{pb}"

    swapped = {k: v for k, v in declared.items() if k != victim}
    swapped[ghost] = declared[victim]
    pb = trim_problems(items, swapped, frozen, on_disk)
    assert any("不在盘上" in s for s in pb), f"反向不成立：申报换成没回盘的名却没红 ⇒ 乙没牙：{pb}"

    line = declared[victim]
    for mline, label in ((line.replace("｜盘上证据＝", "｜证据＝"), "键名漂了"),
                         (line.replace("｜被删 %d 条＝" % k_victim, "｜被删 %d 条＝" % (k_victim + 1)),
                          "申报条数与实列不符")):
        try:
            sweep_decls(text.replace(line, mline))
        except ValueError:
            pass
        else:
            raise AssertionError("畸形申报行（%s）没被 parser 判出 ⇒ 漏写一整行会被静默当成没申报" % label)

    spaced = "\n".join(ln.replace("、", "、 ") if ln.startswith(DECL_PREFIX) else ln
                       for ln in text.splitlines())
    decl2, ev2 = sweep_decls(spaced)
    assert set(decl2) == set(declared) and ev2 == evidence, "只改排版就换了解析结果 ⇒ 提取器按排版取数"
    assert trim_problems(items, decl2, frozen, on_disk) == [], "等价改写把绿的判成红了"
