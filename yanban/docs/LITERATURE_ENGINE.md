# 文献引擎

Provider 体系（`companion/literature/providers/`）：
- CrossrefProvider / ArxivProvider：适配既有 sources.py（中文期刊+被引数 / arXiv API+限流退避）
- OpenAlexProvider：无需 Key，含 OA 标记与引用数
- SemanticScholarProvider：无 Key 低频可用；429 自动降级
- Unpaywall：DOI → 合法 OA PDF 定位（server 端 `resolve_pdf_for_doi`）

管线：`safe_search 并行（失败静默降级，用户无感知）→ normalize → dedup（DOI 精确 + 标题归一化模糊）→ 评分 → 排序 → Top-N`。

评分（§13 反"引用越高越好"）：base 0.4；年份 ≥ intent.year_from +0.25 / 早于 −0.3；引用 ≥50 +0.2、≥10 +0.1；主题匹配 +0.15；OA +0.1。
推荐理由逐篇具体生成（年份/引用/主题匹配/OA），禁止"高度相关"式废话。

多轮收敛：用户反馈写入 Paper Memory → ResearchIntent.current_focus/rejected_directions 更新 → 下一轮查询与过滤随 intent 变化（E2E 断言覆盖）。
