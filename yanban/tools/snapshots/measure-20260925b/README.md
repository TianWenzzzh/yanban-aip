# measure-20260925b — #26 外部提问语料的两件仪器（轮 140）

产出证据：`docs/evidence/g132_question_origin_gate.txt`、
`docs/evidence/g132_ab_gate_passes_on_external_pool.txt`、池子本体
`docs/evidence/g132_external_questions_pool.json`（206 条，sha256
`ea078d19c4b310eb1dada5470820aa8f49150eea5554bf0793297f5f1729425f`）。

## 为什么要有这两个文件
轮 112 的只读盘点把「线上=真实用户」这个前提量没了（754 项目全是自造流量，
`scan_title_origin.py` 判据 7/7 条标题在自家脚本里逐字命中）；轮 138/139 又发现 #25 的可用对
被同一个根卡住：**池子里全是我们自己写的句子**。人这轮给不出外部提问，所以自己去找了一批。

## `fetch_external_questions.py`（碰网络，只读 GET）
Stack Exchange 2.3 `questions` 端点，10 个 (站点, 标签) 各取 25 条高票题，
`user_text` **只放标题原文**（零加工——"不是我写的"这件事不能被我自己的手污染），
每条带 question_id / 永久链接 / 作者 / 发布时间 / score / 许可证（CC BY-SA 要求署名）。
出处不全（已注销账号没有 owner）当场不收并计数，网络瞬断重试 3 次。

## `verify_question_origin.py`（离线判定，零网络零模型）
四关，缺一不放行：
1. 出处齐（链接里必须含同一个 question_id，防"贴了个像样的域名"）；
2. 逐字扫自家代码树 **0 命中**——复用 `scan_title_origin.find_hits`，同一把尺子不另造；
   🔴 溯源池跳过被扫语料自身（语料也躺在 `docs/evidence/*.json`，不跳过就永远拿不到 0 命中）；
3. **正对照**：把 `scan_title_origin.TITLES`（已知全是自家情景）喂同一把尺子，必须 7/7 命中
   ⇒ 没有这一条，"0 命中"完全可能是扫描器根本没在扫；
4. 池子 ≥ 门槛，门槛**从 `probe_plan_ab.py` 现读**（`ast` 解析 `MIN_USABLE`/`USABLE_RATE` 算出 46），
   不在这抄第二份。

## 读数
`① 206/206 合格｜② 扫 360 个文件 0/206 命中｜③ 正对照 7/7｜④ 206 ≥ 门槛 46 ⇒ RC=0 放行`
（首跑时 ① 是 206/213，闸门 RC=3 拒了那 7 条出处不全的，**当场咬到了**。）

## 已知限制
- 🔴 这批是**英语科研社区**提问，不是本产品目标用户（中文本科生）的分布。
  它解掉的是"提问不是我写的"这一条，**不解**"线上=真实用户"——那句限制仍然挂着。
- 本地跑到第一条就 `没走到 repair`（这台机器此刻连不上模型），所以"闸门放行"只证明
  **起跑预算这一关过了**，没证明容器里能出数；真正的 A/B 必须在容器跑。
- 供给预检 `probe_supply_precheck.py` 还盯着 g98 那套题号，换池子后它量的口径与 A/B 仍不对齐
  （轮 138 记过这条）——重跑 #25 之前要么把它指到新池，要么别再拿它的 RC=0 当"分母达标"。
  → 轮 144 已收口（这条留作当时的记录）：阈值与 `load_cases/pool_shortfall` 改为用 `ast` 从
  `probe_plan_ab.py` 现读、起跑算术挪到检索之前，并加 `--budget` 本机零检索查分母。
  读数 `docs/evidence/g134_precheck_repoint.txt`：同一份外部池按旧的 24 对口径送进长跑闸门
  ⇒ 预计 8.3 对 <16（长跑本就 RC=3 拒跑），按 N=70 ⇒ 24.3 对放行。容器内重跑仍未做（#25 在跑）。

| 文件 | sha256 |
| --- | --- |
| `fetch_external_questions.py` | `3c949040a36101e2446020b9b9dfee78c82b3ff930697a7ca58a9c0ff03b61c2` |
| `verify_question_origin.py` | `a77eeecbb0f929c2b4b3d198ad74fc46d5326dda5ad7d3fe9de2b199091bc20d` |
