轮 152 追加的端到端量具：两条申报给 `llm_topic` 的天花板句，走**产品自己的** `extract_update`（内含 `capture_topic(text) or llm_topic(text)`）→ `apply_update` → `ready_for_search`，报 topic 来源/confidence/放行/缺槽位。这场**真发模型调用**（与轮 149–152 其余零调用仪器相反），所以计数桩是「必须 ≥1」而非「必须为 0」，并配 `--plant-mock` 植入证明它会红。
读数见 `docs/evidence/g151_ceiling_e2e_online.txt`：兜底通道 2/2 给出逐字 topic（'三维重建' conf 0.346、'图拉普拉斯算子' conf 0.385），但 ready_for_search 0/2（现读阈值 0.52）⇒ 「判给兜底」只解决了 topic 这一维，堵点仍在其余 7 个空槽位。正对照 g98 那句 conf 0.615 放行 True、负对照 conf 0.192 放行 False ⇒ 尺子两向都分得开。

钉字节的理由：E/F 两道 sha、Z0 合取哨兵、M1/M2 桩、P/N 两对照任一改动，这组数就复现不出来。

🔴 快照副本不能在原位复跑（容器路径写死 /app、/tmp），复跑用 `输出/scripts/` 那份。

| 文件 | sha256 |
| --- | --- |
| `probe_deployed_ceiling_e2e.py` | `2187969800baa608238ac2456da867e3cff422b81f4c7e476fd685c8d08cdcb4` |
