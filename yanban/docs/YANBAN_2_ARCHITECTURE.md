# 研伴 2.0 架构（Companion 认知层）

```text
用户 ⇄ webui /companion 路由（Companion.tsx：对话 + 论文卡 + PDF Drawer）
        ↓ /companion/*
leader.py 挂载 companion/api/router.py
        ↓
companion/conversation/dialogue_manager.handle_message()
  ├─ intent/extractor.py   规则通道提取增量（确定性，可测试）
  ├─ intent/model.py       ResearchIntent 合并 + confidence 重算
  ├─ intent/clarification.py  缺口×权重 → 1-2 个高增益反问（就绪则静默）
  ├─ 状态机 DISCOVER→CLARIFY→FORMULATE→SEARCH→SCREEN→READ→REFINE→DELIVER
  └─ literature/engine.search_for_intent()  多 Provider 并行（safe_search 降级）
        → dedup（DOI+标题）→ 评分（年份偏好+引用对数缓增+OA+主题匹配）→ 推荐理由
        ↓ 需要全文时
companion/paper/（resolver→downloader→parser(PyMuPDF)→qa）
        ↓
（多智能体协作场景）既有 orchestrator.py → 5 Partner + WanBridge —— 原样保留
```

分层原则：认知层叠加在既有系统上；梧桐链路（AIC/端点/证书/叮当）冻结；classic 工作台五页保留（VITE_DEFAULT_UI 切换）。
