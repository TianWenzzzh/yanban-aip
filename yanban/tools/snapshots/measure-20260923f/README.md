# measure-20260923f（轮 116 · 事后补钉轮 115 的两台仪器）

- `probe_citation_gap.py`　sha256 = 859c404913bc6989e07a7a822054cf6da177f405de9b30dd1f445e7bf0cf4777
- `probe_gate_yield_v2.py`　sha256 = 0354cb048e92b4e61738b7a9dbe93c6e00d68e6ba6e786a743e007bfcb2e9387

## 为什么这一代是从容器取回来的，不是从工作副本复制的

轮 115 收口之后，工作副本又各自改过：

- `probe_citation_gap.py`（13589 → 14149 字节）加了 `_save()`：夹具改为**逐条原子重写**
  （`.tmp` + `os.replace`），原来只在收尾 dump 一次。改动只影响落盘时机，**不影响逐句分类**。
- `probe_gate_yield_v2.py`（6653 → 8395 字节）加了逐样本 `｜卡 N｜` 字段、第 5 个情景、
  以及汇总里的"带卡分母自证"。

所以「现在工作副本那一份」≠「产出 g96a/g96b 那两个数的那一份」。照惯例复制工作副本进快照目录，
钉住的就是一份从没跑过的代码——`measure-20260923d/README.md` 记的就是同一次事故，这次是**在它已经
被写进文档之后第二次发生**，差别只这回容器 `/tmp` 还留着旧字节、来得及救。

取回时双向核过：容器 `sha256sum` 报的 = 本地落地后自己算的 = 上面钉的这两条
（脚本 `输出/scripts/fetch_frozen_instruments.py`，不一致就删掉落地文件并 RC=2）。
并且 `diff 容器版 工作副本` 确认差异**只有**上面列的那几处，没有夹带别的改动。

## 这两台各产出了哪个数

- `probe_citation_gap.py` → `docs/evidence/g96_citegap_fixture.json`（50 条、其中 30 条带卡）
  与 `g96a_citegap_online.txt`：轮 115 预登记判据 M1 的「有卡零引用 33%」靶子出自这里。
  它与产品 `grounding_report` 逐份对账，对不上就整份作废（RC=2）。
- `probe_gate_yield_v2.py`（这一代）→ `g96b0_gate_yield_aborted_n61.txt`（误参跑批，中途杀）
  与 `g96b_gate_yield_n48.txt`：轮 115 三条不粉饰结论里的两条（p=0.121、`no_citation` 0/48 未开火）
  出自这份读数。**注意 0/48 的分母里没有卡数**——这一代仪器印不出分母，那正是 next 代要修的。

## 换一代的条件

工作副本下次产出新读数时，再新建日期目录钉新一代（见 `measure-20260923g`）。
本目录是归档不是工作副本；改这里任何一个字节都会让
`tests2/test_measure_snapshot.py::test_frozen_instruments_are_byte_identical` 变红。
