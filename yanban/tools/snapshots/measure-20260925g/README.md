轮 146 的量具之一：线上真路径下「主题抽取这条兜底通道」活着没有，以及外部英语提问能不能拿到 topic。
容器内跑，零落库（C4 用 mtime+size 自证），模型调用硬上限 4 次（C1b 用产品自己的 `llm.REAL_CALLS` 计数对账）。

读数见 `docs/evidence/g140_topic_channel_online.txt`：C1 环境 `LLM_MOCK=False`、`GLM_API_KEY 非空=True`、
`glm-4-flash`；C2 正对照中文 → topic=「钓鱼网站检测」（通道活着）；C3 三条英语提问 0/3 出 topic；
包装层**一条异常都没捕获到** ⇒ 不是请求层坏了，是模型有响应、被产品自己的校验判成空 → 判读 **V3**（RC=0）。
下一台仪器（`probe_topic_reject_reason.py`）就是去量「被判掉的那道闸门是哪一道」。

| 文件 | sha256 |
| --- | --- |
| `probe_topic_channel.py` | `81fde81fa6974acb9230ba9927e47f53adc440c48e0151a54ed1dce557972302` |
