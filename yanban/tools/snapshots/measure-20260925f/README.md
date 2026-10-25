轮 145 的 #25 前置量具：外部提问池里有多少条**首问就能进检索轮**（本机跑，零模型调用、零网络，这一条由 W0/W0b 两条判据盯着）。
读数见 `docs/evidence/g139_wants_results_local.txt`：206 条去重提问里 **0 条** 首问直接进检索、**0 条** 被规则通道抽出非空 topic；进入 `llm_topic` 兜底通道 206 次（mock 分支，未联网）；两条控制都开着（正对照 = g98 里轮 139 真拿到卡的中文情景，直接进检索 True；负对照 = 英语求助句，被反问 True）⇒ 按预登记判读 RC=3。

钉字节的理由：这条读数撑起 #25 的「第四次盲跑没有理由」。判据 W0/W0b/W1/W1c/W2 与那句 `clarifies()` 复刻（`compose_interview_reply(...) is not None and not wants_results(t)`，走 `extract_update` 规则通道）任一改动，0/206 就复现不出来。

🔴 v1→v2 的更正记在这里：v1 的文件头断言「零模型调用、零网络」但**没有任何机制盯着它**，实际每条提问都走进了 `llm_topic` 的网络分支（本机没配 key，请求失败、异常被 `except` 吞掉、topic 留空）。v1 的数没错但口径写错了。v2 加 W0（断言 `llm.LLM_MOCK` 为真）+ W0b（进入兜底通道的次数必须等于规则通道漏掉的条数）+ `PROBE_PLANT_UNWIRE=1` 植入负控制（摘掉计数桩必须红在 W0b，实测 RC=2）。

本机仪器没有容器份可比；🔴 快照里的副本**不能在原位复跑**（`ROOT` 由自身路径深度算），复跑用 `输出/scripts/` 那份。

| 文件 | sha256 |
| --- | --- |
| `probe_wants_results.py` | `362b58a471e369e50f83e64bce2c1473ee1dc54702546626ebe23c8128b382b4` |
