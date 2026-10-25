本轮（轮 157 / N2）的读数由这一对仪器产出，两份都是从容器 /tmp 取回的「真跑过那份字节」：

- supply_gate.py：第一次冻。它上次已经跑过（g168 的起跑前置那次，判读 RC=5），
  但当时按 RUNTIME_STATE 里记的规矩没冻（不钉从没产出下一场读数的代码）；本轮就是那场。
- probe_provider_supply.py：与 measure-20260926j 里那份字节已不同（j 冻的是当时的旧工作副本），
  本代取回的是刚刚在容器里产出 RC=0 读数的那份。

读数落点：docs/evidence/g169_n2_retry_paired_before_after.txt
配套判据：tests2/test_provider_retry_budget.py（429 每源一次 / 5xx 仍重试 / 分档与覆盖面不变）

| 文件 | sha256 |
| --- | --- |
| `probe_provider_supply.py` | `b7b701bad302fafdba8148cc42fd6888825c204a06286384580076d9bf16f3ce` |
| `supply_gate.py` | `d19ac1fbba86da21c66297ddf60d196ad14e5c5c96990c04212c9acd9b59150b` |
