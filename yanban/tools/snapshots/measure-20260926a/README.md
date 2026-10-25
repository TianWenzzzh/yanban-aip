# measure-20260926a：`harvest_bili_pool.py`（#26 真人料池产线）

产出 `docs/evidence/g154_bili_pool.json`：候选视频 80 条（reply≥20）→ 主评论 240 次 + 楼中楼 140 次
只读 GET ⇒ 逐字取回 488 条、≥12 汉字 356 条入池，每条带 `bvid/aid/rpid/url/comment_ts/like/video_title/collected_at/text_sha256`。

判据 H0–H5 跑前写死（文件头）：H0 BV 必须满长 12 字符；H1 只用 GET 且**代码不读 `member`**（用户名不进上下文也不进文件）；
H2 取回 0 条 ⇒ RC=2 且失败按码统计（本场楼中楼 -352 共 119 次，绝不计为"没有这种话"）；
H3 缺出处不收；H4 入库文本 == 接口 `content.message`（strip 之外零加工）+ 每条留 sha256；H5 分母四档一起报。

🔴 已知偏差（写进 `meta.known_bias`，读数必须带着它用）：未登录 `x/v2/reply` 每页只回按赞前 3 条、
`reply/main` 被 -352，楼中楼是唯一没被分页掐住的通道但连打会被限流
⇒ 这一池系统性偏向「高赞 + 考研/读研经验视频下」，不是全体中文研究者的均匀抽样。
🔴 许可（`meta.license`）：评论版权归评论者，本池只用于本地离线评测，不外发、不转载原话、不展示用户名；对外发布前整池删除。

同一命令连跑两场的复现对照：第一场 493 条逐字 / 352 入池 / 线索 30，第二场 488 / 356 / 线索 32
⇒ 条数会漂（分页窗口按赞序会变），**放行率两场均 12 条**（普查口径），这条稳。

| 文件 | sha256 |
| --- | --- |
| `harvest_bili_pool.py` | `2ec31ab8aec06abe4ac1bbe0c15c4051cd7eeb06f99ef8c00efbc5f9b1a18be0` |
