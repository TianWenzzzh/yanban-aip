轮 144 的**零卡归因**（容器内跑，零模型调用，SQLite 以 `mode=ro` 打开）：
不猜原因，读产品自己在 `run_search` 里无条件落库的诊断（`search_skipped` /
`search_engine_returned` / `search_stored` / `search_error`），按 `projects.created_at` 的小时
分桶——分桶是这台仪器的关键设计：沙箱库 `/tmp/probe_sandbox_plan_ab/yanban.db` 被多轮长跑复用，
不分时间就会把上一轮的读数混进这一轮。

- 三条互斥判读（D1 反问轮不检索 / D2 检索跑了拿回 0 篇 / D3 根本没走到 run_search）跑前写死。
- 结果 D1：本轮两个时段共 140 条 intent（=A/B 的 140 条回复）**全部** `skipped=clarifying`，
  且抽取后的 topic 为空串；更早时段的行里能看到 `returned=8` ⇒ 检索路径本身是通的。
- 结论边界：这解释的是"为什么 P1'' 没有分母"，**不是**"规划层有没有用"；也**不是**产品缺陷判定——
  「反问轮不检索」是轮 40 用 n=36 盲评立下的设计。它成立的前提是"每条提问都是新项目的首问"，
  这在真实用户分布上是否成立**没测**（线上没有真实用户）。

| 文件 | sha256 |
| --- | --- |
| `probe_card_drop.py` | `b83fd1f1acccb64c69c35e039229fea1789f8030cdbbad0f5a23e23f8e4fed3b` |
