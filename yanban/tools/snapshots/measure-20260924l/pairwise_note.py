"""三台配对读数脚本共用的一句体检：转储行数对不对得上文件名里写的 n。

为什么单独一个模块：`analyze_blind_pairs.py` / `lc_winrate.py` / `probe_citation_effect.py`
读同一批 PAIRS_JSON 转储，「部分样本」这件事要么三台都喊、要么三台都不该喊——
各抄一份就会漂（轮 122 已经量到「换决策量会让旧夹具失去鉴别力」是同一类失效）。
"""
from __future__ import annotations

import pathlib
import re


def partial_note(path: pathlib.Path, rows: int) -> str:
    """文件名里带 `n36` 才算声明了样本量；对不上就给出该印的那一行，否则空串。

    返回空串**不等于**「这份是完整的」——`g36_blind_before.txt` 这类不带 n 的命名走不到
    这里，那是「没声明」而不是「声明了且对上了」。调用方要按这两种态分开措辞。
    """
    m = re.search(r"n(\d+)", path.name)
    if not m:
        return ""
    n = int(m.group(1))
    if n == rows:
        return ""
    return (f"⚠️ 部分样本：{path.name} 转储 {rows} 行 ≠ 文件名声明 n={n}"
            f"（差 {n - rows} 对）⇒ 本读数只作个案，不得当作那份预登记的完整结果")
