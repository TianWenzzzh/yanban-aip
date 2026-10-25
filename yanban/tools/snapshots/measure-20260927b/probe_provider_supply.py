"""起跑前体检：四个文献源现在到底活着几个（零模型调用、每源最多一次检索）。

为什么要它：事项二那场 A/B 判「未测」（RC=3）不是因为两臂等价，而是**可用对只剩 10/15**，
5 对全部损耗在「走到检索轮但两臂都 0 卡」。容器裸日志里数得出 10 次 provider 熔断
（OpenAlex、Semantic Scholar 各自 120→240→480→900s 连击，arXiv 一次 406）
⇒ **可用对分母是供给的函数，不是料的函数**：同一批「首问即放行」的真人料，
上一场 9/9 全可用、这一场 10/15。拿这种窗口去跑一小时 A/B，
等于把预算花在测一个会被限流判死的数上。

测量实现与 A/B 长跑的起跑前置**共用 `supply_gate.py`**（两处各写一遍，迟早一个印"窗口健康"、
一个印"活的源 1"，而两边都自称同一把尺）。本文件只留判读与退出码。
🔴 熔断表是**进程内**的（`providers/base.py:_DOWN_UNTIL`），本探针是新进程 ⇒ 看不见长跑那边的冷却，
所以这一台只测"此刻 HTTP 通不通"；"那一场当时有几个源活着"的证据是那一场的日志（`g165`/`g166`）。

判据与退出码（跑前写死；同一台仪器三种结论不许共用一个码）：
  P1 四个源各发**一条**固定查询（同一串英文主题词），报耗时与回条数；抛错印异常类型与原因。
  P2 「活」= 拿到 ≥1 条。0 条与不可用分开记（轮 96 的口径：混在一起会把限流读成相关性差）。
  P3 外发包数在 **httpx 传输层**数（`AsyncClient.send`），四源各一次的上限是 4。
     🔴 不用产品自己的 `provider_stats()['calls']`：实测四包都发出去它仍报 0 ⇒ 那条判据恒真
     （首版拿了它，于是这条线永远不会红）。
  P4 退出码：
       RC=0 活的源 ≥2 且外发 ≤4 包 ⇒ 窗口健康，允许起跑；
       RC=3 活的源 <2 ⇒ 窗口不健康，别拿一小时的模型调用去换一个会被供给判死的数；
       RC=5 活的源 ≥2 但外发 >4 包 ⇒ 可以起跑，但未治项 N2（429 的 HTTP 层重试）在场；
            这不是仪器坏了，也不许读成「包数没问题」。
     换代记录：`measure-20260926h` 把第三种情况判成 RC=2「读数作废」——归因错了（多出来的包是
     产品对 429 重试发的，不是体检发的）；`measure-20260926j` 拆成 0/3/5 三码；
     本版只把**测量**并进共用模块，判读与退出码一字未改 ⇒ 跑出来的形状应与 j 同
     （不同时刻供给本来就抖，比的是"抛错/0 条/活"三分与包数上限，不是比数值）。

跑法（容器里才有 GLM 密钥与文献源；`supply_gate.py` 必须一起上传）：
  MSYS_NO_PATHCONV=1 PYTHONUTF8=1 /d/anaconda/python.exe 输出/scripts/run_cloud_probe.py \\
     E:/AIC智能体迁移包/输出/scripts/probe_provider_supply.py \\
     E:/AIC智能体迁移包/输出/scripts/supply_gate.py
本地自检（不碰网络、不碰容器；验的是共用模块那套判据会不会咬人）：
  PYTHONUTF8=1 code/yanban/.venv/Scripts/python.exe 输出/scripts/probe_provider_supply.py --selfcheck
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/tmp")
_HERE = pathlib.Path(__file__).resolve()
for _cand in ["/app", "/tmp", str(pathlib.Path.cwd()), *map(str, _HERE.parents)]:
    if (pathlib.Path(_cand) / "companion").is_dir():
        sys.path.insert(0, _cand)
        break
else:
    print("判读：找不到含 companion/ 的仓根 ⇒ 仪器没连上被测物（RC=2）")
    sys.exit(2)

LIVE_MIN = 2
PACKET_CAP = 4


def main() -> int:
    try:
        from supply_gate import supply_now
    except ImportError as exc:
        print(f"判读：取不到共用模块 supply_gate（{exc}）⇒ 不许退回自己写一遍，"
              "体检与长跑不同源就不算同一把尺（RC=2）")
        return 2
    lines, live, packets, names = supply_now()
    for text in lines:
        print("  " + text)
    print(f"\nP3 httpx 传输层实数外发 {packets} 包（四源各一次的上限是 {PACKET_CAP}）")
    print(f"P2/P4 活的源 {live} 个" + (f"（{'、'.join(names)}）" if names else "（无）")
          + f"｜起跑下限 {LIVE_MIN} 个")
    if packets > PACKET_CAP:
        print("   🔴 包数超 4 = 未治项 N2 在场：`providers/base.py` 的 HTTP 层对 429 重试，"
              "**每撞一次限流的源要多敲两包**。这不是体检在刷配额（体检只发起 4 次检索），"
              "所以不许判成「读数作废」——见 P4 的退出码语义。")
    if live < LIVE_MIN:
        print(f"判读：只有 {live} 源可用 ⇒ 现在跑 A/B，可用对会被供给判死（上一场就是这么丢的），"
              "等配额窗口再跑（RC=3，这是窗口结论，不是产品结论）")
        return 3
    if packets > PACKET_CAP:
        print(f"判读：窗口够用（{live} 源可用）可以起跑，但未治项 N2 的重试放大在场（{packets} 包）"
              "⇒ 单独退出码 5：允许跑，不许把这条读成仪器坏了，也不许据此说「包数没问题」（RC=5）")
        return 5
    print(f"判读：窗口健康（{live} 源可用、{packets} 包 = 每源一次）⇒ 允许起跑 A/B（RC=0）")
    return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.path.insert(0, str(_HERE.parent))
        from supply_gate import selfcheck as gate_selfcheck
        sys.exit(gate_selfcheck())
    sys.exit(main())
