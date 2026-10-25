快照 measure-20260927d · 轮 160 的两台仪器（字节从容器 /tmp 取回，不是工作副本）

钉住的是**产出 g172 那份读数的那一份字节**：`probe_subquery_funnel.py` 跑过四次，前三次的
判据与这一份不同（Q1 用文件时间戳三连、reach 的分母只问全库、没有 nocap 那一列），
所以"这份字节产出 11/25=44%、RC=5" only；复跑这一份应当逐字得到同一套判定行
（带回篇数会随文献源供给变，见 g172 正文 B）。
`probe_wal_who_touches.py` 是 Q1 换判据的依据：它证明常驻服务自己就会动 `-shm`
（静置 20 秒、一个连接都不开照样动），而内容指纹对真写敏感（副本上 UPDATE 立刻变）。

读数与判线出处：`docs/evidence/g172_subquery_funnel_and_reach.txt`；
判据条文在仪器文件头（Q0—Q5），本 README 不抄第二份。
新自检 R-d/R-e 的植入跑证：`输出/_scratch/mutcheck_n4.py`（3 条变异全部 RC=2 且红在预期条目）。

| 文件 | 用途 |
| --- | --- |
| `probe_subquery_funnel.py` | 真子查询的五级漏斗与 reach（零模型调用、只读库） |
| `probe_wal_who_touches.py` | 只读自证该钉什么：WAL 文件时间戳 vs 内容指纹 |

| 文件 | sha256 |
| --- | --- |
| `probe_subquery_funnel.py` | `0aa1728a78b515dc3838f4ea68d5f9bf657b187ae700eb31a092d632971ec7d6` |
| `probe_wal_who_touches.py` | `73d510a83f941d5853be1b4ba966d2c264817e25155d942e1a38da6fca92c407` |
