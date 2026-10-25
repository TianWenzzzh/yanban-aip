# measure-20260926c：`shot_companion_states.mjs`（P0/P7 像素验收仪器）

CDP 常驻 headless Edge，逐场景 navigate → 输入 → 发送 → 等这一轮真落地 → 截图，
并同时导出**文本层读数**（卡上文案、进度条/刻度的 rect 比例、缺项条数、横向溢出、产物链接命中测试）。
为什么不用 `msedge --screenshot`：那种一次性进程在别的 Edge 实例在跑时会「RC=0、不出文件」，
而且没法驱动"发一条消息"这个状态迁移。

首跑抓到并修掉的三处仪器缺陷（读数当场作废重跑）：
① 主题串场——`localStorage.clear()` 做在 `about:blank` 上清的是另一个 origin，深浅两张字节完全相同；
② 等待条件写成「卡存在」（卡存在是常态）⇒ 回复没落地就拍了照；
③ 「卡可见」用 `getBoundingClientRect()` 是否 truthy ⇒ 隐藏元素也返回矩形，恒真。
第二次追加两处：④ 找卡时的 tab 回退用 `startsWith("论文")` 会点到抽屉按钮「论文·知识库」
把自己面板打开；⑤ `看得见` 改成 `elementFromPoint` 命中测试（被抽屉盖住的链接不许读成可见）。

判据：出图数 == 场景数、各图字节互不相同、主题与场景一致、窄屏收起态按 `expect` 分档；
不满足就 RC=2 并打印「不合格场景」——**这一行只证明图存在且互不相同，目视仍要人看图**。

| 文件 | sha256 |
| --- | --- |
| `shot_companion_states.mjs` | `b39ffd1a0f0fab5147e51b65e0f7ccfecab188b8861cc4d39ea38efa40c9f591` |
