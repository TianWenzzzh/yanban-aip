快照 measure-20260927f · 轮 162 供给根因四台（字节从容器 /tmp 取回，不是工作副本）

这一轮的顺序是「先量根因，再动产品码」，四台各司其职：
  1. `probe_supply_failure_causes.py` —— 把服务端**自己说的话**取回来（状态码 + Retry-After + 响应体原话）。
     它同时否掉了 `providers/base.py:43` 旧注释里那句「Retry-After 是日额度级的」：实测 15~37 **秒**。
  2. `probe_arxiv_who_answers.py` —— arXiv 的 406 到底是谁在答：完整响应头显示 `via: 1.1 varnish`
     + `x-served-by: cache-lga-*` （CDN 边缘），且**不带任何参数的裸 URL 也 406**、换 UA/Accept/http
     方案都一样 ⇒ 换请求形状修不动，属环境项。另一件它顺手做的事：把 OpenAlex 的三条形状**轮换出场顺序**
     重跑，用来区分"形状效应"与"时刻效应"（那次九条全 200 ⇒ 本轮没有形状可归因，如实记为分不出来）。
  3. `probe_retry_after_window.py` —— 单变量实验：等够服务端要的秒数再打一次，成不成？
     答案分源：OpenAlex 兑现（等 17s → 200），Semantic Scholar 不兑现（无该头、盲等 22s 四次仍 429）。
     ⇒ 这条直接决定了产品码怎么改：有头就照秒数冷却，没头保留 120s 起步翻倍的阶梯。
  4. `probe_cooldown_policy_paired.py` —— 改完的前后数：**同一份服务端应答，两种策略各自重放**
     （采集阶段每轮必打、不查任何锁，避免"新策略 produced 的轨迹去对比旧策略"那种自我确认）。

读数与判据出处：`docs/evidence/g174_supply_root_causes_and_retry_after.txt`；
判线条文在各自文件头，本 README 不抄第二份。
用例与植入：`tests2/test_provider_retry_after.py`（8 条）+ `输出/_scratch/mutcheck_n8.py`（5 条植入，
其中 3 条各带一条「不该红的必须照旧绿」）。

| 文件 | sha256 |
| --- | --- |
| `probe_arxiv_who_answers.py` | `265fef4398ed28c73d49d801c47b9404090c9b4603459d351efb32d43341089e` |
| `probe_cooldown_policy_paired.py` | `df28cb8d15cc6634c2abeb746e0f01220b67a480ffb5f30a556c556aa1fabb23` |
| `probe_retry_after_window.py` | `a8388d5a2a9a8db2ccebadbc55456baad2490a9ddcda4b1d2748bfb5458ee61b` |
| `probe_supply_failure_causes.py` | `20fdfa239cbc4d4c926260c07d3a69fb4853bceb5ab5f1f9ff11385e7cd5e041` |
