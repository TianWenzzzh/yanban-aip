# 研伴系统 量化基准结果（诚实四指标，2026-09-17 实测）

1. 调研大模型多智能体协作的研究现状，给我摘要与方向建议… → 状态=completed 分解=1 子任务=[('literature', 'Completed', 4293)] 端到端=234.4s 报告=1292字
2. 检索 knowledge graph embedding 文献 3篇，并整理… → 状态=completed 分解=1 子任务=[('literature', 'Completed', 34898)] 端到端=345.7s 报告=1517字
3. 对比 RAG 与微调在医疗问答的准确率，有 1000 条标注数据，请设计实验… → 状态=completed 分解=2 子任务=[('experiment', 'Completed', 36863), ('analysis', 'AwaitingInput', 12)] 端到端=318.7s 报告=1611字
4. 分析这批实验结果的相关性：```csv
accuracy,latency
0… → 状态=completed 分解=1 子任务=[('analysis', 'Completed', 9460)] 端到端=234.4s 报告=1362字
5. 写一段相关工作：要点1 MCP统一工具接入；要点2 A2A智能体任务委派；要… → 状态=completed 分解=1 子任务=[('writing', 'Completed', 24105)] 端到端=294.6s 报告=1489字
6. 评审这段结论：多智能体系统通过标准化协议实现协作，在两个数据集上实验显示准确… → 状态=completed 分解=1 子任务=[('review', 'Completed', 28149)] 端到端=258.5s 报告=1215字
7. 调研 MCP 与 A2A 协议互操作的研究现状，给我摘要和方向建议… → 状态=completed 分解=1 子任务=[('writing', 'Completed', 15450)] 端到端=258.5s 报告=1537字
8. 检索 federated learning 医疗影像 文献 2篇… → 状态=completed 分解=1 子任务=[('literature', 'Completed', 2354)] 端到端=96.2s 报告=930字
9. 设计一个验证多智能体协作提升科研效率的实验，并评审这段写作：协作系统让效率提… → 状态=completed 分解=3 子任务=[('experiment', 'Completed', 32331), ('writing', 'Completed', 16045), ('review', 'Completed', 32489)] 端到端=342.7s 报告=1586字
10. 分析 ```csv
loss,epoch
2.1,1
1.6,2
1.2,3… → 状态=completed 分解=1 子任务=[('analysis', 'Completed', 11058)] 端到端=252.5s 报告=1480字

## 诚实四指标（W3：替代旧单一"任务成功率"口径）

| 指标 | 定义 | 数值 |
|---|---|---|
| Task Completion Rate | 全部子任务 completed 的任务 / 总任务 | 9/10 = 90% |
| Partial Completion Rate | ≥1 个子任务 completed 的任务 / 总任务 | 10/10 = 100% |
| RPC Success Rate | completed 子任务 RPC / 全部 RPC | 27/30 = 90% |
| Capability Coverage | completed 子任务 / 计划子任务 | 12/13 = 92% |

跨智能体 RPC 总次数 | 30 |

- analysis 调用 3 次：平均 6843ms / 中位 9460ms
- experiment 调用 2 次：平均 34597ms / 中位 34597ms
- literature 调用 3 次：平均 13848ms / 中位 4293ms
- review 调用 2 次：平均 30319ms / 中位 30319ms
- writing 调用 3 次：平均 18533ms / 中位 16045ms