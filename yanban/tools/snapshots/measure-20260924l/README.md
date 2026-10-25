# measure-20260924l —— 轮 123/124：配对读数脚本的三种态护栏

产出证据：`docs/evidence/g105_pairwise_reader_guard.txt`（判据 9/9、变异 4/4、g59 回归六数逐字未变）。
现场：`输出/scripts/` 工作副本，2026-09-24 07:1x 跑完 `guard_pairwise_readers.py`（RC=0）之后按字节拷进来。

- `pairwise_note.py` 是四台读数脚本共用的「部分样本」判断（轮 124 抽出来的）。
  **它的变异只能就地改，不能用 `_mut_` 副本** —— 调用方按名字 import，副本无效＝假绿。
- `exp_tie_convention.py` 的读数是一条**负结果**：11 份真实转储 143 局里平局 0、未判 0 ⇒
  四种分母取法同数，"统一口径"不立项。冻它是为了将来有人再提"统一"时能重跑同一把尺。
- 四台读数脚本（`read_lcarm` / `analyze_blind_pairs` / `lc_winrate` / `probe_citation_effect`）
  现在都走 `partial_note`；A4 顺带量到一个事实：**g59 本身就是 35/36 的部分样本**
  （18+17 行装在名为 n36 的文件里），日志引用它时历来按整份读。

## 冻结字节清单（当场算，与 tests2/test_measure_snapshot.py 的 PINNED 同源）

`test_snapshot_dir_also_carries_a_readme_naming_the_hashes` 要求 README 自己点名哈希——
这份表由脚本运行时写出，不手抄。

| 文件 | sha256 |
| --- | --- |
| `pairwise_note.py` | `e7f82f672d40571ce3369a4991022d3d56f601d15093a87e89f512637c3101a4` |
| `guard_pairwise_readers.py` | `3bb1d288a93bcd78c64afe7b306e104453ebb2320d6da061380097999002f1fa` |
| `exp_tie_convention.py` | `4a6f5241b23a1c1c54b85eb8bb2d02abe24df378366e2d8f44853a5e129ef2c3` |
| `read_lcarm.py` | `5ab5d5f4dd899a2dc7a6e78cc80762f280e574479bd57aa709bfbebcdad3bae0` |
| `analyze_blind_pairs.py` | `f34e416ef0a3505599cc8174590a33d464c9da2c30cb1b3cca34922e17277511` |
| `lc_winrate.py` | `38d7285339ceb6deeda3eb7ff3f10ef586ff8abc242b3c2edf47e2a71ec72e80` |
| `probe_citation_effect.py` | `52de28513c9c485571633553c284bff45510d75d3c0bb6941bb940be81cf3241` |
