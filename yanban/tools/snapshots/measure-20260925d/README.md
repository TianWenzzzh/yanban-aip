轮 144 的**供给体检**（容器内跑，零模型调用、只读）：外部提问池前 70 条逐题走产品自己的
`search_for_intent(intent, limit=8)`（与 `dialogue_manager.py:217` 同一个入口、同一个 limit）。

- 这一代与上一代的差别是**判据换了源**：`MIN_USABLE / 两臂带卡率 / POOL_MIN / load_cases /
  pool_shortfall` 全部用 `ast` 从 `probe_plan_ab.py` 的源码现读并**直接调用**，起跑算术挪到检索之前；
  上一版自己钉 `N_CASES=24`、阈值靠启动方传参，与长跑双向失配（本机零检索已算出来：
  旧的 24 对口径送进长跑闸门 ⇒ 长跑本就 RC=3 拒跑）。
- 读数（判读行原样引用，见 `docs/evidence/g134_precheck_repoint.txt` 的口径说明与容器输出）：
  控制查询首尾各 8 篇且没掉、单臂带卡 69/70、provider 卡数由 crossref 438 + openalex 45 撑起
  ⇒ 体检判「供给上界够」。**注意这条读数的口径是单臂**，它不是"可用对"。
- 它的价值恰在与 A/B 的矛盾上：同一份池子、同一天、同一个容器，体检 69/70 有卡，
  而 A/B 的 140 条回复零条带卡 ⇒ 归因交给 `probe_card_drop.py`（快照 `measure-20260925e`）。

| 文件 | sha256 |
| --- | --- |
| `probe_supply_precheck.py` | `dfac15fe08670f176ba154bf7017cc4053d9e13cbf033dfa1b28c09a080569d4` |
