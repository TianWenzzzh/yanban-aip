# measure-20260926b：`probe_pool_readiness_local.py`（池子 readiness 普查，轮 149 那台）

本轮只加两件事：`--pool/--label`（换输入不换尺子）与 `topic 非空` 一行；另修一处形状
（料池是 `{meta, items}` 时直接 `TypeError`，加了形状分支，取数口径仍是只吃 `user_text`）。

回归证据（同一台仪器、英语池 g132）：首问放行 0/206、`ready_for_search` 0、
一个槽位都没填上 199、正对照放行 True conf 0.615 / 负对照 False 0.192、零网络雷触发 0、RC=3
——与轮 149 落盘的读数逐字相同 ⇒ 改仪器没有换尺子。

中文池 g154 的读数见 `g154` 池子文件与轮 154 日志：12/352 放行、ready 8、topic 非空 70/352=19.9%。

| 文件 | sha256 |
| --- | --- |
| `probe_pool_readiness_local.py` | `380de0a81b960848635c8b3280fa824912a9cc13c4e530e05bb0393f44f388e3` |
