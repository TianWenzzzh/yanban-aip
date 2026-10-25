"""把**探针进程**的四个数据落点全指到沙箱 ⇒ 探针不再往生产库与知识库里造垃圾。

为什么不需要改产品代码、也不需要部署：`companion/db.py:94 get_conn()` 每次看模块级 `DB_PATH`，
而测试早就靠「重置 `_CONN` + 改 `DB_PATH`」做隔离（`db.py:97` 的注释写了这条路）。
同一个钩子给云端探针用就行——探针跑在**自己的进程**里，不碰 uvicorn 那份连接。

为什么要一次改四个落点：少指一个就是"半个沙箱"。轮 120 只读盘点（`g101`）实测线上
754 个项目全部由探针造出来，就是过去 20 轮"只指了数据库、没指知识库"这类半隔离攒下的。
四个全局：`db.DB_PATH` / `knowledge.KB_ROOT` / `matrix.MATRIX_DIR` / `downloader.PDF_DIR`。
"""
from __future__ import annotations

import pathlib
import sqlite3

PROD_DB = pathlib.Path("/app/data/yanban.db")
COUNTS = ("projects", "messages", "conversations", "papers", "events")


def isolate(root: str | pathlib.Path) -> dict[str, dict[str, str]]:
    """切到沙箱并返回改动前后的路径表（打印用，让"隔离到了哪"是可核对的而不是口头承诺）。"""
    import sqlite3 as _sq

    import companion.db as db
    import companion.knowledge as kn
    import companion.literature.matrix as mx
    import companion.paper.downloader as dl

    p = pathlib.Path(root)
    p.mkdir(parents=True, exist_ok=True)
    conn = getattr(db, "_CONN", None)
    if conn is not None:
        try:
            conn.close()
        except _sq.Error as e:
            print(f"⚠️ 旧连接没关掉（DB_PATH 已换，新读会走沙箱）：{type(e).__name__}: {e}")
        db._CONN = None
    before = {"DB_PATH": str(db.DB_PATH), "KB_ROOT": str(kn.KB_ROOT),
              "MATRIX_DIR": str(mx.MATRIX_DIR), "PDF_DIR": str(dl.PDF_DIR)}
    db.DATA_DIR = p
    db.DB_PATH = p / "yanban.db"
    kn.KB_ROOT = p / "knowledge"
    mx.MATRIX_DIR = p / "matrix"
    dl.PDF_DIR = p / "papers" / "pdf"
    after = {"DB_PATH": str(db.DB_PATH), "KB_ROOT": str(kn.KB_ROOT),
             "MATRIX_DIR": str(mx.MATRIX_DIR), "PDF_DIR": str(dl.PDF_DIR)}
    return {"before": before, "after": after}


def counts(db_path: pathlib.Path) -> dict[str, int]:
    """**只读**取各表行数。取不到就抛——把"读不到"当成 0 会让轮 120 那种盘点假绿。"""
    if not pathlib.Path(db_path).is_file():
        raise FileNotFoundError(f"库不存在：{db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        out: dict[str, int] = {}
        for t in COUNTS:
            try:
                out[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error:
                out[t] = -1        # 表不存在：留 -1 让人看见，不静默当 0
        return out
    finally:
        con.close()
