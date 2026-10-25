# measure-20260923h（轮 117 · 起 g98 之前钉的新一代）

- `probe_gate_yield_v2.py`　sha256 = 5be1011ecc80bfda935f78bff7cb822aa777844ca564b6d8942d1d984bd04281（24281 字节）
- `parse_gate_yield_lines.py`　sha256 = d2606c18674a1b7006081f23aa252e52afe442b4b8f1c06825bd0c6deeffdd7b（6774 字节）

## 这一台修的是"代价算不出来"

轮 116 的 N2/N3 判据落空，原因是仪器压根不印那两个字段。这一代补上：

1. 逐样本行加「（前置 N）」与「｜接地 g/c」两列 —— 前置 = 重采样/闸门之前的长度，
   没有它就量不出"闸门把回复改短了多少"。
2. 代价汇总拆两档：**实质回复（前置 ≥200 字）** 与 **过场句（前置 <200 字）**。
   g98 靶子池实测 31 条里 11 条是"我这就去查"式过场句，本来无话可引；
   混在一档里，开火率与长度代价会互相掩盖。
3. `--cases <池子.json>`：情景可换成从线上会话库捞的真提问（找不到文件 RC=2，**不退回内置情景**）。
4. `--selfcheck`：`main()` 在 `LLM_MOCK=1` 下直接退出 ⇒ 那段汇总打印平时永远跑不到，
   所以用手算样例喂 `tier_report()`，7 项断言。
5. `parse_gate_yield_lines.py` 换命名字段，并把两档还原值与探针自印的「代价·」汇总**对账**，
   对不上就 ❌ 返回非 0。

## 离线守卫（改完必须两条都绿才许起长跑）

    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_gate_yield_v2.py --selfcheck
    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/mutate_tier_selfcheck.py
    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/test_parse_tier_offline.py

- `mutate_tier_selfcheck.py`　sha256 = 668835bb4cf5656b43c7a0fd7fe593e8e5dce5261bb4663a0eaeb2934799042d（不进快照目录，见文件头说明）
- `test_parse_tier_offline.py`　sha256 = 89e57337ec07520e788590ef0bbf4fc56b46fd9b568e5803e1c0766acea3c1db（不进快照目录，见文件头说明）

`mutate_tier_selfcheck.py` 存在的理由：第一版自检四条变异里漏了一条（把两档切分线 200 改成 0 仍全绿），
因为样例全在 ≥200 字那一侧。**自检自己也会假绿**，所以"改坏必须红"也要固化成一条命令。

## 跑法（⚠️ 参数是每个情景的轮数）

    PYTHONUTF8=1 uv run --no-project --with paramiko python 输出/scripts/run_cloud_probe.py \
        输出/scripts/probe_gate_yield_v2.py code/yanban/docs/evidence/g98_cite_pool.json \
        --cases /tmp/g98_cite_pool.json 4

`run_cloud_probe.py` 按**文件名**把存在的本地文件投到容器 /tmp 并从 argv 剔掉，所以 `--cases`
后面必须写**容器内路径** `/tmp/g98_cite_pool.json`（写本地路径会被吃掉，容器里只剩光杆
`--cases` ⇒ 探针判 RC=2，不会静默改用内置 5 情景）。上限行自报 19 情景 × 4 = 76 条回复。
**起完必须先读「用例池」与「探针上限」两行再走开。**

预登记判据 Q0—Q6 写在 `docs/ITERATION_LOG.md` 轮 117 一节（README 不钉哈希，所以判据不放这儿）。
