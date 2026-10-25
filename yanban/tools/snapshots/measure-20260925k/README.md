轮 147 的部署复量：线上那份 extractor.py 的 sha 与本机提交份一致，且直接调线上函数看判定（零模型调用、零落库，DB mtime+size 前后逐位相同）。

六条全过：拉丁术语不再判壳（GNN/CRISPR）、水词表 11 个词逐条仍判壳、「题目是 CRISPR 脱靶检测的深度学习模型」在线上也抽出 topic=CRISPR、无主题句仍不硬查。

前置两道：sync_cloud RC=0（探活 200、项目数 754→754）、check_cloud_drift RC=0（45 个 .py 逐文件一致）。

| 文件 | sha256 |
| --- | --- |
| `probe_deployed_shell_guard.py` | `f5daaed1d2ac9abd98c7c963295c878abe2df55979ed380ee545394a3f33a26c` |
