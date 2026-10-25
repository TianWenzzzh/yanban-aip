本场（g168）真跑过的仪器字节，从容器 yanban-leader-1 的 /tmp/probe_gap_subquery_ab_v2.py 取回冻结。

与 measure-20260926g 的差异只有两处，都不碰判据：
① 新增 S0 起跑前置（从 probe_provider_supply 现读 LIVE_MIN/PACKET_CAP，测量走 supply_gate.supply_now 同一把尺）；
② --selfcheck 增加 C8a/b/c/d 四条，测这条前置会不会拒绝开跑、以及阈值有没有第二份赋值。
六个判据常量 MIN_USABLE_PAIRS / PLANNED / BOOT_ITERS / SEED / PREC_FLOOR_PP / EXPLORE_MAX 逐字未变。

同一份字节做了两件事并都留了读数：起跑前 --selfcheck 18/18 通过（RC=0），随后跑出 g168 那份 15 对全可用的 A/B 读数。

| 文件 | sha256 |
| --- | --- |
| `probe_gap_subquery_ab_v2.py` | `e23a8d70f492c058499ac149430cc706e4dd81dfd38b42523ea3b72f937120e9` |
