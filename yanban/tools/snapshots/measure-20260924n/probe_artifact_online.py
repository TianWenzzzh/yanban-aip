"""线上验收：产物端点在**云端实例**上真的能出可点击的单文件（不是本地 TestClient 的自证）。

只读：从线上库里挑「有笔记且有论文」的项目，直接 HTTP 打本机 8041 的端点，
数它给出的引用锚点、参考文献条目、是否出现不参与编号的一节，以及有没有混进可执行标签。
不建项目、不写库、不打印任何凭据。
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8041/companion/artifact/"
DB = pathlib.Path("/app/data/yanban.db")
KB = pathlib.Path("/app/data/knowledge")

# 台账封顶取**线上那份产品**的常量，不在这里钉 12：钉死了改产品不会让这台仪器变红，
# 而这条判据的全部意义就是「台账与 headline 不许各说各话」。读不到就直接停，不猜。
try:
    from companion.artifact import _TRAIL_MAX as CAP
except Exception as exc:                       # noqa: BLE001
    print(f"判读：读不到产品里的台账封顶（{type(exc).__name__}: {exc}）⇒ 不猜数值，不出数（RC=2）")
    sys.exit(2)
print(f"台账封顶取自产品常量 _TRAIL_MAX = {CAP}")

# 正对照（先问「被测的东西在不在那里」再问端点好不好）：
# 按论文数挑项目会挑到一批从没生成过报告的探针项目 ⇒ 正文零角标 ⇒ 「无死链」恒真。
# 所以先去 vault 里找**正文真带 [n] 角标**的项目目录，拿它们当验收对象。
cited = []
kb_dirs = [d for d in KB.iterdir() if d.is_dir()] if KB.is_dir() else []
for d in kb_dirs:
    for md in d.glob("*.md"):
        try:
            if re.search(r"\[\d{1,2}\]", md.read_text(encoding="utf-8", errors="replace")):
                cited.append(d.name)
                break
        except OSError:
            continue
print(f"知识库目录 {len(kb_dirs)} 个｜其中正文带 [n] 角标的 {len(cited)} 个"
      f"{'（0 ⇒ 线上没有可验的引用样本，只能报未验收）' if not cited else ''}")

# 判据（跑前写死）：C1 状态 200 且 content-type 是 text/html；C2 参考文献条数 > 0；
# C3 每个 href="#ref-N" 都有对应 id（线上真数据也不许死链）；C4 输出里不出现 <script；
# C5 论文数 > 6 的项目里必须出现「不参与编号」（证明上限真的在截断）；
# C6 「可核对性」块的**计数分支**至少在 1 个样本上开火（0 ⇒ 这一维未验收，恒真的「未测」不算证据）；
# C7 印「未测」的样本里绝不允许出现任何数字百分比（分母为 0 不得伪装成 0%）；
# C8 逐句台账与 headline **对得上账**：台账行数 == min(带出处句数, 12)，
#    且「给出依据的行 + 明说找不到的行」== 台账行数；未截断时「给出依据的行」== 接地数 + 挂错编号数。
#    这条是 R4 的验收：台账少了不算坏（截断），但**少到与 headline 打架**就是产物在自相矛盾。
rows = []
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    rows = con.execute(
        "SELECT p.id, p.name, (SELECT COUNT(*) FROM papers a WHERE a.project_id=p.id) np "
        "FROM projects p ORDER BY np DESC").fetchall()
except sqlite3.OperationalError as exc:
    print(f"判读：线上库结构读不动（{exc}）⇒ 仪器没连上被测物，不出数（RC=2）")
    sys.exit(2)
finally:
    con.close()

order = cited[:12] if cited else [r[0] for r in rows[:12]]
print(f"线上项目共 {len(rows)} 个；本轮按上面的「有角标」名单取 {len(order)} 个当验收对象")
by_id = {r[0]: r for r in rows}
seen: list[bool] = []
checked: list[bool] = []        # 「可核对性」走到计数分支（分母 > 0）的样本
untested_pct: list[str] = []    # 印了「未测」却同时出现数字百分比的样本（C7 的反例）
best = None
DUMP = pathlib.Path("/tmp/g108_artifact.html")
for pid in order[:12]:
    np = by_id.get(pid, (pid, "", 0))[2]
    try:
        with urllib.request.urlopen(BASE + pid, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("content-type", "")
            code = r.status
    except urllib.error.HTTPError as exc:
        print(f"  {pid} 论文{np}｜HTTP {exc.code} {exc.read()[:120]!r}")
        continue
    except Exception as exc:                       # noqa: BLE001 连接类失败要印形状
        print(f"  {pid} 论文{np}｜失败 {type(exc).__name__}: {exc}")
        continue
    hrefs = set(re.findall(r'href="#ref-(\d+)"', body))
    ids = set(re.findall(r'id="ref-(\d+)"', body))
    dead = hrefs - ids
    frac = re.search(r"可核对性</strong>：.*?<strong>(\d+)/(\d+)</strong>", body, re.S)
    unmeasured = bool(re.search(r"可核对性</strong>.*?未测", body, re.S))
    if unmeasured and re.search(r"\d+%", body):
        untested_pct.append(pid)
    label = f"{frac.group(1)}/{frac.group(2)}" if frac else ("未测" if unmeasured else "无块")
    ledger = body.count('<details class="ev">')
    led_ev = body.count("依据：《") + body.count("依据其实在《")
    checked.append(bool(frac))
    print(f"  {pid} 论文{np}｜HTTP {code}｜{ctype}｜{len(body)} 字｜"
          f"参考文献 {len(ids)} 条｜角标 {len(hrefs)} 处｜死链 {len(dead)}｜"
          f"可核对性 {label}｜台账 {ledger} 行（给出依据 {led_ev}）｜"
          f"不参与编号一节={'不参与编号' in body}｜<script={'<script' in body}")
    seen.append(bool(hrefs))
    if frac and DUMP is not None:
        DUMP.write_text(body, encoding="utf-8")     # 留一份真实产物给人看图
        DUMP = None
    if not best and frac:
        best = (pid, np, ids, hrefs, dead, body)
    if not best and len(ids) >= 1:
        best = (pid, np, ids, hrefs, dead, body)
    if not best and hrefs:
        best = (pid, np, ids, hrefs, dead, body)     # 有角标的样本优先当验收对象

# 正对照：零角标的池子上「无死链」是恒真的，不能拿它当「引用可点」的证据。
fired = sum(1 for _p in seen if _p)
print(f"\n角标开火的样本数：{fired} / {len(seen)}（0 ⇒ 「可点击引用」这一维本轮**未验收**）")
print(f"可核对性走到计数分支的样本数：{sum(checked)} / {len(checked)}"
      f"（0 ⇒ 这一维**未验收**：只出「未测」是恒真，不算证据）")

if not best:
    print("判读：没有一个项目产出带参考文献的产物 ⇒ 未验收（RC=2），不等于「功能正常」")
    sys.exit(2)
pid, np, ids, hrefs, dead, body = best
cap_ok = ("不参与编号" in body) if np > 6 else True
print(f"\n选定验收对象：{pid}（库里 {np} 篇）")
print(f"C3 无死锚点：{'✅' if not dead else '❌ ' + str(dead)}"
      + ("（⚠️ 该对象零角标 ⇒ 这条是恒真，不算证据）" if not hrefs else ""))
print(f"C4 无可执行标签：{'✅' if '<script' not in body else '❌'}")
print(f"C5 上限截断（论文>6 时必须有不参与编号一节）：{'✅' if cap_ok else '❌ 实际 ' + str(np) + ' 篇'}")
print(f"C7 未测样本不得印百分比：{'✅' if not untested_pct else '❌ ' + ', '.join(untested_pct)}")
line = re.search(r"可核对性</strong>：(.*?)</p>", body, re.S)
if line:
    print("计数分支原文：", re.sub(r"<[^>]+>", "", line.group(1))[:200])

# C8：台账与 headline 对账（选定对象上做，逐样本那圈只印读数）
sf = re.search(r"可核对性</strong>：.*?<strong>(\d+)/(\d+)</strong>", body, re.S)
smis = re.search(r"(\d+) 句依据其实在别篇", body)
sgap = re.search(r"(\d+) 句整池查无依据", body)
l_rows = body.count('<details class="ev">')
l_ev = body.count("依据：《") + body.count("依据其实在《")
l_gap = body.count("找不到</strong>能回连的原文片段")
if not sf:
    ledger_ok, l_msg = False, "选定对象没有计数分支（选中了「未测」的样本 ⇒ 未验收，换对象重跑）"
else:
    g_n, c_n = int(sf.group(1)), int(sf.group(2))
    mis_n = int(smis.group(1)) if smis else 0
    gap_n = int(sgap.group(1)) if sgap else 0
    want_rows = min(c_n, CAP)
    if l_rows != want_rows:
        ledger_ok, l_msg = False, f"台账 {l_rows} 行，headline 说有 {c_n} 句带出处 ⇒ 应为 {want_rows} 行"
    elif l_ev + l_gap != l_rows:
        ledger_ok, l_msg = False, f"台账 {l_rows} 行里给出依据 {l_ev}、明说找不到 {l_gap} ⇒ {l_rows - l_ev - l_gap} 行两类都不是"
    elif g_n + mis_n + gap_n != c_n:
        ledger_ok, l_msg = False, f"headline 自己就不自洽：接地 {g_n}+挂错 {mis_n}+缺证 {gap_n} ≠ 带出处 {c_n}"
    elif c_n > CAP and f"另有 {c_n - CAP} 句未列出" not in body:
        ledger_ok, l_msg = False, f"截断了 {c_n - CAP} 句却没写「另有 N 句未列出」"
    else:
        ledger_ok = True
        l_msg = (f"台账 {l_rows}/{c_n} 行｜给出依据 {l_ev}（接地 {g_n}+挂错 {mis_n}）"
                 f"｜明说查无 {l_gap}（headline {gap_n}）")
print(f"C8 逐句台账与 headline 对账：{'✅' if ledger_ok else '❌'} {l_msg}")
if not any(seen):
    print("判读：端点线上可用、参考文献与截断都成立，但**这批项目正文里一个角标都没有** ⇒ "
          "「引用可点」这一维线上未验收（零开火的池子不能当证据，轮 115 的老账）。退出码 4。")
    sys.exit(4)
if not any(checked) and not untested_pct:
    print("判读：可核对性块一个样本都没走到计数分支（这批项目的综述里没带角标的句子）⇒ "
          "这一维本轮线上未验收，退出码 4。端点其余判据不受影响。")
    sys.exit(4)
ok = not dead and cap_ok and not untested_pct and ledger_ok
print("判读：" + ("线上产物端点可用：锚点闭合、无死链、无脚本，可核对性计数分支已在真数据上开火，"
                "且逐句台账与 headline 对得上账。" if ok
                else "线上验收未过（死链 / 截断未生效 / 未测样本印了百分比 / 台账与 headline 打架），"
                     "别把 200 当成可用。"))
sys.exit(0 if ok else 1)
