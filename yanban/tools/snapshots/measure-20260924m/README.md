# measure-20260924m —— 轮 126：产物端点的线上验收仪器

产出证据：`docs/evidence/g109_artifact_checkability_online.txt`
（12/12 样本走到「可核对性」计数分支、C3 无死锚点、C4 无脚本、C5 上限截断、C7 未测不印百分比）。

- `probe_artifact_online.py`：C6 是**正对照**——只出「未测」的池子不许算这一维过了；
  零开火 ⇒ RC=4 而不是报喜（轮 115 的老账，这台仪器第一次跑就靠它挡下一次恒真判读）。
- `render_artifact_demo.py`：本地侧那台"不出图就退出 2"的渲染器。它本轮给出的是一条**负结果**：
  本地 259 个 vault 目录里 report 笔记 0 篇、带角标 0 篇 ⇒ 「可核对性」在本地没有对象，
  图与读数只能来自线上。冻它是为了下次有人以为"本地能复现"时能重跑同一判据。

| 文件 | sha256 |
| --- | --- |
| `probe_artifact_online.py` | `37e53f20bc6b577a3c1ce5313cd6b86b679e7da9cfe7d91f16c493ac1211aca5` |
| `render_artifact_demo.py` | `d6c2524e9507f2926e07ce80aa8477a08bfec45dfd6c2b666d2bf2a68c15346f` |
