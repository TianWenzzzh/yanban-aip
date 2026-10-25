# 测量仪器快照 · measure-20260923c（轮 113 第二次冻结）

产出 `docs/evidence/g94b_counterev_supply_seven_pools.txt` 的那一份字节。
与 `measure-20260923b` 的唯一差别：**命令行可追加主题**（`g94` 三个池复跑逐字节相同，
只证明确定性、不证明跨池稳健，于是加了追加参数去问「这道门槛会不会咬」）。
本目录是归档不是工作副本；工作副本在迁移包 `输出/scripts/`，更新快照请再新建日期目录。
钉字节的测试：`tests2/test_measure_snapshot.py`。

- `probe_counterev_supply.py`　sha256 = e0fc4a1f8529704abca72633af7bb0108abc09d93c13d6418a3a429741f72336
