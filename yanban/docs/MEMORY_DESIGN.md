# Memory 设计

存储：`data/yanban.db`（SQLite WAL，gitignore）。表：users / projects / research_intents / conversations / messages / papers / events。

四类记忆：
1. **User Profile**（users.profile_json）：研究经验、编程/ML/DL/LLM 水平、语言与阅读偏好。
2. **Research Project**（projects + research_intents）：多项目隔离，intent 以 JSON 全量存储，confidence 由字段权重表计算。
3. **Paper Memory**（papers）：status（recommended/to_read/read/favorite/rejected）+ user_feedback + relevance/difficulty（预留）。
4. **Episodic**（events）：决策事件（"放弃 URL 路线，转向视觉+LLM"），intent_updated / paper_feedback 自动留痕。

检索：`memory/retriever.py` TF-IDF Top-K（零依赖）；embedding 可插拔升级（接口 `retrieve(project_id, query, k)`）。
约束：禁止全量聊天塞 LLM；用户反馈必经 papers_feedback 影响下一轮排序；项目间不串扰（验收 3/10/11）。
