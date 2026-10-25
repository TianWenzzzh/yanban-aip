"""自生长知识库（Obsidian 式）：markdown vault + [[双链]] + 反向链接。

- 位置：data/knowledge/{project_id}/*.md（gitignore）
- 自动生长：对话结论 / 论文元信息 / 用户决策 → 笔记追加或更新
- 双链：[[笔记名]] 语法；backlinks() 反向索引
- 检索：search() 跨笔记全文
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent.parent / "data" / "knowledge"


def _vault(project_id: str) -> Path:
    p = KB_ROOT / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(title: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", (title or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    return name[:80] or "untitled"


def upsert_note(project_id: str, title: str, body: str, tags: list[str] | None = None) -> str:
    """创建/更新笔记（标题即文件名）。返回笔记名。"""
    name = _slug(title)
    front = "---\n"
    front += f"title: {title}\n"
    if tags:
        front += f"tags: [{', '.join(tags)}]\n"
    front += f"updated: {__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n---\n\n"
    _vault(project_id).joinpath(f"{name}.md").write_text(front + body, encoding="utf-8")
    return name


def append_note(project_id: str, title: str, extra: str) -> None:
    name = _slug(title)
    f = _vault(project_id) / f"{name}.md"
    if f.exists():
        f.write_text(f.read_text(encoding="utf-8") + "\n" + extra, encoding="utf-8")
    else:
        upsert_note(project_id, title, extra)


def get_note(project_id: str, title: str) -> str | None:
    f = _vault(project_id) / f"{_slug(title)}.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


def strip_front(text: str) -> tuple[str, list[str]]:
    """去掉 YAML front-matter，返回（正文，tags）。列表预览与渲染都不该把 --- 暴露给用户。"""
    m = re.match(r"^---\n(.*?)\n---\n+", text, re.S)
    if not m:
        return text, []
    tags: list[str] = []
    for line in m.group(1).splitlines():
        if line.startswith("tags:"):
            tags = [t.strip() for t in line.split(":", 1)[1].strip(" []").split(",") if t.strip()]
    return text[m.end():], tags


def list_notes(project_id: str) -> list[dict]:
    out = []
    for f in sorted(_vault(project_id).glob("*.md")):
        text = f.read_text(encoding="utf-8")
        body, tags = strip_front(text)
        links = re.findall(r"\[\[([^\]]+)\]\]", body)
        out.append({
            "note": f.stem,
            "bytes": f.stat().st_size,
            "links": links,
            "tags": tags,
            "head": re.sub(r"^#+\s*", "", re.sub(r"\s+", " ", body)).strip()[:120],
        })
    return out


def note_body(project_id: str, name: str) -> str | None:
    """按 list_notes 给出的笔记名取正文（去掉 front-matter）。

    不走 get_note(title)：那个会再 slug 一次，而这里的 name 已经是文件名本体。
    """
    f = _vault(project_id) / f"{name}.md"
    if not f.exists():
        return None
    return strip_front(f.read_text(encoding="utf-8"))[0]


def backlinks(project_id: str, title: str) -> list[str]:
    """引用了 [[title]] 的其他笔记。"""
    needle = f"[[{title}]]"
    out = []
    for f in _vault(project_id).glob("*.md"):
        if needle in f.read_text(encoding="utf-8") and f.stem != _slug(title):
            out.append(f.stem)
    return out


def search(project_id: str, query: str, limit: int = 20) -> list[dict]:
    q = query.lower()
    hits = []
    for f in _vault(project_id).glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if q in text.lower():
            pos = text.lower().find(q)
            hits.append({"note": f.stem, "context": text[max(0, pos - 40):pos + 80].replace("\n", " ")})
        if len(hits) >= limit:
            break
    return hits


def export_zip(project_id: str) -> bytes:
    """把该项目的 vault 打成 zip（Obsidian / 任意 markdown 编辑器可直接打开）。"""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(_vault(project_id).glob("*.md")):
            zf.write(f, arcname=f"yanban-kb/{f.name}")
    return buf.getvalue()


def auto_grow_from_paper(project_id: str, paper: dict) -> str:
    """论文入库时自动生成论文笔记（自生长）。"""
    title = paper.get("title") or "未命名论文"
    body = (
        f"# {title}\n\n"
        f"- 作者：{', '.join(paper.get('authors') or []) or '佚名'}\n"
        f"- 年份：{paper.get('year', '未知')}｜来源：{paper.get('venue', '未知')}\n"
        f"- DOI：{paper.get('doi') or '—'}\n"
        f"- 状态：{paper.get('status', 'recommended')}\n\n"
        f"## 摘要\n{paper.get('abstract') or '（未获取到摘要）'}\n\n"
        f"## 推荐理由\n" + "\n".join(f"- {r}" for r in (paper.get("recommendation_reason") or [])) + "\n"
    )
    return upsert_note(project_id, f"论文：{title}", body, tags=["paper"])


def auto_grow_from_decision(project_id: str, summary: str, detail: str = "") -> str:
    """用户决策/研究事件 → 决策笔记（Episodic 的 markdown 化）。"""
    note_title = f"决策：{summary[:50]}"
    append_note(project_id, note_title, f"\n- {datetime_now()}：{summary}\n{detail}\n")
    return note_title


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")
