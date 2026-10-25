# measure-20260924i（轮 117→118 · 判 g98 的那一代仪器）

- `probe_gate_yield_v2.py`　sha256 = 5be1011ecc80bfda935f78bff7cb822aa777844ca564b6d8942d1d984bd04281（24281 字节）
- `parse_gate_yield_lines.py`　sha256 = 3a07bd776eea8781cc1c90b238cf07c3014ebf235779bfbdf5b904bd5eb687d2（17086 字节）
- `test_parse_tier_offline.py`　sha256 = fc0b686ba1be1e97601f6f8cf9e7d56d07f192ba5d92065ec8a4d4501c183ee5（5471 字节）
- `test_judge_g98_offline.py`　sha256 = bbf839b0e0bb7292e7188dad43d291c756464acaabf0a3e996934194b4d19562（10423 字节）

探针 `probe_gate_yield_v2.py` 与 `measure-20260923h` 里那份**逐字节相同**（冻结脚本当场断言过一次，
不同就拒绝冻结）—— 本轮只动判分器与离线测试，没动印读数的代码。

## 这一代修的是"结论由谁判"

h 钉的是印读数的探针；但 g98 的**结论**（留下 / 收窄 / 回退 / 先修 / 判不了）由
`parse_gate_yield_lines.py --judge` 产出，它一改口径就得重新冻结，否则事后答不出"当时是哪一版判的"。

1. `--judge` 逐条判预登记的 Q0—Q6，只看探针收尾**真会打印**的那几行。
2. 🆕 **三态对账**：`ok / mismatch / not-comparable`。旧版把"两档压根没比过"（轮 117 之前的行没有
   「前置」列）与"比对通过"共用一个返回 0 ⇒ 判分器会对着 g97 印「Q5 对账 ✅」。
3. 🆕 **两把尺口径核对**：闸门用 `mentor.py:802` 的 `_CITE_TRACE`（认 `【n】`），
   计数尺 `grounding_report` 只数"≥8 字句子里的 `[n]` 或《》"。
   于是"带角标句 0 条"**不等于**"闸门在这档没有可治对象"；判分器先数出
   「卡>0、计数尺读到 0、闸门却没开火」的条数，非 0 就**否决 Q3 的收窄结论**。
4. `test_judge_g98_offline.py` 13 条分支用例（作废 / 未测 / 收窄 / 回退 / 先修 / 口径不一致各一条，
   并且**两条走 `main()` 的 CLI 级反例** —— `recon` 那个状态是 `main()` 算的，只测纯函数等于跳过要验的那一步）。
5. `test_parse_tier_offline.py` 4 条（还原 + 对账，含"汇总少算一条必须作废"）。

⚠️ 顺带记一笔事实修正：h 的 README 里写「`--selfcheck` 7 项断言」是当时的数，
本轮加到 **11 项**（J 键名形状 / K 结构不变量），M 变异也加到 **9 条**。h 已冻结，不改它，改在这里说明。

## 冻结后自证（本脚本真的跑过，不是照抄命令）

    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe measure-20260924i/test_judge_g98_offline.py
    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe measure-20260924i/test_parse_tier_offline.py
    PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe measure-20260924i/probe_gate_yield_v2.py --selfcheck

## 本轮新出现的两台工具（只记哈希，不进快照）

- `probe_ps_check.py`　sha256 = 6ee3cf008d4602dc72c4ce354c7384f97879e6e9dbeb73284bf1d288a54c5a69（不进快照目录：它按路径找别的文件）
- `pre_run_checks.py`　sha256 = 16a54f45715bb78311a0cb150370bee20b003cb218f5f4850dbcc90ff31326ba（不进快照目录：它按路径找别的文件）
- `wait_probe_done.py`　sha256 = 4cf3f919788f78c29c45464eff4745462f43349550e688b8e8df7632cf3aecc9（不进快照目录：它按路径找别的文件）
- `mutate_tier_selfcheck.py`　sha256 = 668835bb4cf5656b43c7a0fd7fe593e8e5dce5261bb4663a0eaeb2934799042d（不进快照目录：它按路径找别的文件）
- `mirror_team_repo.py`　sha256 = d31923966e71aeee53835522a9361c30635a8b60bc5f3258bc8e984b2ea64bec（不进快照目录：它按路径找别的文件）

`wait_probe_done.py` 的由来：判 g98 只能等它跑完（部分样本不许手算），而我此前每隔几分钟
手工问一次进度，每次问吃掉一整轮上下文 —— 等待本身不该占判断的预算。
