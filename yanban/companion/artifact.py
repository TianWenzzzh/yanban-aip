"""自包含 HTML 产物（对标 WorkBuddy 差距②「交付可点击的产物」，不是交付数据包）。

现有的三种出口都是**数据包**：知识库 zip、RIS/BibTeX、分享用的 JSON 快照——它们要求接收方
再有一个工具才看得到内容。这里补的是「双击就能读、引用能点回出处」的单文件产物：

- **先转义再套标记**：所有文本（论文标题、摘要、笔记正文，全部来自外部 provider 与模型）
  先 `html.escape`，再把 `**粗体**` / `[n]` / `[[双链]]` 这些安全的行内记号换成标签。
  转义先生效 ⇒ 外部内容里的 `<script>` 只可能是文本，不可能是标签。
- **零 JS、零外部资源**：不写 `<script>`、不引外链 CSS/字体/图片，断网也能完整打开。
- **URL 只认 http/https**：`javascript:` / `data:` 之类的值一律降级成纯文本，不进 `href`。
- **不许有死锚点**：正文里 `[7]` 而参考文献只有 5 篇时，那个角标降级成文本而不是链到不存在的位置。

渲染是**纯函数**（数据进、字符串出），取数由调用方负责——便于逐条写夹具断言。
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_SAFE_SCHEMES = {"http", "https"}
_CITE = re.compile(r"\[(\d{1,2})\]")


def safe_url(value: object) -> str:
    """外部 provider 给的链接：只放过 http/https，其余一律空串（调用方据此降级为文本）。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    return raw if parsed.scheme in _SAFE_SCHEMES else ""


def inline(escaped: str) -> str:
    """作用在**已转义**的文本上，因此这里只可能生成白名单内的标签。"""
    out = _CITE.sub(lambda m: f'<a class="cite" href="#ref-{m.group(1)}">[{m.group(1)}]</a>', escaped)
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[\[([^\]\n]+)\]\]", r'<span class="wikilink">\1</span>', out)
    return out


def md_to_html(text: str) -> str:
    """笔记里真用到的那几点：#~### 标题、- / 1. 列表、粗体、段落。其余原样转义。"""
    out: list[str] = []
    para: list[str] = []
    list_kind = ""          # "ul" | "ol" | ""
    list_open = False

    def flush_para() -> None:
        if para:
            out.append("<p>" + inline(html.escape(" ".join(para))) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append(f"</{list_kind}>")
            list_open = False

    for line in text.splitlines():
        raw = line.rstrip()
        if not raw.strip():
            flush_para()
            close_list()
            continue
        head = re.match(r"^(#{1,4})\s+(.*)$", raw)
        if head:
            flush_para()
            close_list()
            lvl = min(len(head.group(1)) + 1, 5)   # 笔记里的 # 是 h1，产物里让位给文档标题
            out.append(f"<h{lvl}>{inline(html.escape(head.group(2)))}</h{lvl}>")
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", raw)
        numbered = re.match(r"^\d+[.)]\s+(.*)$", raw)
        if bullet or numbered:
            flush_para()
            kind = "ul" if bullet else "ol"
            if list_kind != kind:
                close_list()
                list_kind = kind
                out.append(f"<{kind}>")
                list_open = True
            out.append(f"<li>{inline(html.escape((bullet or numbered).group(1)))}</li>")
            continue
        close_list()
        para.append(raw.strip())
    flush_para()
    close_list()
    return "\n".join(out)


def _cite_cap() -> int:
    """报告可引用的论文上限，直接取产品那个常量（改它这里跟着变，不钉字面量）。"""
    from companion.conversation.mentor import MAX_PAPER_CANDIDATES
    return MAX_PAPER_CANDIDATES


def _text(value: object) -> str:
    """字段可能存成列表（`engine.py` 把来源写成 `[source]`）——`str()` 会把 Python 的
    list repr 直接印进交付物（线上实测 `｜['crossref']`），这里统一成可读文本。"""
    if isinstance(value, (list, tuple, set)):
        return "·".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def _ordered(notes: list[dict]) -> list[dict]:
    """综述排最前：产物是给人读的文档，不是文件列表。分组依据用产品自己打的 tag。"""
    return sorted(notes, key=lambda n: 0 if "report" in (n.get("tags") or []) else 1)


_TRAIL_MAX = 12   # 台账长度上限：产物是给人读的文档，不是逐句 dump


def _trail(rows: list[dict], citable: list[dict]) -> str:
    """逐句把「这句对上哪篇的哪段原文」摊开，读者不必自己去检索就能核。

    片段是从论文自己的标题/TLDR/摘要里**逐字摘**出来的（`best_spans`），不是模型转述；
    挂错编号那句照实说依据在别篇，不替它把编号圆回去 —— 圆回去就等于替报告撒谎。
    """
    if not rows:
        return ""
    items = []
    for row in rows[:_TRAIL_MAX]:
        sent = inline(html.escape(row["sentence"]))
        span = row.get("span")
        if not span:
            items.append(
                f'<details class="ev"><summary>{sent}</summary>'
                f'<p class="meta">被引各篇的标题/摘要里<strong>找不到</strong>能回连的原文片段 —— '
                f'这句整池查无依据，请优先复核。</p></details>')
            continue
        ref = int(span["paper"]) + 1
        title = _text((citable[ref - 1] if 1 <= ref <= len(citable) else {}).get("title"))
        who = f"《{html.escape(title)}》" if title else f"第 {ref} 篇"
        head = (f"依据：{who}（ref-{ref}）" if row["status"] == "grounded"
                else f"这句挂的编号没接住，依据其实在{who}（ref-{ref}）")
        quote = inline(html.escape(str(span.get("text") or "")))
        meta = _text(span.get("source"))
        items.append(
            f'<details class="ev"><summary>{sent}</summary>'
            f'<p class="meta">{head}｜出自该篇的{("「" + meta + "」") if meta else "原文"}，'
            f'词面重合 <strong>{span.get("overlap")}</strong> 词</p>'
            f'<blockquote>{quote}</blockquote></details>')
    omitted = len(rows) - len(items)
    more = f"（另有 {omitted} 句未列出）" if omitted > 0 else ""
    # 整条台账默认折起来：线上真产物截图实测，12 行摊开会把综述正文推到两三屏之后，
    # 「可核对」反而挡住了要读的东西。折叠不影响可得性，点开就在。
    return (f'<details class="trail"><summary>逐句依据{more}：点开看那句在论文原文里到底对上了什么。</summary>'
            f'<div class="evs">{"".join(items)}</div></details>')


_STEPS_MAX = 12   # 执行轨迹的列出上限，理由同 _TRAIL_MAX：产物是文档不是日志 dump
_WHEN = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")


def _when(value: object) -> str:
    """时间戳缩成「日期 时:分」：整串 ISO 带时区会把一行撑到换行、把日期从中间折断（看图看到的）。

    时区不显示是因为块标题已声明「时间均为 UTC」；认不出的格式**原样印**，不猜、不丢。
    """
    raw = _text(value)
    m = _WHEN.match(raw)
    return f"{m.group(1)} {m.group(2)}" if m else raw


def _steps(events: list[dict]) -> str:
    """把项目自己的事件表按时间列进产物：读者看得见研伴做了哪几步，而不只有结论。

    这些行由各处 `db.write_event` 在产品动作发生时写入，**不是模型转述**，所以步数与措辞可核。
    步数与列出的条目同取一个 `rows`（各算各的会印出「共 13 步」却只列得出 12 步，两边都不红）。
    """
    rows = [{"when": _when(e.get("created_at")), "what": _text(e.get("summary"))}
            for e in events if _text(e.get("summary"))]
    if not rows:
        return ""
    shown = rows[:_STEPS_MAX]
    items = "".join(
        f'<li>{html.escape(r["what"])}'
        + (f'<span class="when">{html.escape(r["when"])}</span>' if r["when"] else "")
        + "</li>" for r in shown)
    omitted = len(rows) - len(shown)
    more = f"（另有 {omitted} 步未列出）" if omitted > 0 else ""
    return (f'<details class="steps"><summary>执行轨迹 <strong>{len(rows)}</strong> 步{more}：'
            f'产品自己在动作发生时记下的事件，不是模型转述。'
            f'时间均为 UTC。</summary><ol class="steps">{items}</ol></details>')


def _cite_counts(text: str, n: int) -> list[int]:
    """参考文献里每一篇被挂了几个 `[n]` 角标。

    用产物自己的 `_CITE` 来数，不另写一条正则：两边各数一遍，迟早一个算进《篇名》一个不算。
    """
    counts = [0] * n
    for m in _CITE.findall(text or ""):
        i = int(m) - 1
        if 0 <= i < n:
            counts[i] += 1
    return counts


def _support_label(n_cite: int, n_support: int) -> str:
    """一条参考文献的 precision 读数：挂了几次、真撑住几句。

    三种形态必须同一种句式（看图才发现原先两长一短，长的那句在 nowrap 下会顶出边界）：
    含义由上面的「可核对性」块统一解释一次，这里只给数字。
    """
    if not n_cite:
        return '<span class="sup">挂 0 次</span>'
    if not n_support:
        return f'<span class="sup zero">挂 {n_cite} 次 · 撑住 0 句</span>'
    return f'<span class="sup">挂 {n_cite} 次 · 撑住 {n_support} 句</span>'


def _checkability(g: dict, cites: list[int], citable: list[dict], n_refs: int) -> str:
    """综述自身的接地读数：分句回连到被引论文自己的文字上，纯词面匹配、零模型调用。

    为什么要放进产物：「看起来有引用」是能骗人的（实测引用痕迹本身就讨好评委），
    所以把接得住几句与整池查无依据几句分开数出来，连同那几句原文一起交给读者自己核。
    摘录走和正文同一套内联渲染（不然 `**粗体**` 会以字面量露出来），再按同一规则掐掉越界角标。

    两个维度分开报（ALCE 2305.14627 的 recall/precision 之分）：句子有没有依据 = 上面那些数；
    **来源该不该被引** = 下面那句「挂了几次 / 撑住几句」。后者只在有 `[n]` 角标可数时才报，
    纯《篇名》式的综述没有这一维的分母 ⇒ 明说未测，不拿 0 篇冒充"全都合规"。
    """
    cited, grounded = g["cited_sentences"], g["grounded"]
    if not cited:
        return ('<p class="check"><strong>可核对性</strong>：这份综述里<strong>没有</strong>带出处标记的句子，'
                '因此「引用接得住多少」<strong>未测</strong>：没有可数的句子，这个比例不存在。</p>')
    pct = round(100 * grounded / cited)
    parts = [f'带出处标记（[n] 角标或《篇名》）的句子 <strong>{cited}</strong> 句，'
             f'其中 <strong>{grounded}/{cited}</strong>'
             f' 能在被引论文自己的文字里找到字面依据（{pct}%）']
    if g["misnumbered"]:
        parts.append(f'{g["misnumbered"]} 句依据其实在别篇（挂错编号）')
    if g["true_gap"]:
        parts.append(f'{g["true_gap"]} 句整池查无依据')
    ever = sum(1 for c in cites if c)
    fake = sum(1 for c, s in zip(cites, g["supports"]) if c and not s)
    over = sum(1 for c, s in zip(cites, g["supports"]) if s > c)
    never = max(0, len(citable) - ever)
    if not ever:
        parts.append('「这篇该不该被引」<strong>未测</strong>：整篇没有 [n] 角标可数（只有《篇名》式提及）')
    else:
        line = (f'<strong>{ever}</strong> 篇被挂过角标，'
                + (f'其中 <strong>{fake}</strong> 篇整篇没撑住任何一句' if fake else '每一篇都至少撑住一句'))
        if never:
            line += f'；另有 {never} 篇列在来源里但一次都没被挂过'
        # 「撑住 M 句」数的是这篇的原文真被用到的句子：挂错编号的那几句会把依据记到别篇头上，
        # 所以 M > N 是可达的（线上 12 个样本里还没出现，这条是防御性解释，不冒充观察结果）。
        if over:
            line += (f'；{over} 篇撑住的句数多于它被挂的角标数'
                     f'（那些句子把依据挂到了别篇，就是上面「挂错编号」的另一面）')
        parts.append(line)
    trail = _trail(g["rows"], citable)
    return _kill_dead_anchors(
        f'<div class="check"><p><strong>可核对性</strong>：{"；".join(parts)}。</p>{trail}</div>', n_refs)


def _kill_dead_anchors(body: str, n_refs: int) -> str:
    """角标编号超出参考文献条数 ⇒ 降级成纯文本，产物里不留死链。"""
    def sub(m: re.Match) -> str:
        num = int(m.group(1))
        if 1 <= num <= n_refs:
            return m.group(0)
        return f'<span class="cite dead">[{m.group(1)}]</span>'
    return re.sub(r'<a class="cite" href="#ref-(\d{1,2})">\[\d{1,2}\]</a>', sub, body)


# 打印面别再给 `.note` 加 page-break-inside:avoid：一份综述整块就是一个 .note，
# 浏览器会把正文全部推到下一页（线上真产物实测 4 页、第 1 页版面只占 33%；去掉后 3 页、95%，
# 读数与图见 docs/evidence/g125_print_readings.txt）。
# 这段说明只能写在 Python 侧：CSS 是随产物发出去的，注释里的"可核对性"和百分比数字
# 会一起进文档，把「没有报告就不许出现这块 / 不许冒出 %」那两条断言带红（本轮真踩到）。
_CSS = """
:root{color-scheme:light dark}
body{margin:0;padding:2rem 1.25rem 4rem;background:#fff;color:#1c1c1e;
 font:16px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:46rem;margin:0 auto}
h1{font-size:1.6rem;line-height:1.35;margin:0 0 .35rem}
h2{font-size:1.25rem;margin:2.2rem 0 .6rem;padding-bottom:.3rem;border-bottom:1px solid #e5e5ea}
h3{font-size:1.05rem;margin:1.6rem 0 .4rem}
h4{font-size:1rem;margin:1.2rem 0 .3rem;color:#3a3a3c}
p,li{overflow-wrap:anywhere}
.meta{color:#8e8e93;font-size:.85rem;margin:0 0 2rem}
.check{margin:0 0 1.6rem;padding:.85rem 1.1rem;background:#f2f7ff;border-left:3px solid #0a6cff;font-size:.92rem}
.check p{margin:.2rem 0}
.trail{margin:.5rem 0 .1rem}
.trail>summary{cursor:pointer;font-size:.9rem;color:#0a6cff;list-style:none}
.trail>summary::-webkit-details-marker{display:none}
.trail>summary::before{content:"▸ ";color:#0a6cff}
.trail[open]>summary::before{content:"▾ "}
.steps{margin:.5rem 0 1.6rem}
.steps>summary{cursor:pointer;font-size:.9rem;color:#0a6cff;list-style:none}
.steps>summary::-webkit-details-marker{display:none}
.steps>summary::before{content:"▸ ";color:#0a6cff}
.steps[open]>summary::before{content:"▾ "}
ol.steps{margin:.35rem 0 0;padding-left:1.4rem}
ol.steps li{margin:.25rem 0;font-size:.9rem}
.when{color:#8e8e93;font-size:.82rem;margin-left:.45rem;white-space:nowrap;font-variant-numeric:tabular-nums}
details.ev{margin:.35rem 0;border:1px solid #dde6f7;border-radius:5px;padding:.3rem .55rem}
details.ev>summary{cursor:pointer;font-size:.88rem;list-style:none}
details.ev>summary::-webkit-details-marker{display:none}
details.ev>summary::before{content:"▸ ";color:#0a6cff}
details.ev[open]>summary::before{content:"▾ "}
details.ev p.meta{margin:.35rem 0 .2rem}
details.ev blockquote{margin:.2rem 0 .3rem;padding-left:.6rem;border-left:2px solid #cddcf7;font-size:.88rem}
.note{border-top:1px solid #f0f0f2;padding-top:1.2rem;margin-top:1.2rem}
.note>h2:first-child,.note>h3:first-child{margin-top:0}
a{color:#0a6cff}
.cite{text-decoration:none;font-variant-numeric:tabular-nums}
.cite.dead{color:#8e8e93}
.wikilink{border-bottom:1px dotted #c7c7cc}
ol.refs{padding-left:1.4rem}
ol.refs li{margin:.55rem 0}
.sup{color:#8e8e93;font-size:.82rem;margin-left:.45rem;white-space:nowrap}
.sup.zero{color:#b3261e;font-weight:600}
.badge{display:inline-block;font-size:.72rem;padding:.05rem .4rem;border:1px solid #d1d1d6;
 border-radius:999px;color:#3a3a3c;margin-right:.3rem;vertical-align:.1rem}
.honesty{margin-top:2.5rem;padding:1rem 1.1rem;background:#f7f7f9;border-left:3px solid #c7c7cc;
 font-size:.85rem;color:#3a3a3c}
@media(prefers-color-scheme:dark){body{background:#1c1c1e;color:#f2f2f7}
 h4{color:#c7c7cc}.meta,.cite.dead{color:#98989f}.honesty{background:#2c2c2e;color:#c7c7cc;border-color:#48484a}
 a{color:#6cb2ff}.badge{border-color:#48484a;color:#c7c7cc}.note{border-color:#2c2c2e}
 .check{background:#20263a;border-color:#6cb2ff}
 .wikilink{border-color:#5a5a5f}}
@media print{body{padding:0}h2{page-break-after:avoid}}
"""


def render_artifact(project_name: str, notes: list[dict], papers: list[dict],
                    generated_at: str = "", events: list[dict] | None = None) -> str:
    """notes: [{"note": 标题, "body": 去 front-matter 的 markdown, "tags": [...]}]；papers: 论文卡列表。

    events: 该项目的事件行（`kind`/`summary`/`created_at`），用来列「执行轨迹」；不传或为空 ⇒ 整块不出现。
    """
    from companion.conversation import report
    ordered = _ordered(notes)
    # 封面用综述自己的标题：项目名往往是用户那句开场白（还常被截断），当文档标题读不通。
    first_report = next((n for n in ordered if "report" in (n.get("tags") or [])), None)
    doc_title = ((first_report or {}).get("note") or "").strip() or project_name or "研伴研究产物"
    title = html.escape(doc_title)
    sub = f"项目：{html.escape(project_name)}｜" if project_name and doc_title != project_name else ""
    # 编号必须与报告同源同序：清单来自 report.citable_papers（先剔无标题），上限来自 mentor。
    # 产物要是把库里第 7、8 篇也编成 ref-7/ref-8，就等于宣称它们被引用过。
    cap = _cite_cap()
    selected = report.citable_papers(papers)
    citable, rest = selected[:cap], selected[cap:]
    blocks: list[str] = []
    for n in ordered:
        body = n.get("body") or ""
        blocks.append(f'<section class="note">\n{md_to_html(body)}\n</section>')
    body_html = "\n".join(blocks) or (
        '<p class="meta">这个项目还没有笔记。先在对话里让研伴生成一份综述报告或对比表，'
        '再来导出这个文件。</p>')

    refs: list[str] = []
    # 可核对性的两个维度共用同一次判定：g 在这里算一次，正文台账与参考文献的「撑住几句」
    # 都从它取——各算一遍的话，一边说「六篇来源」、另一边台账里只有五篇有依据，谁都不会红。
    from companion.literature import evidence
    report_text = "\n".join((n.get("body") or "") for n in notes if "report" in (n.get("tags") or []))
    g = evidence.grounding_report(report_text, citable) if report_text.strip() else None
    cites = _cite_counts(report_text, len(citable)) if g else []
    for i, p in enumerate(citable):
        t = html.escape(str(p.get("title") or "未命名论文"))
        authors = ", ".join(str(a) for a in (p.get("authors") or [])[:6]) or "佚名"
        bits = [f'<strong>{t}</strong>', html.escape(authors)]
        meta = "｜".join(x for x in (_text(p.get("year")), _text(p.get("venue")),
                                     _text(p.get("source"))) if x and x != "None")
        if meta:
            bits.append(html.escape(meta))
        links = []
        for label, key in (("全文", "oa_url"), ("DOI", "doi_url"), ("DOI", "doi")):
            url = safe_url(p.get(key))
            if url:
                links.append(f'<a href="{html.escape(url, quote=True)}">{label}</a>')
                break
        status = str(p.get("status") or "").strip()
        if status and status != "recommended":
            links.append(f'<span class="badge">{html.escape(status)}</span>')
        sup = _support_label(cites[i], g["supports"][i]) if g else ""
        refs.append(f"<li>{' — '.join(bits)}{'（' + ' · '.join(links) + '）' if links else ''}"
                    f"{sup}</li>")
    refs_html = ("<ol class=\"refs\">" + "\n".join(refs) + "</ol>") if refs else \
        '<p class="meta">这个项目还没有入库论文，因此没有参考文献。</p>'
    rest_html = ""
    if rest:
        items = "".join(f"<li>{html.escape(str(p.get('title') or '未命名论文'))}"
                        f" — {html.escape(', '.join(str(a) for a in (p.get('authors') or [])[:3]) or '佚名')}"
                        f"（{html.escape(str(p.get('year') or '年份未知'))}）</li>"
                        for p in rest)
        rest_html = (f'<h2>库里其余论文（{len(rest)} 篇，不参与编号）</h2>\n'
                     f'<p class="meta">报告只引用了下面这 {cap} 篇之内的文献；'
                     f'这些是同一项目里检索到但没有被编号引用的论文。</p>\n'
                     f'<ul class="rest">{items}</ul>\n')
    body_html = _kill_dead_anchors(body_html, len(refs))
    check_html = _checkability(g, cites, citable, len(refs)) if g else ""
    steps_html = _steps(events or [])

    stamp = f"生成于 {html.escape(generated_at)}" if generated_at else "生成时间未记录"
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        f"<title>{title} · 研伴</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>{title}</h1>\n"
        f"<p class=\"meta\">{sub}{stamp}｜笔记 {len(notes)} 篇｜参考文献 {len(refs)} 条"
        + (f"（另有 {len(rest)} 篇不参与编号）" if rest else "") + "</p>\n"
        + (check_html + "\n" if check_html else "")
        + (steps_html + "\n" if steps_html else "")
        + f"{body_html}\n"
        f"<h2 id=\"refs\">参考文献（{len(refs)}）</h2>\n"
        + _with_ids(refs_html) + "\n" + rest_html +
        "<p class=\"honesty\">这是一个<strong>静态快照</strong>：正文里的角标链向本页的参考文献条目，"
        f"而参考文献里的链接指向生成时各文献库给出的出处。可引用的编号只有前 {cap} 篇"
        "（与生成报告时喂给模型的清单同源同序），文件本身不再联网取数，也不会随项目后续变化。"
        + ("顶上那个接地读数由<strong>代码</strong>逐句回连论文原文算出，不出自模型。" if check_html else "")
        + "</p>\n"
        "</main>\n</body>\n</html>\n")


def _with_ids(refs_html: str) -> str:
    """给每条参考文献补 id，顺序与正文角标一致（第 1 条 = ref-1）。"""
    counter = {"n": 0}

    def sub(_m: re.Match) -> str:
        counter["n"] += 1
        return f'<li id="ref-{counter["n"]}">'
    return re.sub(r"<li>", sub, refs_html)
