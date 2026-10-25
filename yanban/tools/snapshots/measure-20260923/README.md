# 测量仪器快照（2026-09-23，轮 112）——**只读，不要在这里改代码**

本目录的三份文件是**字节冻结的快照**，作用是：换机器之后仍能复现
`docs/evidence/g88…`、`g89…`、`g87…` 这些读数。工作副本在迁移包的
`输出/scripts/`，改仪器只改那边；下一轮若要刷新快照，新建一个日期目录，不要就地编辑本目录。

```
sha256  文件
fa54317f7044757928f41178a4aa4a3a1ad6a1a55c96faa9b5c53bf3ae05ded1  verify_title_claims.py
693902fb1b617d2792cffec1d0d655c1358de10846afe45032712c3491c85cf6  probe_critique_depth.py
9d126821fa0f9f66c0e4596f902201bb9d08b59499f53b582ef551a386ba1d57  probe_counterev_position.py
```

三份各自的用途与**已知的不适用条件**：

- `verify_title_claims.py` — 不经 LLM 评委的净贡献量具（`g89`）。本地跑，只要网络，零模型调用。
  `--selfcheck` 不过就拒绝出数。
- `probe_critique_depth.py` — 报告侧相反证据率 / 分节接地率 / 反证闸门落地率（`g75`—`g78`、`g88`）。
  要在云端容器里跑（`输出/scripts/run_cloud_probe.py` 送进去），它读的是闸门自己上报的
  `sections_counterev` 与 `counterev_note`。
- `probe_counterev_position.py` — **轮 111 之后已不适用于产品链路**：它复刻的是"整节重写"那条旧路，
  遇到新 build 会打印"读数无效"并退出 2。保留是为了让 `g87` 那份历史读数仍可复现。

另有一份 `title_verify_cache.json`（Crossref 命中缓存）留在迁移包 `输出/scripts/` 里，
它让 `verify_title_claims.py` 复跑时不必再打网络；删掉只是变慢，不影响判定。
