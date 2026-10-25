# 测量仪器快照 · measure-20260923d（轮 114）

产出 `docs/evidence/g90b_gate_yield_toolong_n48.txt` 的那一份字节。

**这一份不是从工作副本复制的，是从云端容器 `/tmp/probe_gate_yield_v2.py` 取回来的。**
原因：探针跑起来之后，工作副本又被改过两处（边车改成按脚本名各存一份、`main()` 里加了自报样本上限那行），
所以「本地现在这一份」已经不等于「产出这份读数的那一份」——照惯例复制工作副本进来，钉住的就是一份从没跑过的代码。
取回时双向核过：本地 base64 解码后自己算的 sha256 = 容器 `sha256sum` 报的那条 = 下面钉住的这条。

本目录是归档不是工作副本；工作副本在迁移包 `输出/scripts/`，更新快照请再新建日期目录。
钉字节的测试：`tests2/test_measure_snapshot.py`。

- `probe_gate_yield_v2.py`　sha256 = c197a37415e3e866fa38e762aaec3df448b960ff4fae2636093e52c4a1d6d689
