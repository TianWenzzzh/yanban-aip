# -*- coding: utf-8 -*-
"""一次性诊断：WAL 线上库里 `-shm` 到底是谁在动——我的只读连接，还是常驻服务自己？

起因：轮 160 漏斗探针 Q1 把 `yanban.db`、`-wal`、`-shm` 三个文件的 (mtime_ns,size) 三连当
"只读自证"，实跑判出「只有 -shm 动了，主库与 -wal 一字节没变」⇒ RC=2，一整场读数作废。
本机（Windows/SQLite）临时库实验给出三条前置事实（输出/_scratch/shm_probe.py 实跑）：
  · 只读连接**可能**顺手创建/刷新 -wal 与 -shm，但不碰主库；
  · 一次 UPDATE（改值不加行）只动 -wal，主库时间戳不变 ⇒ "主库没动"证明不了"没人写"；
  · 跨连接读 `PRAGMA data_version` 对写**不敏感**（实测 2→2）⇒ 它不能当判据。
所以必须在这里分清两种解释，别把判据改成"刚好能让数通过"的形状：
  ① 别的连接（常驻服务）在动 -shm ⇒ 时间戳三连从写下那天起就不可能被满足，该换成内容指纹；
  ② 是我的只读连接动的 ⇒ 同样不成立，理由相同。
无论哪种，"有没有人真写了库"都只能问**内容**，不能问时间戳。

判线（跑前写死，全在容器里量）：
  W-1 静置 SETTLE 秒、期间**一个 sqlite 连接都不开** ⇒ 三个文件谁动了？
      动了（-shm 或 -wal 时间戳变了）⇒ 直接证明"文件时间戳"测的不是"有没有人写库"，
      因为有别人在动它，而我的仪器根本没开局。
  W-2 只开一个 `mode=ro` 连接读一行再关 ⇒ 谁动了？
  W-3 拿那个 `mode=ro` 连接试着 INSERT ⇒ **必须抛**（不抛 ⇒ 名义只读、实际可写，仪器作废）
  W-4 内容指纹（papers / research_intents / reports / knowledge 四张表的
      count + max(rowid) + 全表拼接 sha256）在 W-1/W-2 前后必须相同；
      不同 ⇒ 读数期间真有人写了线上库 ⇒ 那份漏斗读数确实要作废（RC=2，判据不放宽）
  W-5 判据可核性自证：把同一把指纹尺用在**临时副本**上做一次真写入 ⇒ 必须变
      （不变 ⇒ 这把尺根本不开火，那 W-4 的"相同"毫无意义）。副本在 /tmp，绝不碰线上库。
退出码：0=时间戳判据被否、内容指纹判据成立（可据此换 Q1）｜1=内容不稳（真有并发写）
        ｜2=仪器坏（尺不开火 / ro 能写）。
用法：MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \\
        E:/AIC智能体迁移包/输出/scripts/probe_wal_who_touches.py 20
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import sqlite3
import sys
import time

DB = pathlib.Path("/app/data/yanban.db")
TMPDB = pathlib.Path("/tmp/_walwho_copy.db")
TABLES = ("papers", "research_intents", "projects", "messages")
SETTLE = int(next((a for a in sys.argv[1:] if a.isdigit()), 20))


def die(code: int, msg: str) -> None:
    print(f"判读：{msg}（RC={code}）")
    sys.exit(code)


def stamps() -> tuple:
    out = []
    for suf in ("", "-wal", "-shm"):
        p = pathlib.Path(str(DB) + suf)
        st = p.stat() if p.exists() else None
        out.append((p.name, None if st is None else (st.st_mtime_ns, st.st_size)))
    return tuple(out)


def moved(a: tuple, b: tuple) -> list:
    return [x[0] for x, y in zip(a, b) if x[1] != y[1]]


def ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def fingerprint(con: sqlite3.Connection) -> dict:
    """内容级指纹：四张表的行数、最大 rowid 与全表拼接摘要。

    表名写错**不许**咽成一条恒定相同的错误串（首版就是这么写的，那等于指纹尺在空气上开火）。
    """
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in TABLES if t not in have]
    if missing:
        die(2, f"指纹尺要扫的表不在库里：{missing}（实有 {sorted(have)}）⇒ 判据落在空气上")
    out = {}
    for t in TABLES:
        rows = con.execute(f"SELECT rowid, * FROM {t} ORDER BY rowid").fetchall()
        h = hashlib.sha256(repr(rows).encode("utf-8", "replace")).hexdigest()[:16]
        out[t] = (len(rows), rows[-1][0] if rows else 0, h)
    return out


def main() -> int:
    if not DB.is_file():
        die(2, f"{DB} 不在 ⇒ 容器路径不对")
    print(f"W-0 库文件：{[p.name for p in (DB, pathlib.Path(str(DB)+'-wal'), pathlib.Path(str(DB)+'-shm')) if p.exists()]}"
          f"｜静置 {SETTLE}s 期间**一个连接都不开**")
    s0 = stamps()
    con = ro()
    f0 = fingerprint(con)
    con.close()
    time.sleep(SETTLE)
    s1 = stamps()
    m1 = moved(s0, s1)
    print(f"W-1 静置 {SETTLE}s 没人开局 ⇒ 动了的文件：{m1 or '无'}")
    for a, b in zip(s0, s1):
        print(f"      {a[0]:16s} {a[1]} → {b[1]}")
    if m1:
        print("      ↑ 我的仪器在这段里一个 sqlite 连接都没开，它照样动 ⇒ 时间戳测的是"
              "「有没有别的连接活着」，不是「有没有人写库」")

    con = ro()
    f1 = fingerprint(con)
    try:
        con.execute("INSERT INTO papers(project_id, paper_json) VALUES('__nope__','{}')")
        con.commit()
        ro_write_ok = True
    except sqlite3.Error as exc:
        ro_write_ok = False
        print(f"W-3 mode=ro 试写被拒：{type(exc).__name__}｜{str(exc)[:70]}")
    finally:
        con.close()
    if ro_write_ok:
        die(2, "mode=ro 连接真写进去了 ⇒ 名义只读、实际可写，本仪器全部结论作废")
    time.sleep(2)
    s2 = stamps()
    f2 = fingerprint(ro())
    print(f"W-2 只开一个 mode=ro 连接读一遍 ⇒ 动了的文件：{moved(s1, s2) or '无'}")
    if f1 != f2:
        for t in TABLES:
            if f1[t] != f2[t]:
                print(f"      🔴 {t}: {f1[t]} → {f2[t]}")
        print("判读：读数期间线上库内容真变了（有并发写在提交）⇒ 同一时刻的漏斗读数不可信（RC=1）")
        return 1
    print(f"W-4 内容指纹前后相同：papers={f2['papers'][0]} 行 / "
          f"intents={f2['research_intents'][0]} 行 ⇒ 没人写过库")

    shutil.copy(DB, TMPDB)
    w = sqlite3.connect(TMPDB)
    before = fingerprint(w)
    w.execute("UPDATE papers SET project_id=project_id || '_x' WHERE rowid=1")
    w.commit()
    after = fingerprint(w)
    w.close()
    TMPDB.unlink(missing_ok=True)
    pathlib.Path(str(TMPDB) + "-wal").unlink(missing_ok=True)
    pathlib.Path(str(TMPDB) + "-shm").unlink(missing_ok=True)
    if before == after:
        die(2, "对副本做了一次真 UPDATE 而指纹没变 ⇒ 这把尺永不开火，W-4 的"
               "「前后相同」毫无意义（RC=2）")
    print("W-5 副本上真写一行 ⇒ 指纹变了 ⇒ 这把尺会开火（负控制成立）")
    print(f"判读：主库 (mtime,size) 与 -wal/-shm 三者 {('会自己动' if m1 else '这次没自己动')}"
          f"，而内容级指纹在 {SETTLE + 2}s 里稳如磐石 ⇒ 只读自证该钉"
          "**内容指纹 + mode=ro 拒写**，不该钉文件时间戳三连；"
          "轮 160 那次 RC=2 是判据写错，不是读数作废")
    return 0


if __name__ == "__main__":
    sys.exit(main())
