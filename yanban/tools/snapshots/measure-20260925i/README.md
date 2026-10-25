轮 146 的**权威成因**量具：零模型调用，把云端 `g141` 记录下来的模型原始 topic 按产品
`llm_topic` 的同一条链（长度/标点 → `_topic_is_shell` → `_TOPIC_FILLER` → 逐字校验）逐道重放，
并报出第一道开火的闸门；三例都与产品实判一致才给 RC=0。
读数见 `docs/evidence/g142_topic_guard_replay_local.txt`。

钉字节的理由：这台仪器是「英语 topic 结构性进不了检索」这句话的唯一证据；
它只调产品自己的 `_topic_is_shell` / `_TOPIC_FILLER`，一旦有人改这两道闸门，
这份读数就该跟着变——而它必须能复现，否则那句结论没有出处。

🔴 快照里的副本不能在原位复跑（它按自身路径深度 sys.path），复跑用 `输出/scripts/` 那份。


| 文件 | sha256 |
| --- | --- |
| `explain_topic_reject_local.py` | `b74a61499ffdf5cd7a5cf6439bb9d616cf4639be62895e3fa93742a7cf64c012` |
