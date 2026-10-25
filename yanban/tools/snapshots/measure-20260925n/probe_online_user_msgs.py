"""轮 150：线上库里到底有没有**不是我们造的**中文用户原话——零模型调用、只读、按日期冻结。

为什么要量这件事：#26 要"换掉自家提问"，而唯一天然外部、逐字可得、又不需要爬公网的中文料，
就是产品自己收过的真实用户消息。但线上 754 个项目绝大多数是探针自造的（已登记的污染面），
所以"有多少条真是外部用户说的"必须先机械分类，不能靠眼看两条就下结论。

分类口径（机械、互斥、可复核）：
  E  与仓内某份**外部池**逐字相同（g132 英语池）⇒ 外部来源，但是英文；
  S  与仓内**自家情景池**逐字相同（g98 手工情景 + g114 两份线上盘点）⇒ 自家提问，正是 #26 要换掉的东西；
     （g114 本身就是从这张库的 messages 里盘点出来的 70 条，所以它算自家料的另一种落盘形式，不是外部）
  U  两条都不是 ⇒ 未知来源，需要人工看是否真实用户；
  分类器两条分支各配一条控制（不是"两侧都要命中"——E 侧 0 命中是**预期读数**：
  g132 那批英语提问从没走过线上对话路径，轮 144 只把它喂给纯函数 `extract_update`，没有落 messages）：
  S 侧取库里真实存在的一条要求逐字命中；U 侧把那条尾巴加一段"植入串"后重分类，必须落 U
  ——落不进去就是分类器恒真 ⇒ RC=2。

预登记判据（跑前写死）：
  R0 只读自证：DB 的 mtime+size 在**任何查询之前**取一次，跑完再取一次，逐位相同；否则 RC=2。
  R1 认路哨兵：`docs/evidence/` 下三份池子文件必须都能读到且非空，否则分母是假的 ⇒ RC=2。
  R2 计数：user 消息总数、按 E/S/U 分开的条数，以及 U 里中文占比（按「含 ≥2 个汉字」判）。
  R3 决策量：U 且中文的条数 ≥ 1 时逐条打印（截断到 90 字），供人工判定是不是真实用户；
     = 0 ⇒ 结论是"库里没有外部中文料，#26 必须走公网来源"（这本身是有效负结果，落文档）。
  零模型调用由机制保证：本仪器**自己把 `LLM_MOCK` 设成 1**（容器里默认是关的，线上要真调用），
  并给 `httpx.AsyncClient.post` 装雷（触发数必须 0）；
  `PROBE_PLANT_TRIP=1` 主动撞一次雷，必须红在 Z 行（不点雷就分不清"零网络"与"雷是装饰"）。
  收尾行以「判读：」开头，聚合只在跑完整份之后打印。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
import sys

os.environ["LLM_MOCK"] = "1"   # 必须早于任何 `import llm`：容器里这个变量默认是关的（线上要真调用）

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

DB = pathlib.Path("/app/data/yanban.db")
EVIDENCE = pathlib.Path("/tmp")      # run_cloud_probe 把数据文件 docker cp 到容器 /tmp
EXTERNAL_EN = ["g132_external_questions_pool.json"]
SELF_POOLS = ["g98_cite_pool.json", "g114_question_pool.json", "g114_question_pool_full70.json"]


def texts_from(names: list[str]) -> set[str]:
    out: set[str] = set()
    for n in names:
        p = EVIDENCE / n
        if not p.is_file():
            print(f"R1 认路哨兵：{n} 不在容器里 ⇒ 分母会是假的")
            return set()
        rows = json.loads(p.read_text(encoding="utf-8"))
        for r in rows:
            t = (r.get("user_text") or "").strip() if isinstance(r, dict) else str(r).strip()
            if t:
                out.add(html_unescape(t))
    return out


def html_unescape(s: str) -> str:
    from html import unescape
    return unescape(s)


def main() -> int:
    import llm
    st0 = DB.stat()
    trip = {"net": 0}
    import httpx

    async def boom(*a, **k):
        trip["net"] += 1
        raise AssertionError("本仪器声称零网络")

    httpx.AsyncClient.post = boom
    print(f"C1 环境：LLM_MOCK={llm.LLM_MOCK}｜DB={DB}｜size={st0.st_size}")
    if not llm.LLM_MOCK:
        print("判读：LLM_MOCK 没生效 ⇒ 有真调用风险（RC=2）")
        return 2

    ext = texts_from(EXTERNAL_EN)
    selfp = texts_from(SELF_POOLS)
    if not ext or not selfp:
        print("判读：R1 不过——池子文件读不到，分类集会空 ⇒ 整份不作数（RC=2）")
        return 2
    print(f"R1 池子规模：外部英语 {len(ext)} 条｜自家情景 {len(selfp)} 条")

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT m.content, p.name, m.created_at FROM messages m "
        "JOIN conversations c ON c.id=m.conversation_id JOIN projects p ON p.id=c.project_id "
        "WHERE m.role='user' ORDER BY m.created_at"
    ).fetchall()

    cats = {"E": [], "S": [], "U": []}
    for content, pname, ts in rows:
        t = (content or "").strip()
        if not t:
            continue
        if t in ext:
            cats["E"].append((t, pname, ts))
        elif t in selfp:
            cats["S"].append((t, pname, ts))
        else:
            cats["U"].append((t, pname, ts))

    def is_zh(s: str) -> bool:
        return len(re.findall(r"[一-鿿]", s)) >= 2

    zh_u = [x for x in cats["U"] if is_zh(x[0])]
    total = sum(len(v) for v in cats.values())
    print(f"\nR2 user 消息：共 {total} 条（读自 messages.role='user'）")
    print(f"    E 逐字命中外部英语池：{len(cats['E'])} 条")
    print(f"    S 逐字命中自家情景池：{len(cats['S'])} 条")
    print(f"    U 两份都不命中：{len(cats['U'])} 条｜其中含 ≥2 汉字：{len(zh_u)} 条")
    # E=0 是**预期读数**而不是仪器故障：g132 那批英语提问从没走过线上对话路径（轮 144 只喂给
    # extract_update 这条纯函数，没有落 messages），所以库里本来就不该有它。
    # 分类器的两条分支改由植入用例盯：S 侧用库里真实存在的一条，U 侧用一条故意不在任何池里的串。
    if not cats["S"]:
        print("判读：S 侧一条都没命中 ⇒ 逐字比对没接上（编码/截断都会这样），整份不作数（RC=2）")
        return 2
    sample_s = cats["S"][0][0]
    plant = sample_s + "（植入串：故意不在任何池里）"
    got = "E" if plant in ext else "S" if plant in selfp else "U"
    print(f"    控制·S 侧：库里第一条逐字存在于自家池=True｜"
          f"控制·U 侧：把自家一条尾巴加 11 个字符后重分类 ⇒ {got}（必须 U，落不进去就是分类器恒真）")
    if got != "U":
        print("判读：U 分支的植入用例没过 ⇒ 分类器恒真，整份不作数（RC=2）")
        return 2

    if zh_u:
        print("\nR3 U 类中文逐条（最多 40 条，供人工判「是不是真实用户」）：")
        for t, pname, ts in zh_u[:40]:
            one = " ".join(t.split())[:90]
            print(f"    · [{ts}] 项目名={pname[:20]!r}｜{one}")
    else:
        print("\nR3 U 类中文 0 条")

    if os.environ.get("PROBE_PLANT_TRIP") or "--plant-trip" in sys.argv:
        # 容器跑法只认 argv：run_cloud_probe 是把脚本送进 `docker exec` 里执行的，
        # 本地 shell 的环境变量根本不会跟进去——实测第一场用环境变量"植入"，雷一声没响、
        # 照样 RC=0，那是假绿。所以容器侧一律用 `--plant-trip` 参数。
        import asyncio

        async def knock():
            async with httpx.AsyncClient() as c:
                try:
                    await c.post("http://127.0.0.1:1/p", json={})
                except Exception:  # noqa: BLE001
                    pass

        asyncio.run(knock())
    st1 = DB.stat()
    print(f"\nZ 零网络自证：httpx post 触发={trip['net']}（必须 0）")
    print(f"R0 只读自证：mtime/size 跑前={(st0.st_mtime_ns, st0.st_size)} "
          f"跑后={(st1.st_mtime_ns, st1.st_size)} 未变={(st0.st_mtime_ns, st0.st_size) == (st1.st_mtime_ns, st1.st_size)}")
    if trip["net"] or (st0.st_mtime_ns, st0.st_size) != (st1.st_mtime_ns, st1.st_size):
        print("判读：自证不过 ⇒ 读数不作数（RC=2）")
        return 2
    v = (f"库里有 {len(zh_u)} 条「非外部池、非自家池」的中文用户消息 ⇒ 可作 #26 的种子料，先人工过一遍"
         if zh_u else
         "库里没有一条外部中文料（user 消息全部逐字来自自家池或探针）⇒ #26 只能走公网来源，"
         "这是有效负结果，不是没测")
    print(f"\n判读：{v}（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
