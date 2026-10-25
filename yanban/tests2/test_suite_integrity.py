"""套件自身的完整性闸门（轮 192：两条都是实测抓出来的既有缺陷，不是预防性虚构）。

① 同名 `def test_` 在同一文件里出现两次 —— Python 让后定义的那份**静默盖掉**前面那份。
   现场事实：`tests2/test_dialogue_llm.py` 曾有 535 行（24 条用例）逐字重复了一遍
   （区间 [1155,1689] 与 [1750,2284] 逐行相同，用 `git log -S` 可查到是哪一轮引入）。
   🔴 收集数看不出来：pytest 本来就只收集一次，所以 `^G1 pytest=` 与植入前后都不报警——
   这正是它能活这么久还一路"全绿"的原因。代价是文件看上去比实际多一倍的覆盖，
   而且改前一份的人会以为自己在改测试。
② 用例直接取 `db.get_conn()`（进程级单例）写库 = 写进本地**被 git 跟踪**的 `data/yanban.db`。
   现场事实：上面那条记忆用例每跑一次落 7 行，库里累计 756 行、全在 `t-memory-cap` 名下
   （那个项目连 `projects` 表都不在）。后果不只是脏：轮 191 数"哪些记忆没有出处"时，
   分母里一大半是测试垃圾。

已知限制：②认的是"该文件里出现过 `DB_PATH` 重定向"，不是"每条写库用例都走临时库"——
一个文件只要有一处重定向就算过。收紧到逐用例需要 AST 找调用所在函数，留给后面。
"""
import pathlib
import re

T2 = pathlib.Path(__file__).resolve().parent
DEF = re.compile(r"^def (test_\w+)")


def test_no_shadowed_test_definitions():
    """每个 tests2 文件里的 def test_ 名字必须唯一（重复的那份永远不会跑）。"""
    offenders = []
    scanned = 0
    for f in sorted(T2.glob("*.py")):
        scanned += 1
        seen = {}
        for i, ln in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            m = DEF.match(ln)
            if not m:
                continue
            name = m.group(1)
            if name in seen:
                offenders.append(f"{f.name}::{name} L{seen[name]} 被 L{i} 盖住")
            else:
                seen[name] = i
    assert scanned > 50, f"只扫到 {scanned} 个文件，判据没接上目录"
    assert not offenders, "有同名用例被静默覆盖：\n" + "\n".join(offenders)


def test_db_writing_tests_redirect_the_database():
    """碰过 db.get_conn()/write_event( 的文件必须显式重定向 DB_PATH，别写进被跟踪的库。"""
    offenders, writers = [], []
    for f in sorted(T2.glob("*.py")):
        if f.name == pathlib.Path(__file__).name:
            continue
        s = f.read_text(encoding="utf-8")
        if "get_conn(" not in s and "write_event(" not in s:
            continue
        writers.append(f.name)
        if "DB_PATH" not in s:
            offenders.append(f.name)
    # 正向对照：这条判据确实看得见写库文件（首轮实测 7 个），不是空扫一遍假装绿
    assert len(writers) >= 5, f"只认出 {len(writers)} 个写库文件：{writers}"
    assert not offenders, "这些测试文件写库但没重定向 DB_PATH（会落进被 git 跟踪的 data/yanban.db）：" \
                          f"{offenders}"
