轮 148 的重放量具：g141 记到的模型原始返回，喂进修复前（`git show 9f30425^` 的字节）与修复后两版产品源码，各跑一遍 `llm_topic`（零模型调用，httpx 装雷 + 桩消费对账，两场植入红在 K4）。

读数：`mice killing` 旧版 topic=''／新版 'mice killing'；`研究生挫败感处理` 两版都 ''；新版拿到 topic 后 `ready_for_search` 仍 False（confidence 0.231）⇒ #25 未解开。

| 文件 | sha256 |
| --- | --- |
| `probe_latin_topic_replay.py` | `ab52d3e9604ec1763e43a18a4ded51af0296bd8312336538d7b328703397157c` |
