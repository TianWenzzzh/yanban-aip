"""钉住测量仪器快照的字节：`tools/snapshots/measure-20260923/` 是归档，不是第二份工作副本。

为什么要用测试来钉而不是只写一句"别改这里"：同名代码存在两份时，最早坏掉的是"哪一份产出了这个数"。
快照目录里任何字节变动都会让 `docs/evidence/g87…g89` 的可复现性变成口头承诺。
要更新仪器就改迁移包 `输出/scripts/` 的工作副本，然后**新建一个日期目录**放新快照（本测试不动）。
"""
from __future__ import annotations

import hashlib
import pathlib

SNAPSHOTS = pathlib.Path(__file__).resolve().parents[1] / "tools" / "snapshots"

# sha256 ← 冻结当场打印所得（measure-20260923 = 轮 112，measure-20260923b = 轮 113），不是凭记忆写的
PINNED = {
    "measure-20260923": {
        "verify_title_claims.py":
            "fa54317f7044757928f41178a4aa4a3a1ad6a1a55c96faa9b5c53bf3ae05ded1",
        "probe_critique_depth.py":
            "693902fb1b617d2792cffec1d0d655c1358de10846afe45032712c3491c85cf6",
        "probe_counterev_position.py":
            "9d126821fa0f9f66c0e4596f902201bb9d08b59499f53b582ef551a386ba1d57",
    },
    "measure-20260923b": {
        "probe_counterev_supply.py":
            "2f135b2f934de8e35c2e2257ec779e26f26e1f339a05b562756467f91b335977",
    },
    "measure-20260923c": {
        "probe_counterev_supply.py":
            "e0fc4a1f8529704abca72633af7bb0108abc09d93c13d6418a3a429741f72336",
    },
    # d 这一份是从**容器里**取回的：跑完之后工作副本又改过，复制工作副本就等于钉住一份从没跑过的代码
    "measure-20260923d": {
        "probe_gate_yield_v2.py":
            "c197a37415e3e866fa38e762aaec3df448b960ff4fae2636093e52c4a1d6d689",
    },
    # e 冻结在**跑 g96 之前**：它是轮 115 那条预登记判据（有卡零引用 33%）的出处，
    # 判据一旦写死，产出它的仪器就不许再动，否则"基线"会变成跟着读数走的东西
    "measure-20260923e": {
        "probe_cite_density_baseline.py":
            "615682e7a36be8b3d5da3c40d9f74ac437dc50dafac14f1513ab882f3a78bf32",
    },
    # f = 轮 115 两台仪器**事后补钉**，字节从云端容器 /tmp 取回（工作副本跑完之后又改过，
    # 复制工作副本等于钉一份从没跑过的代码）。取回时容器 sha 与本地 sha 双向核过。
    "measure-20260923f": {
        "probe_citation_gap.py":
            "859c404913bc6989e07a7a822054cf6da177f405de9b30dd1f445e7bf0cf4777",
        "probe_gate_yield_v2.py":
            "0354cb048e92b4e61738b7a9dbe93c6e00d68e6ba6e786a743e007bfcb2e9387",
    },
    # g = 轮 116 起 g97 之前钉的新一代：给 `no_citation` 配上"带卡回复数"这个分母
    "measure-20260923g": {
        "probe_gate_yield_v2.py":
            "f24bc80b9f799da0c51ba931250a459624e552721c36fc49eb0699d932cbf8fe",
        "parse_gate_yield_lines.py":
            "f2680e0b1d117f793f2d4a9dd0a0f4912e744ac318de8231ff32071a439978e3",
    },
    # h = 轮 117 起 g98 之前钉的新一代：逐样本行加「前置长度 + 接地率」两列、
    # 代价按实质/过场两档分开、`--cases` 能吃 #19 的线上靶子池。
    # 这一代的「改坏必须红」由 输出/scripts/mutate_tier_selfcheck.py 守着（README 记它的 sha）。
    "measure-20260923h": {
        "parse_gate_yield_lines.py":
            "d2606c18674a1b7006081f23aa252e52afe442b4b8f1c06825bd0c6deeffdd7b",
        "probe_gate_yield_v2.py":
            "5be1011ecc80bfda935f78bff7cb822aa777844ca564b6d8942d1d984bd04281",
    },
    # 判 g98 的那一代：探针字节与 h 相同（冻结脚本当场断言过），判分器与它的离线测试是新的一代
    "measure-20260924i": {
        "parse_gate_yield_lines.py":
            "3a07bd776eea8781cc1c90b238cf07c3014ebf235779bfbdf5b904bd5eb687d2",
        "probe_gate_yield_v2.py":
            "5be1011ecc80bfda935f78bff7cb822aa777844ca564b6d8942d1d984bd04281",
        "test_judge_g98_offline.py":
            "bbf839b0e0bb7292e7188dad43d291c756464acaabf0a3e996934194b4d19562",
        "test_parse_tier_offline.py":
            "fc0b686ba1be1e97601f6f8cf9e7d56d07f192ba5d92065ec8a4d4501c183ee5",
    },
    "measure-20260924j": {
        "mutate_trace_selfcheck.py":
            "6a8c4dea71d76316db46a2979f52c135b836f9ebb945bb812913f95a1a981a1d",
        "probe_trace_vs_count.py":
            "20627646b59016632714b14a11ca96e18c88d9aaab0677c1d2c1cdb7d50b6518",
    },
    # k = 判 g100（轮 119 规划层 A/B）的那一代探针，字节同样从容器 /tmp 取回：
    # 工作副本在 A/B 跑完之后把自检 M 拆成了 M/N，复制过来就等于钉一份没产出读数的代码。
    # 守卫对这一份**只抓 7 条**（M7「漏掐 plan_prefix」打不红），README 里写明了为什么
    # 读数仍然有效（S1 是运行时哨兵，实测 leak=0/12）—— 别把这条当成"8/8 已验"。
    "measure-20260924k": {
        "mutate_plan_ab_selfcheck.py":
            "16d6f498dc695b0429f445d8f569243f058884d672463224419f8a03c95991f9",
        "probe_plan_ab.py":
            "3857cca42e8dba1da5655866b412590600869667a030c6c2661656d97b79534c",
    },
    # l = 轮 123/124 的配对读数仪器（产出 g105）。冻的是跑完 guard 之后的工作副本字节。
    # 这一代值得单独一句：`pairwise_note.py` 是四台共用的判断，**它一旦动，四台一起动**，
    # 所以它的 sha 是这条链的锚；guard 的 M3 就是靠就地改它来验证"三台同时红"。
    "measure-20260924l": {
        "pairwise_note.py":
            "e7f82f672d40571ce3369a4991022d3d56f601d15093a87e89f512637c3101a4",
        "guard_pairwise_readers.py":
            "3bb1d288a93bcd78c64afe7b306e104453ebb2320d6da061380097999002f1fa",
        "exp_tie_convention.py":
            "4a6f5241b23a1c1c54b85eb8bb2d02abe24df378366e2d8f44853a5e129ef2c3",
        "read_lcarm.py":
            "5ab5d5f4dd899a2dc7a6e78cc80762f280e574479bd57aa709bfbebcdad3bae0",
        "analyze_blind_pairs.py":
            "f34e416ef0a3505599cc8174590a33d464c9da2c30cb1b3cca34922e17277511",
        "lc_winrate.py":
            "38d7285339ceb6deeda3eb7ff3f10ef586ff8abc242b3c2edf47e2a71ec72e80",
        "probe_citation_effect.py":
            "52de28513c9c485571633553c284bff45510d75d3c0bb6941bb940be81cf3241",
    },
    # m = 轮 126 的产物端点验收仪器（产出 g109）。冻的是线上 12/12 那次跑完之后的字节。
    # C6 是这台仪器的正对照：只出「未测」的池子不算这一维过了 ⇒ 零开火它自己退 4。
    "measure-20260924m": {
        "probe_artifact_online.py":
            "37e53f20bc6b577a3c1ce5313cd6b86b679e7da9cfe7d91f16c493ac1211aca5",
        "render_artifact_demo.py":
            "d6c2524e9507f2926e07ce80aa8477a08bfec45dfd6c2b666d2bf2a68c15346f",
    },
    # n = 轮 130 的产物台账验收仪器。这一代多了一道 **C8**：逐句台账必须与汇总数对得上账
    # （行数 == min(带出处句数, 产品常量 `_TRAIL_MAX`)，且「给出依据 + 明说查无」== 行数）。
    # 封顶从线上那份产品读，读不到就 RC=2 —— 在这钉 12 的话，改产品常量时这台仪器不会红。
    "measure-20260924n": {
        "probe_artifact_online.py":
            "83bf17838388e658499d81f2bec8e5cf18f71099eb64a97da6495b8df7dbf2ac",
    },
    # o 这一份同样是从容器 /tmp 取回来的（跑完即取，工作副本与容器份 sha 当场双向核对过）
    "measure-20260924o": {
        "probe_plan_ab.py":
            "6f663e924775eacec1e3dade9eb7b7065a39f8c2d56e64cfa43e38130637da20",
    },
    "measure-20260924p": {
        "probe_gap_attribution.py":
            "5428fcd944cb145c054b4ccb4efa31c05347765a6e09d440a30052db74136fe0",
    },
    "measure-20260924q": {
        "probe_gap_shape.py":
            "c9590ce0293c9a74d978b620fb4e74271a8683b94049c0df3c3860c476b9bfe3",
    },
    "measure-20260924r": {
        "probe_ref_precision.py":
            "927a7919f9f27b1b1ac9c4f1b5e23ded947274b6efd632557afcc21d8553e1d9",
    },
    # 25a = 轮 138 的 #25 A/B。probe_plan_ab.py **不在这里重复存**：容器里真跑过那一份与
    # measure-20260924o 逐字节同一份（sha 6f663e92…），同名代码放两份时最早坏掉的就是
    # "哪一份产出了这个数"。本目录只钉这轮才第一次上传进容器的前置件 sandbox_data.py。
    "measure-20260925a": {
        "sandbox_data.py":
            "7f98ff37be4361f186618e690d768629f388addc497d0b78ae2f1c8328f8b065",
    },
    # 25b = 轮 140 的 #26 外部语料两件仪器。g132 那个「206 条外部提问、自家树 0 命中、
    # 正对照 7/7」的读数是这两份印的，判线一改（比如把正对照删了）读数就再也复现不出来。
    "measure-20260925b": {
        "fetch_external_questions.py":
            "3c949040a36101e2446020b9b9dfee78c82b3ff930697a7ca58a9c0ff03b61c2",
        "verify_question_origin.py":
            "a77eeecbb0f929c2b4b3d198ad74fc46d5326dda5ad7d3fe9de2b199091bc20d",
    },
    # 25c = 轮 141 起跑、轮 144 收尾的 #25 第一次真跑（外部池 N=70）。字节从容器 /tmp 取回，
    # 与工作副本当场比过一致（同一 sha 也印在 probe_supply_precheck.py 的阈值来源行里）。
    "measure-20260925c": {
        "probe_plan_ab.py":
            "4283ca1ca5c3663831d780ce27bd0f3a76d969b5ba56c4df0700b3f94c5b9c37",
    },
    # 25d/25e = 轮 144 那对互相矛盾的读数（体检 69/70 有卡 vs A/B 140 条零卡）的两台仪器。
    # 归因结论只由这两份字节印出，改判据（比如把 D1/D2/D3 的分支调个向）历史读数就复现不出来。
    "measure-20260925d": {
        "probe_supply_precheck.py":
            "dfac15fe08670f176ba154bf7017cc4053d9e13cbf033dfa1b28c09a080569d4",
    },
    "measure-20260925e": {
        "probe_card_drop.py":
            "b83fd1f1acccb64c69c35e039229fea1789f8030cdbbad0f5a23e23f8e4fed3b",
    },
    # 25f = 轮 145 的前置量具（外部池 206 条里首问能进检索轮的条数）。这台是**本机**跑的仪器，
    # 没有容器份可比，所以「这份字节真产出过这个读数」由 land_r145_freeze.py 当场跑三场
    # （自检 / 正常 / 植入负控制）并逐场核对退出码来保证，证据正文是当场捕获的 stdout。
    "measure-20260925f": {
        "probe_wants_results.py":
            "362b58a471e369e50f83e64bce2c1473ee1dc54702546626ebe23c8128b382b4",
    },
    # 25g/25h = 轮 146 那对**云端**仪器（通道活性 + 模型原始返回）；字节从容器 /tmp 取回，
    # 与工作副本当场比过一致。25h 自己印的 R 分类不可信（校验复刻不全），它的价值只有「通道活着」
    # 与「模型原文」两条，权威成因改由 25i 给。
    # 25i = 轮 146 的本机零模型重放（逐道调用产品的 _topic_is_shell / _TOPIC_FILLER），
    # 是「英语 topic 结构性进不了检索」这句话的唯一出处。
    "measure-20260925g": {
        "probe_topic_channel.py":
            "81fde81fa6974acb9230ba9927e47f53adc440c48e0151a54ed1dce557972302",
    },
    "measure-20260925h": {
        "probe_topic_reject_reason.py":
            "39c959c9ff911ef5cb77e75b49b72ffacadca041a741ae05e2119946531db7b4",
    },
    "measure-20260925i": {
        "explain_topic_reject_local.py":
            "b74a61499ffdf5cd7a5cf6439bb9d616cf4639be62895e3fa93742a7cf64c012",
    },
    # 25j = 轮 147 的壳词闸门对照量具（修复前／修复后两段在同一份证据文件里）。
    "measure-20260925j": {
        "probe_topic_guard_shapes.py":
            "70b1f31a9cfc3496f6e94a1f0b9193ebc12d63d0652561a08987aae31edc4c10",
    },
    # 25k = 轮 147 的线上行为复量（既比字节也调线上函数），字节从容器 /tmp 取回。
    "measure-20260925k": {
        "probe_deployed_shell_guard.py":
            "f5daaed1d2ac9abd98c7c963295c878abe2df55979ed380ee545394a3f33a26c",
    },
    # 25l = 轮 148 的修复前后重放（旧版走 git 字节 exec），本机零模型调用。
    "measure-20260925l": {
        "probe_latin_topic_replay.py":
            "ab52d3e9604ec1763e43a18a4ded51af0296bd8312336538d7b328703397157c",
    },
    # 25m = 轮 149 的外部池普查（本机跑，判据全部现读产品函数/参数）。
    "measure-20260925m": {
        "probe_pool_readiness_local.py":
            "b8150f15f066b688951ec0b130b68644005cfd1f09c41b7a76c875baf7b9d32d",
    },
    # 25n = 轮 150 的线上库只读盘点，字节从容器 /tmp 取回（跑完之后工作副本又改过就是事故）。
    "measure-20260925n": {
        "probe_online_user_msgs.py":
            "5dccf7a7c513c29e59e7d6f542f7659317bea3372a3d6d0b20358dd26a08c2d5",
    },
    # 25o = 轮 152 的模板量具（本机跑，Z0 计数桩 + --plant-net 植入场自证零模型调用），
    # sha 取 land_r152_freeze.py 冻结当场打印的那一行，不是凭记忆写的
    "measure-20260925o": {
        "probe_topic_capture_corpus.py":
            "5d1b793c7ce0c3ddf9d848935ee9be77bc4bfe241bbb1bf2eb262f9ca7f0d3ca",
    },
    # 25p = 轮 152 补记的线上复量，字节从容器 /tmp 取回（与本机工作副本当场对过，两份一致）
    "measure-20260925p": {
        "probe_deployed_topic_capture.py":
            "8611e6fa4cbb259f455094d027e88541737d9ff12846f481f11965e526692c92",
    },
    # 25q = 轮 152 追加的端到端量具（这场**真发模型调用**，计数桩方向相反：必须 ≥1），容器 /tmp 取回
    "measure-20260925q": {
        "probe_deployed_ceiling_e2e.py":
            "2187969800baa608238ac2456da867e3cff422b81f4c7e476fd685c8d08cdcb4",
    },
    # 25r = 轮 153 的线上复量：右栏进度卡那三个字段必须在容器那份函数上成立（零模型调用、零写库）。
    # 容器 /tmp 取回，与工作副本当场对过一致；README 另记了同轮抓到的「同长度植入骗过 .pyc」这条仪器缺陷。
    "measure-20260925r": {
        "probe_deployed_meta.py":
            "44fbe69b1c7c31029fc87b1b73b76cb8877b42a2c46c4812a589d5e28cdd8aa8",
    },
    # 轮 154：这四台都是**本机跑**的仪器（抓取/普查/截图/选料），容器里没有它们的字节，
    # 所以用 `freeze_instruments.py --local` 钉工作副本——README 里逐台写明了这一点。
    "measure-20260926a": {
        "harvest_bili_pool.py":
            "2ec31ab8aec06abe4ac1bbe0c15c4051cd7eeb06f99ef8c00efbc5f9b1a18be0",
    },
    "measure-20260926b": {
        "probe_pool_readiness_local.py":
            "380de0a81b960848635c8b3280fa824912a9cc13c4e530e05bb0393f44f388e3",
    },
    "measure-20260926c": {
        "shot_companion_states.mjs":
            "b39ffd1a0f0fab5147e51b65e0f7ccfecab188b8861cc4d39ea38efa40c9f591",
    },
    "measure-20260926d": {
        "select_ab_cases.py":
            "34167646d3a8254983f1f6f7878d0ef31d8a7c3d40e5e17945d53fe9c14695a5",
    },
    # 轮 155 事项一：容器份与工作副本逐字节相同（freeze 时打印过 两份一致=True）
    "measure-20260926e": {
        "probe_renumber_replay.py":
            "544a26dbd1232e8c155b6b3204c75ceac3108119e23e2b082e205bbc8c29c07d",
    },
    # 轮 155 补充：接地尺的轮转判别力（同一批现场与 g160 对上账：148/174 逐位相同）
    "measure-20260926f": {
        "probe_anchor_order_check.py":
            "7def9545bd013fe1ac4fdce122678ab13d075d5338c3eb5fec5bb951581128fb",
    },
    # 轮 155 事项二：A/B 第二场（新预登记）与起跑前供给体检
    "measure-20260926g": {
        "probe_gap_subquery_ab_v2.py":
            "aa1ca405cdb9fabf54420baa1b1efa2ccfdb03e5db0c629626e9f683d148a29e",
    },
    "measure-20260926h": {
        "probe_provider_supply.py":
            "7976965c4237670bbdbf50dea0e79afc479dd71937bae6fd5ad1e16d82e3091c",
    },
    # 轮 155 追加四：B 的挂角标负担普查（g167）
    "measure-20260926i": {
        "probe_reference_fidelity.py":
            "7461ab926615db1006fd374557ac5ffc07369dc0dfdfaca6f1583372b689e1fc",
    },
    # 轮 155 追加四：供给体检的第二版（退出码拆成 0/3/5）。
    # 🔴 与上面 20260926h **同名不同字节**是故意的：h 是产出 `g165` 的那一份，
    #    j 是产出 `g166` 后半段的那一份。快照按"谁产出的数"分代钉，不按文件名覆盖。
    "measure-20260926j": {
        "probe_provider_supply.py":
            "546cc23ee3a4958c428346833c1f1b8e868921ecd57f4b3f871cc47c4009f175",
    },
    # 轮 155 追加六：A/B 第三场（g168，15 对全可用）用的仪器。
    # 🔴 与 measure-20260926g **同名不同字节**是故意的：g 那份跑出 10/13 判「未测」，
    #    a 这份只在开头多了一道 S0 起跑前置（六个判据常量逐字未变），按"谁产出的数"分代钉。
    "measure-20260927a": {
        "probe_gap_subquery_ab_v2.py":
            "e23a8d70f492c058499ac149430cc706e4dd81dfd38b42523ea3b72f937120e9",
    },
    # 轮 157：N2（429 退出 HTTP 层重试）的前后数由这一对产出。
    # supply_gate.py 是第一次冻（g168 那次跑过但按规矩没冻）；probe_provider_supply.py
    # 与 measure-20260926j 那份字节已不同（j=546cc2…，本代=b7b701…），按"谁产出的数"分代钉。
    "measure-20260927b": {
        "probe_provider_supply.py":
            "b7b701bad302fafdba8148cc42fd6888825c204a06286384580076d9bf16f3ce",
        "supply_gate.py":
            "d19ac1fbba86da21c66297ddf60d196ad14e5c5c96990c04212c9acd9b59150b",
    },
    # 轮 159：N3 量具的第一份线上读数（12 份真报告 / 只读库 / 零模型调用）由这台产出。
    # 判线里「旋转对照」被自己的 RC=2 拦下过一次（库里只有 3 个主题，旋转≈同一批材料），
    # 换成跨领域对照才分得开——这段来龙去脉在快照 README 与 g171 头部。
    "measure-20260927c": {
        "probe_rescuable_oracle.py":
            "9e5c2d41728524252333c8c00904f435d079c533253753ae4bc7820dc2379861",
    },
    # 轮 160：真子查询漏斗与 reach（24 份报告 / 独立子查询只有 3 条 / 只读库 / 零模型调用）。
    # 同一台仪器这一轮跑过四次，判线在两处被读数逼着改口（Q1 文件时间戳→内容指纹+拒写、
    # reach 分母"只问全库"→"本场够得着的材料全集"），钉的是产出 11/25=44% 那一次的字节；
    # 前三个字节版本没冻——它们产出的数不在这份证据里，冻了反而误导复现。
    # 另一台是改口的依据（-shm 在零连接静置下自己动 / 内容指纹对真写敏感）。
    "measure-20260927d": {
        "probe_subquery_funnel.py":
            "0aa1728a78b515dc3838f4ea68d5f9bf657b187ae700eb31a092d632971ec7d6",
        "probe_wal_who_touches.py":
            "73d510a83f941d5853be1b4ba966d2c264817e25155d942e1a38da6fca92c407",
    },
    # 轮 161：①「改检索词面」的立项前提被这台仪器自己否掉（六条确定性候选式子最优的一条与现状打平，
    # 11 vs 11 句，判线要 ≥+2），所以它钉的是一次**没改产品码**的证据。
    # 同一轮里它还提供了一条反例：整表口径救 0 句的那行，小并集反而救 1 句 ⇒
    # `supplement_value` 对候选集不单调，"天花板"不是上界（行为由 test_supplement_value 的 V6 钉住）。
    "measure-20260927e": {
        "probe_legit_ceiling.py":
            "0143071453370a8fe7582ec8ef93cb248acf37eb59d4a857431d035dc74c6290",
    },
    # 轮 162：① 转向"供给"之后的四台根因仪器。它们各自否掉了一句旧断言：
    #   base.py 的「429 的 Retry-After 是日额度级」（实测 32—37 秒，等够就 200）、
    #   以及「arXiv 406 是出口 IP 被拒」（应答方是 varnish/CDN 边缘，且**不带任何参数**的裸 URL 也 406
    #   ⇒ 形状修不动，这条旧结论方向没错但归因写错了对象）。
    # 配对那台（cooldown_policy_paired）的结论是**收益未证实**（+1 轮 < 判线 2 轮），
    # 冻它是为了让下一场能原样重放这份轨迹，不是给一次成功背书。
    "measure-20260927f": {
        "probe_supply_failure_causes.py":
            "20fdfa239cbc4d4c926260c07d3a69fb4853bceb5ab5f1f9ff11385e7cd5e041",
        "probe_arxiv_who_answers.py":
            "265fef4398ed28c73d49d801c47b9404090c9b4603459d351efb32d43341089e",
        "probe_retry_after_window.py":
            "a8388d5a2a9a8db2ccebadbc55456baad2490a9ddcda4b1d2748bfb5458ee61b",
        "probe_cooldown_policy_paired.py":
            "df28cb8d15cc6634c2abeb746e0f01220b67a480ffb5f30a556c556aa1fabb23",
    },
}


def test_snapshot_dir_also_carries_a_readme_naming_the_hashes():
    for snap, files in PINNED.items():
        text = (SNAPSHOTS / snap / "README.md").read_text(encoding="utf-8")
        for name, digest in files.items():
            assert digest in text, f"{snap}/README 里的 {name} 哈希与钉住的值不一致（两处一旦分开就没人知道信哪个）"


def test_frozen_instruments_are_byte_identical():
    for snap, files in PINNED.items():
        for name, digest in files.items():
            path = SNAPSHOTS / snap / name
            assert path.is_file(), f"快照缺文件 {snap}/{name}：已归档读数的将无法复现"
            got = hashlib.sha256(path.read_bytes()).hexdigest()
            assert got == digest, \
                f"{snap}/{name} 被改过，快照已不再是产出那些数的那一份\n  期望 {digest}\n  实际 {got}"


def test_every_snapshot_on_disk_is_pinned():
    """防"落盘了但没钉"：冻结脚本会建目录、复制字节，而 PINNED 是另一处手工编辑。

    为什么单开一条：上面两条测试都只遍历 PINNED，所以一次只改了一半的冻结
    （新建了 measure-* 目录却忘了登记哈希）会让那份读数**仍然没有**任何测试守着，
    而整套测试照绿——这是"人工维护层不进 sha"的同一类事故。
    """
    on_disk = {p.name for p in SNAPSHOTS.iterdir() if p.is_dir() and p.name.startswith("measure-")}
    missing_dirs = sorted(on_disk - set(PINNED))
    assert not missing_dirs, f"这些快照目录没登记进 PINNED，等于没钉：{missing_dirs}"
    for snap in sorted(on_disk):
        py = {p.name for p in (SNAPSHOTS / snap).glob("*.py")}
        untracked = sorted(py - set(PINNED[snap]))
        assert not untracked, f"{snap} 里有 .py 未钉哈希（多一份没主的同名代码）：{untracked}"
    assert on_disk == {p.name for p in SNAPSHOTS.iterdir() if p.is_dir()}, \
        "snapshots 下出现了非 measure-* 目录：命名约定一旦被绕过，这条扫描就会漏掉它"
