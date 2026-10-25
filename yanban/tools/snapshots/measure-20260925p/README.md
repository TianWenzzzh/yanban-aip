轮 152 补记的线上复量：把仓内那份句集（`tests2/fixtures_topic_corpus.py`，sha 在 F 行对账）喂进容器里部署后的 `capture_topic`，量「修的行为真上线了吗」（零模型调用，由 Z 行雷计数盯着；DB 由 R 行 mtime+size 自证未动）。
读数见 `docs/evidence/g149_deployed_topic_capture_online.txt`：E/F 两道认路都 ✅（线上 extractor.py sha=fe6e48e2… 与本机提交份一致、句集与仓内份一致），覆盖 14/14（分母派生自夹具 `len(POSITIVE)-len(LLM_ONLY)`）、负例误捕 0/24、带壳 0、天花板 2 条仍空、轮 147 的三条旧期望原样在，RC=0；植入 `--plant-trip` 那场红在 Z、RC=2。

钉字节的理由：这条读数是「轮 152 的修复确实在线上生效」的唯一证据；E/F 任一 sha 闸门、Z 雷、R 只读自证一改，这场就复现不出来。

🔴 快照副本不能在原位复跑（容器路径写死 /app、/tmp），复跑用 `输出/scripts/` 那份。

| 文件 | sha256 |
| --- | --- |
| `probe_deployed_topic_capture.py` | `8611e6fa4cbb259f455094d027e88541737d9ff12846f481f11965e526692c92` |
