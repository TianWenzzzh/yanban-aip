# Changelog（2.0 认知层，feature/companion-2.0 分支）

- Phase 0：ARCHITECTURE_CURRENT / YANBAN2_PLAN 落盘；feature 分支建立；llm.py 新增 LLM_MOCK 开关（MOCK 下 chat/chat_json 确定性返回，全测试零真实调用）
- Phase A：companion 包骨架；ResearchIntent 模型（Pydantic+SQLite）；规则提取器（阶段/目标/水平/年份/OA/主题）；clarification_score 反问引擎；八态状态机；dialogue_manager；/companion API 挂载
- Phase B：四类 Memory（users/projects/papers/events）+ TF-IDF MemoryRetriever + 项目隔离与会话恢复
- Phase C：PaperProvider 体系 + OpenAlex/SemanticScholar/Unpaywall 新增 + 去重管线 + 年份加权评分重写 + 逐篇推荐理由
- Phase D：PyMuPDF 解析 + OA PDF 下载缓存 + PDF Drawer（原生查看器）+ 论文上下文问答
- Phase E：研究地图 v1 + 反馈写 Paper Memory
- Phase F：E2E test_beginner_research_flow（小白反问→intent→检索→反馈→断言）
- 修复：extractor 主题正则双花括号笔误；LEVEL 提示逐字段匹配；retriever 返回契约统一；stunnel 证书链不完整（生产问题，叶+中间+根）
