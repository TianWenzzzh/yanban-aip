# measure-20260924j（轮 118 · 两把尺口径差的量具）

- `probe_trace_vs_count.py`　sha256 = 20627646b59016632714b14a11ca96e18c88d9aaab0677c1d2c1cdb7d50b6518（15665 字节）
- `mutate_trace_selfcheck.py`　sha256 = 6a8c4dea71d76316db46a2979f52c135b836f9ebb945bb812913f95a1a981a1d（3700 字节）

## 它量的是哪件事

轮 117 补记五/六证明：闸门那把尺 `mentor._CITE_TRACE`（认 `[n]`／`【n】`／`《`，整段级）与
计数尺 `evidence.grounding_report`（逐句、句子 ≥8 字、只认 `[n]` 与 `《`）口径不同，
而 `report.py:136-150` 的"这条节没挂引用"判据用的是**计数尺的 0**。
是否要改，取决于线上真实报告里"痕有 && 计数 0"有多少 ⇒ 这台仪器就是来数这个的。
关键前提（`--selfcheck` 里 F/G/H 三项直接验，不靠读代码）：`cited_sentences` 与论文池无关 ⇒
零模型调用、不需要料，纯文本重放。

## 读数（g99，线上 /app/data/knowledge，52 份历史报告 / 258 节 / 带角标句 596）

`T1 主读数：痕有 && 计数 0 ⇒ 0/258 = 0%｜报告级 0/52` ⇒ **线上这条路径没有对象**，
两把尺不用对齐（#23 结为「不改」）。负控制：带角标句合计 596 > 0 ⇒ 计数器确实在数。
证据：`docs/evidence/g99_trace_vs_count_n258.txt`。

## 两台仪器的位置纠正（都是仪器自己的输出逼出来的）

1. 第一版按轮 116 的经验扫 `messages` 表 `kind=deliver`：线上 459 条里 **447 条一个 `## ` 都没有**
   ⇒ RC=2 拒判，没印任何率。报告正文其实由 `knowledge.upsert_note` 写成
   `data/knowledge/{project_id}/*.md`（`companion/knowledge.py:3,29-38`）。
2. 归因分类第一版按"文本里有没有 [n]/《"分，`细节见【1】。` 同时满足两类 ⇒ 自检 J 当场红。
   改成机械判据：把【n】换成 [n] 再数一遍，数得到=形态差（两种改法都咬），数不到=分句门槛
   （只有整节级 `_CITE_TRACE` 咬）。

## 跑法与守卫

    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_trace_vs_count.py --selfcheck
    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/mutate_trace_selfcheck.py
    PYTHONUTF8=1 uv run --no-project --with paramiko python 输出/scripts/run_cloud_probe.py \
        输出/scripts/probe_trace_vs_count.py

预登记判据 T0—T4（含最小分母 20、两侧空档的措辞、三态退出码 0/2/3）写在文件头，
读数在 `docs/ITERATION_LOG.md` 轮 118 一节。
