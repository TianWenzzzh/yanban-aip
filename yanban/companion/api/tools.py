"""对外命名工具表（借鉴 undermind 的 U12：整个产品对外是「一组有名字的工具」）。

这里只写**名字与一句话**；方法与路径由调用方把 `router.routes` 递进来现取，不在这里抄第二份。
所以端点改名、删除或新增而这份表没跟上时，`build()` 当场报错——不会留下一个"看起来还在"的假工具。

🔴 对账必须拿 `companion.api.router.router.routes`，不要拿 `leader.app.routes`：leader 导入
companion 那段是 try/except（`_HAS_COMPANION`），认知层没挂上时 app 里一条 /companion 都没有，
拿它当全集会让这张表"对得上"而其实对面是空的。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Iterable

# (对外工具名, 端点函数名, 一句话) —— 第二列是函数名，不是路径：路径抄在这里就是第二个真相
SPEC: list[tuple[str, str, str]] = [
    ("create_project", "create_project", "开一个研究项目"),
    ("inspect_project", "get_project", "看一个项目的现状与缺口"),
    ("ask", "chat", "问一句，拿一条不带流式的回答"),
    ("ask_stream", "chat_stream", "问一句，逐字流式回答"),
    ("list_turn_steps", "chat_steps", "这条消息发送前会做哪几步、哪一步可以拒绝"),
    ("list_commitments", "get_commitments", "列出这个项目里欠着的承诺与待办"),
    ("set_commitment_status", "set_commitment_status", "改一条承诺的完成状态"),
    ("note_commitment_assumption", "note_commitment_assumption", "给一条承诺记下「我按什么假设算的」"),
    ("search_papers", "run_search", "按检索意图查一轮文献"),
    ("star_papers", "paper_feedback", "给一篇论文记收藏或排除"),
    ("list_papers", "list_papers", "列项目内已存的论文"),
    ("export_papers", "paper_export", "把当前筛选结果导成 CSV"),
    ("read_report_brief", "report_brief_read", "读写报告前那两条问题的当前状态"),
    ("answer_report_brief", "report_brief_write", "回答或跳过写报告前那两条问题"),
    ("write_report", "make_report", "生成一份分节综述报告"),
    ("write_report_stream", "report_stream", "逐字流式生成综述报告"),
    ("rewrite_report_section", "rewrite_report_section", "重写报告的指定一节"),
    ("compare_papers", "literature_matrix", "跨论文做对比表、按列抽取"),
    ("read_paper_sections", "paper_sections", "读一篇论文的章节结构"),
    ("read_pdf_span", "cite_context", "给一句话，取回撑住它的 PDF 原文片段（含出处与页/节）"),
    ("read_search_plan", "intent_plan", "读这一轮打算怎么查"),
    ("edit_search_plan", "intent_plan_edit", "改检索意图的槽位"),
    ("block_memory", "block_memory", "把一条记忆屏蔽掉"),
    ("unblock_memory", "unblock_memory", "恢复一条被屏蔽的记忆"),
    ("remember", "add_memory_note", "记一条项目内的备忘"),
    ("list_project_memories", "list_memories", "列项目内所有记忆"),
    ("forget_memory_note", "delete_memory_note", "硬删一条记忆"),
    ("read_user_memory", "memory_snapshot", "读用户画像与各项目的记忆"),
    ("inspect_user_profile", "get_user_profile", "读用户画像（含出处）"),
    ("forget_user_profile", "forget_user_profile", "清掉用户画像"),
    ("fetch_paper_pdf", "fetch_pdf", "按开放链接把 PDF 取回本地"),
    ("read_paper_pdf", "serve_pdf", "取回一篇论文的 PDF 文件"),
    ("parse_paper_pdf", "parse_paper_pdf", "把一篇 PDF 解析成页与章节"),
    ("render_flowchart", "flowchart", "画一张方法流程图"),
    ("render_mindmap", "mindmap", "画一张研究脉络导图"),
    ("read_research_map", "research_map", "读项目的研究地图"),
    ("search_keywords", "keyword_search", "按关键词找候选术语"),
    ("translate_text", "translate", "把术语译成中文"),
    ("list_knowledge", "kb_list", "列知识库节点"),
    ("read_knowledge_note", "kb_note", "读一条知识笔记"),
    ("write_knowledge_note", "kb_upsert", "写或更新一条知识笔记"),
    ("search_knowledge", "kb_search", "在知识库内检索"),
    ("grow_knowledge", "kb_grow", "让知识库顺着一条笔记往外长"),
    ("export_knowledge", "kb_export", "导出整库为 Markdown"),
    ("read_artifact", "artifact_html", "取一份已生成的产物页面"),
    ("route_workflow", "route_workflow", "把一句话分派给合适的伙伴"),
    ("ask_about_paper", "paper_qa", "就着一篇论文问答"),
    ("check_report_grounding", "report_grounding", "查报告每节的接地情况"),
    ("subscribe_updates", "subscribe", "订阅一个新论文方向"),
    ("list_subscribed_updates", "subscriptions_list", "列订阅与它们检出的更新"),
    ("check_subscribed_update", "subscription_check", "对一条订阅立刻查一次更新"),
    ("create_share_link", "share_create", "生成一个只读分享链接"),
    ("read_share_link", "share_read", "读一个分享出去的项目"),
    ("revoke_share_link", "share_revoke", "撤销一个分享链接"),
    ("list_tools", "list_tools", "取这张工具表本身"),
]

# 只给自家界面用、不对外 advertised 的工具：这两个端点的鉴权是半截的（整套 API 还没有身份层，
# 多用户上线前需要一处身份设计）——先把它们点名，但不让外部调用方从这份表里看见。
INTERNAL: set[str] = {"inspect_user_profile", "forget_user_profile"}


def _by_endpoint(routes: Iterable[Any]) -> dict[str, list[Any]]:
    idx: dict[str, list[Any]] = {}
    for r in routes:
        if not hasattr(r, "methods"):
            continue
        idx.setdefault(r.endpoint.__name__, []).append(r)
    return idx


def build(routes: Iterable[Any]) -> list[dict[str, Any]]:
    """把这张表与真实路由对账，返回逐条工具；对不齐就抛错，不返回一份"差不多"的清单。"""
    idx = _by_endpoint(routes)
    names = [n for n, _, _ in SPEC]
    dup_names = sorted({n for n in names if names.count(n) > 1})
    if dup_names:
        raise LookupError(f"工具名重复：{dup_names}")

    out, claimed = [], set()
    for name, endpoint, summary in SPEC:
        if endpoint in claimed:
            raise LookupError(f"端点 {endpoint} 被两个工具名点名——同一件事不许有两份说法")
        hits = idx.get(endpoint, [])
        if len(hits) != 1:
            raise LookupError(f"工具 {name} 点名的端点 {endpoint} 在路由表里出现 {len(hits)} 次（要求恰好 1）")
        route = hits[0]
        claimed.add(endpoint)
        out.append({
            "name": name,
            "summary": summary,
            "method": "/".join(sorted(route.methods - {"HEAD"})),
            "path": route.path,
            "internal": name in INTERNAL,
        })

    orphans = sorted(set(idx) - claimed)
    if orphans:
        raise LookupError(f"这些端点没有被任何工具名点名（对外词表漏了一个）：{orphans}")
    return out


def external(routes: Iterable[Any]) -> list[dict[str, Any]]:
    return [t for t in build(routes) if not t["internal"]]


def dispatchable(routes: Iterable[Any]) -> dict[str, Callable[..., Any]]:
    """把「工具名 → 端点函数」这一小段派发关系也放在这里，别在调用侧抄第二份。

    收进来的规则是量出来的（轮 197：对外可宣告的 GET 工具 24 颗，其中返回值标注是 dict 的 20 颗）：
    GET、非 internal、且端点函数直接返回 JSON 面。返回文件流/纯文本的那几颗（export_papers、
    export_knowledge、read_artifact、read_paper_pdf）不走这条入口——它们本来就是给浏览器下载用的。
    写类工具（POST/DELETE）也不走：整套 API 还没有身份层，在没有鉴权的服务上开一条能被外部改数据的
    口子不属于本轮。
    """
    idx = _by_endpoint(routes)
    allowed = {t["name"] for t in build(routes) if t["method"] == "GET" and not t["internal"]}
    out: dict[str, Callable[..., Any]] = {}
    for name, endpoint, _summary in SPEC:
        if name not in allowed:
            continue
        hits = idx.get(endpoint, [])
        if len(hits) != 1:
            raise LookupError(f"工具 {name} 点名的端点 {endpoint} 出现 {len(hits)} 次（要求恰好 1）")
        fn = hits[0].endpoint
        # router.py 带 `from __future__ import annotations`，返回值标注在这里是字符串 "dict" 而不是 dict
        # （首跑就是因为拿 `is dict` 比，24 颗全被判成不合格、派发表空到没人能调）
        if inspect.signature(fn).return_annotation not in (dict, "dict"):
            continue
        out[name] = fn
    return out
