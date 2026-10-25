# 研伴系统 量化基准结果（诚实四指标，2026-09-16 实测）

1. 调研大模型多智能体协作的研究现状，给我摘要与方向建议… → 状态=running 分解=3 子任务=[('literature', 'failed', 30013), ('writing', 'Completed', 26329), ('writing', 'Completed', 26502)] 端到端=360.5s 报告=520字
2. 检索 knowledge graph embedding 文献 3篇，并整理… → 状态=completed 分解=2 子任务=[('literature', 'failed', 30008), ('literature', 'Completed', 4932)] 端到端=300.4s 报告=1857字
3. 对比 RAG 与微调在医疗问答的准确率，有 1000 条标注数据，请设计实验… → 状态=running 分解=1 子任务=[('experiment', 'Completed', 29467)] 端到端=360.5s 报告=926字
4. 分析这批实验结果的相关性：```csv
accuracy,latency
0… → 状态=completed 分解=1 子任务=[('analysis', 'Completed', 5675)] 端到端=204.3s 报告=1234字
5. 写一段相关工作：要点1 MCP统一工具接入；要点2 A2A智能体任务委派；要… → 状态=completed 分解=3 子任务=[('writing', 'Completed', 26223), ('literature', 'Completed', 2924), ('analysis', 'AwaitingInput', 9)] 端到端=273.4s 报告=1357字
6. 评审这段结论：多智能体系统通过标准化协议实现协作，在两个数据集上实验显示准确… → 状态=completed 分解=4 子任务=[('review', 'failed', 30018), ('analysis', 'AwaitingInput', 18), ('experiment', 'failed', 30009), ('writing', 'Completed', 10597)] 端到端=261.4s 报告=1233字
7. 调研 MCP 与 A2A 协议互操作的研究现状，给我摘要和方向建议… → 状态=completed 分解=3 子任务=[('literature', 'failed', 30012), ('writing', 'Completed', 18767), ('writing', 'Completed', 24348)] 端到端=261.4s 报告=1409字
8. 检索 federated learning 医疗影像 文献 2篇… → 状态=completed 分解=1 子任务=[('literature', 'Completed', 15724)] 端到端=225.3s 报告=1440字
9. 设计一个验证多智能体协作提升科研效率的实验，并评审这段写作：协作系统让效率提… → 状态=completed 分解=3 子任务=[('experiment', 'failed', 30012), ('writing', 'Completed', 9988), ('review', 'Completed', 28313)] 端到端=264.4s 报告=1310字
10. 分析 ```csv
loss,epoch
2.1,1
1.6,2
1.2,3… → 状态=completed 分解=1 子任务=[('analysis', 'Completed', 10431)] 端到端=285.5s 报告=1599字

## 诚实四指标（W3：替代旧单一"任务成功率"口径）

| 指标 | 定义 | 数值 |
|---|---|---|
| Task Completion Rate | 全部子任务 completed 的任务 / 总任务 | 3/10 = 30% |
| Partial Completion Rate | ≥1 个子任务 completed 的任务 / 总任务 | 10/10 = 100% |
| RPC Success Rate | completed 子任务 RPC / 全部 RPC | 30/36 = 83% |
| Capability Coverage | completed 子任务 / 计划子任务 | 14/22 = 64% |

跨智能体 RPC 总次数 | 36 |

- analysis 调用 4 次：平均 4033ms / 中位 2846ms
- experiment 调用 3 次：平均 29829ms / 中位 30009ms
- literature 调用 6 次：平均 18936ms / 中位 22866ms
- review 调用 2 次：平均 29166ms / 中位 29166ms
- writing 调用 7 次：平均 20393ms / 中位 24348ms