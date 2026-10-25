# 算法升级报告（v6）——调研驱动的四项迭代

> 调研日期：2026-09-12 ｜ 方法：WebSearch 学术/工业双线 → 可落地项实现 → 真实 GLM 验证
> 原则：每个升级必须对应一个已测量的缺陷（v1/v2 实验诚实账本），不做纸面升级

## 一、调研发现 → 升级映射

| 已测量缺陷（v1/v2 实验） | 调研找到的算法依据 | 升级 |
|---|---|---|
| 评分受长度/单 prompt 偏差扰动（v1 同报告不同轮次分数跳变） | Microsoft **LLM-Rubric**（ACL 2024，多维 rubric+逐维独立+锚定标尺）；**Evaluating Scoring Bias**（arXiv:2506.22316，评分提示自身偏差） | ① 多维 rubric 评审器 |
| 修订振荡（v1 5 题中 3 题 revision 后变差） | **Self-Refine**（Madaan et al. 2023）生成→自评→修订循环；工程化补丁：分数轨迹+平台期停机 | ② Self-Refine 定向修订 |
| 工具检索关键词/向量各有盲区 | **RRF**（Cormack et al. 2009；MongoDB/Qdrant/Google 工业标准实践）——只用 rank 免分数归一化 | ③ RRF 混合检索 |
| 单策略单候选（质量上限=单次生成） | **Best-of-N reranking**（test-time compute 换质量，工业标准做法） | ④ GD Best-of-2 选优 |

## 二、实现明细

### ① judge.py 多维 rubric 评审器
5 维度（方法严谨性 0.30/内容完整性 0.25/引用真实性 0.20/结构规范 0.15/语言密度 0.10），
每维独立锚定标尺（0/4/7/10 分行为学定义），显式声明"以信息密度论质量，不以长度论质量"。
一次调用输出全维度 JSON+幻觉清单（比逐维度 N 次调用省 token）。输出 `JudgeReport`，
`weakest_dimensions()` 供修订定向。测试：解析容错/裁剪/排序 4 例。

### ② refine.py Self-Refine 定向修订
循环：judge → 低分维度（<7.0）→ 定向修订指令 → 重写。三重停机准则：
达标即停（target 8.5）/ 平台期停机（连续 2 轮提升 <0.3）/ 轮数封顶。
失败语义：评审挂掉原样返回+标注（不产生半修订污染）。测试：三停机逻辑离线钉。

### ③ 万桥 RRF 混合检索
`/tools/search?mode=hybrid`：关键词（词法）与向量（语义）各取 top-2k，
RRF(d)=Σ1/(60+rank) 融合。实验冒烟：两路结果并集正确加权（b 双路靠前→冠军）。

### ④ GD Best-of-2
GA（flash 重写）与 GC（plus 重写）并发生成两候选 → 多维 rubric 打分选优。
冒烟：GD 10.0（picked=GC）。--judge rubric 全实验切换评分器。

## 三、验证数据（冒烟级；全量 rubric 实验进行中）

- rubric 评分区分度显著优于单分数：同批报告 GC 8.8 / GA 7.65 / GB 7.0 / G2 4.45（单分数法曾给 GB=GC=reject 不可分）
- GD 冒烟 10.0（Best-of-2 选中 GC 候选）
- 真实 GLM embedding-3：cos(文献,查询) 0.56 > cos(网页,查询) 0.52（分离度小但方向正确；目录扩充后重估）

## 四、来源

- [LLM-Rubric（ACL 2024, Microsoft）](https://aclanthology.org/2024.acl-long.745/) ｜ [GitHub](https://github.com/microsoft/LLM-Rubric)
- [Evaluating Scoring Bias in LLM-as-a-Judge（arXiv:2506.22316）](https://arxiv.org/html/2506.22316v2)
- [Position Bias in LLM-as-a-Judge（IJCNLP 2025）](https://aclanthology.org/2025.ijcnlp-long.18.pdf)
- [Self-Refine（Madaan et al. 2023）](https://arxiv.org/abs/2303.17651) ｜ [Reflexion](https://arxiv.org/abs/2303.11366)
- [RRF 原始论文（Cormack et al. 2009）与工业实践](https://www.mongodb.com/resources/basics/reciprocal-rank-fusion) ｜ [Qdrant hybrid search](https://qdrant.tech/course/essentials/day-3/hybrid-search-demo/) ｜ [Google Cloud hybrid search](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/vector-search/about-hybrid-search)
- [Trust or Escalate: LLM Judges with Provable Guarantees（arXiv:2407.18370）](https://arxiv.org/html/2407.18370v1)（后续工作：评委升级机制）
