"""用本地真实数据渲染自包含产物，落一份 HTML 给人眼看（渲染产物必须亲眼看图）。

第二轮就不再手工拼：挑「有带角标综述」的项目走产品函数渲染，打印判据行 + 写文件。
不接命令行参数（中文路径经 bash 会被 GBK 弄乱），自己扫库。
"""
from __future__ import annotations

import html
import os
import pathlib
import re
import sys

os.environ.setdefault("LLM_MOCK", "1")
ROOT = pathlib.Path(r"E:/AIC智能体迁移包/code/yanban")
sys.path.insert(0, str(ROOT))

import companion.artifact as artifact          # noqa: E402
import companion.db as db                      # noqa: E402
import companion.knowledge as knowledge        # noqa: E402
from companion.memory import paper_memory      # noqa: E402

OUT = pathlib.Path(r"E:/AIC智能体迁移包/输出/g108_artifact_checkability.html")


def pick_project():
    """挑「笔记里有 report 标签且正文带 [n] 角标」的项目——没有角标的话可核对性块只会印未测。"""
    best = None
    for d in sorted(knowledge.KB_ROOT.iterdir()):
        if not d.is_dir():
            continue
        for m in knowledge.list_notes(d.name):
            if "report" in m["tags"] and re.search(r"\[\d{1,2}\]", m["head"] + body_of(d.name, m["note"])):
                n_papers = len(paper_memory.list_papers(d.name))
                if best is None or n_papers > best[1]:
                    best = (d.name, n_papers)
                break
    return best


def body_of(pid, name):
    return knowledge.note_body(pid, name) or ""


def main() -> int:
    picked = pick_project()
    if not picked:
        print("本地库里没有「带角标的综述」可渲染 ⇒ 不出图。要看出这块，先在一个项目里生成综述。")
        return 2
    pid, n_papers = picked
    row = db.get_conn().execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    notes = [{"note": m["note"], "body": body_of(pid, m["note"]), "tags": m["tags"]}
             for m in knowledge.list_notes(pid)]
    out = artifact.render_artifact(row["name"], notes, paper_memory.list_papers(pid), generated_at=db.utcnow())
    OUT.write_text(out, encoding="utf-8")

    blocks = re.findall(r"<p>(.*?)</p>", out)
    check = next((b for b in blocks if "可核对性" in b), "")
    print(f"项目 {pid}｜{row['name']}｜笔记 {len(notes)} 篇｜论文 {n_papers} 篇")
    print(f"写入 {OUT.name}（{len(out)} 字）")
    print("可核对性块：", html.unescape(re.sub(r"<[^>]+>", "", check))[:220] or "（没渲染出来）")
    print("第一个笔记标题：", notes[0]["note"] if notes else "—",
          "｜排在最前的是报告吗：", "report" in (notes[0]["tags"] if notes else []))
    print("角标数：", len(re.findall(r'class="cite" href="#ref-', out)),
          "｜死链：", len(re.findall(r'class="cite dead"', out)),
          "｜<script：", "<script" in out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
