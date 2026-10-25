轮 150 的线上库只读盘点：`messages.role='user'` 逐字比对三份池子，量「库里有没有不是我们造的用户原话」。

读数：1223 条 user 消息，外部英语池命中 0、自家池命中 1223、未知来源 0 ⇒ #26 不能从产品日志取料。
自证：mode=ro、零模型调用（仪器自己设 LLM_MOCK=1）、DB mtime+size 跑前跑后逐位相同；
植入 `--plant-trip` 主动撞 httpx 雷 ⇒ 触发 1、红在 Z 行、RC=2。

| 文件 | sha256 |
| --- | --- |
| `probe_online_user_msgs.py` | `5dccf7a7c513c29e59e7d6f542f7659317bea3372a3d6d0b20358dd26a08c2d5` |
