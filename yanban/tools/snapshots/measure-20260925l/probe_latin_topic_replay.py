"""轮 148：轮 146 那句「纯英文 topic 永远进不了检索是代码级必然」还在吗——修复前后两份产品源码各重放一遍。

零模型调用、零网络：把 g141/g140 当场记下的**模型原始返回**喂进各版本的 `llm_topic`，
transport 换成计数桩。旧版不重写公式，直接从 git 取 `9f30425^` 那份字节 exec。

预登记判据（跑前写死，跑完不许改口径）：
  K1 尺子（正对照）：中文那句 + 模型给的 '钓鱼网站检测'，两版都必须非空且相等，否则整份作废 RC=2。
  K2 决策量：是否存在一例 topic 旧版空、新版非空 ⇒ 有 ⇒ 轮 146 那句作废；无 ⇒ 那句仍然成立。
  K3 反向控制：至少一例两版都空（否则不是"修短路"而是"把闸门拆了"）。
  K4 零网络自证：httpx 的 post 触发次数必须 =0；且**每次排队给桩的答案都必须被恰好消费一次**
     （consumed==queued、桩剩 0）——两道各配一场植入：`PROBE_PLANT_UNSTUB=1` 摘掉计数必须红在 K4；
     `PROBE_PLANT_NET=1` 主动发一次真请求，必须让雷响（不点雷就分不清「零网络」与「雷是装饰」）。
  K5 装载保真：exec 装的现役份必须与 import 进来的产品模块逐条同判，否则旧版读数不可信 RC=2。
  K6 下游：新版拿到非空 topic 的那例，用产品自己的 `ready_for_search` 量「有了 topic 是不是就放行」，
     不许把「topic 有了」读成「#25 通了」。
"""
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO = ROOT / "code" / "yanban"
EVD = REPO / "docs" / "evidence"
sys.path[:0] = [str(REPO)]

FIX_COMMIT = "9f30425"                                   # 轮 147 的壳词闸门修复
OLD_GUARD_LINE = "return len(cjk) < 2 or (not cjk and latin and all("


def load(src: str, name: str) -> dict:
    """按源码字符串装载 extractor：只把那句相对 import 换掉，其余逐字节照跑。"""
    import llm
    from companion.intent.model import RESEARCH_STAGES
    needle = "from .model import RESEARCH_STAGES"
    if src.count(needle) != 1:
        raise SystemExit(f"判读：{name} 里那句相对 import 不是恰好 1 次 ⇒ 停手（RC=2）")
    src = src.replace(needle, "RESEARCH_STAGES = globals().get('_RS')")
    ns: dict = {"__name__": name, "re": re, "_RS": RESEARCH_STAGES, "llm": llm}
    exec(compile(src, f"<{name}>", "exec"), ns)
    return ns


class Stub:
    """llm.chat_json 换成计数桩；顺手给 httpx 装雷——任何真网络尝试当场炸。"""

    def __init__(self) -> None:
        self.plant = bool(os.environ.get("PROBE_PLANT_UNSTUB"))
        self.netplant = bool(os.environ.get("PROBE_PLANT_NET"))
        self.queued = 0
        self.consumed = 0
        self.net = 0
        self.left: list[str] = []

    def ask(self, answer: str) -> None:
        self.queued += 1
        self.left = [answer]

    def install(self) -> None:
        import httpx
        import llm

        stub = self

        async def fake_chat_json(prompt: str, **kw):
            if not stub.plant:
                stub.consumed += 1
            if stub.netplant:
                # 活性探针：不点一下雷，就分不清"零网络"和"雷是装饰"
                async with httpx.AsyncClient() as c:
                    try:
                        await c.post("http://127.0.0.1:1/probe", json={})
                    except Exception:  # noqa: BLE001 — 雷自己抛的 AssertionError 也在这里吞
                        pass
            if not stub.left:
                raise AssertionError("桩被多调了一次 ⇒ 案例表与调用次数对不上，读数不作数")
            return {"topic": stub.left.pop(0)}

        async def boom(*a, **k):
            stub.net += 1
            raise AssertionError("本仪器声称零网络，但有人真发了请求")

        llm.chat_json = fake_chat_json
        httpx.AsyncClient.post = boom


def cases() -> dict:
    """案例文本与模型原始返回一律从证据文件、外部池现读，不手抄。"""
    pool = json.loads((EVD / "g132_external_questions_pool.json").read_text(encoding="utf-8"))
    g141 = (EVD / "g141_topic_reject_reason_online.txt").read_text(encoding="utf-8")
    g139 = (EVD / "g139_wants_results_local.txt").read_text(encoding="utf-8")

    def find(prefix: str) -> str:
        hits = [p["user_text"] for p in pool if html.unescape(p["user_text"]).startswith(prefix)]
        if len(hits) != 1:
            raise SystemExit(f"判读：池里 {prefix[:28]!r} 命中 {len(hits)} 条（要求恰好 1）⇒ 停手（RC=2）")
        return hits[0]

    rec = re.findall(r"产品判 topic=''｜模型原始='([^']*)'｜分类=", g141)
    if len(rec) != 2:
        raise SystemExit(f"判读：g141 记到的「模型原始返回」是 {len(rec)} 条，不是 2 条 ⇒ 停手（RC=2）")
    return {
        "zh_text": re.search(r"正对照原文：(.+)", g139).group(1).strip(),
        "zh_topic": re.search(r"C0 正对照：产品判 topic='([^']+)'", g141).group(1),
        "translated": (find("How should I deal with becoming"), rec[0]),
        "latin": (find("I don"), rec[1]),
    }


def main() -> int:
    stub = Stub()
    stub.install()
    import llm
    if llm.LLM_MOCK:
        raise SystemExit("判读：LLM_MOCK=1 ⇒ llm_topic 直接返回空串，本场读数全是幻影（RC=2）")

    cur = (REPO / "companion/intent/extractor.py").read_text(encoding="utf-8")
    got = subprocess.run(["git", "-C", str(REPO), "show", f"{FIX_COMMIT}^:companion/intent/extractor.py"],
                         capture_output=True)
    if got.returncode:
        raise SystemExit("判读：取不到修复前的 extractor.py ⇒ 停手（RC=2）")
    old_src = got.stdout.decode("utf-8")
    if old_src.count(OLD_GUARD_LINE) != 1 or cur.count(OLD_GUARD_LINE) != 0:
        raise SystemExit(f"判读：认路哨兵不过——旧版那行 {old_src.count(OLD_GUARD_LINE)} 次、现役 "
                         f"{cur.count(OLD_GUARD_LINE)} 次 ⇒ 我拿的不是「修复前 vs 修复后」（RC=2）")
    new = load(cur, "extractor_new")
    old = load(old_src, "extractor_old")
    cs = cases()

    print("—— K1 尺子：中文正对照（线上轮 139 真拿到过卡的那句）——")
    stub.ask(cs["zh_topic"])
    t_new = new["llm_topic"](cs["zh_text"])
    stub.ask(cs["zh_topic"])
    t_old = old["llm_topic"](cs["zh_text"])
    print(f"    旧版 topic={t_old!r}｜新版 topic={t_new!r}｜两版同判且非空="
          f"{bool(t_new) and t_new == t_old}")
    if not (t_new and t_new == t_old == cs["zh_topic"]):
        print("判读：正对照渲不出来 ⇒ 装载或桩坏了，整份不作数（RC=2）")
        return 2

    print("\n—— 逐案例：g141 当场记下的模型原始返回，喂进两版 llm_topic ——")
    rows = []
    for label, key in (("模型把英语译成中文（撞逐字校验）", "translated"),
                       ("模型给了纯拉丁 topic", "latin")):
        text, want = cs[key]
        stub.ask(want)
        o = old["llm_topic"](text)
        stub.ask(want)
        n = new["llm_topic"](text)
        rows.append((label, text, want, o, n))
        print(f"    {label}\n      原话：{text[:74]}\n      模型返回={want!r}｜旧版 topic={o!r}｜新版 topic={n!r}")

    flipped = [r for r in rows if not r[3] and r[4]]
    both_empty = [r for r in rows if not r[3] and not r[4]]
    print(f"\nK2 决策量：旧空→新非空 = {len(flipped)} 例｜K3 反向控制：两版都空 = {len(both_empty)} 例")
    if not both_empty:
        print("判读：无一例两版都空 ⇒ 看着像把闸门整条拆了，不是修短路（RC=2）")
        return 2

    print("\n—— 同一批候选串，两版 _topic_is_shell 各判一次（g142 那行印的是旧版口径）——")
    g142 = (EVD / "g142_topic_guard_replay_local.txt").read_text(encoding="utf-8")
    for cand in re.findall(r"^\s+'([^']+)' +壳词判定=True", g142, re.M):
        print(f"      {cand!r:<26} 旧版判壳={bool(old['_topic_is_shell'](cand))}"
              f"  新版判壳={bool(new['_topic_is_shell'](cand))}")

    print("\n—— K5 装载保真：exec 装的现役份 vs import 进来的产品模块 ——")
    from companion.intent import extractor as prod
    probe_strings = ["GNN", "CRISPR 脱靶检测", "IEEE 的方向", "mice killing", "方向", "钓鱼网站检测", ""]
    bad = [s for s in probe_strings if bool(prod._topic_is_shell(s)) != bool(new["_topic_is_shell"](s))]
    print(f"    比对 {len(probe_strings)} 串｜不一致={len(bad)}")
    if bad:
        print(f"判读：exec 装载与产品不一致（{bad}）⇒ 旧版读数不可信（RC=2）")
        return 2

    print("\n—— K6 下游：新版拿到 topic 之后，产品自己的 ready_for_search 放行吗 ——")
    from companion.intent.clarification import ready_for_search
    from companion.intent.model import ResearchIntent
    for label, text, want, o, n in rows:
        if not n:
            continue
        stub.ask(want)
        upd = new["extract_update"](text, ResearchIntent(project_id="probe-r148"))
        it = ResearchIntent(project_id="probe-r148")
        it.apply_update(upd)
        ok, conf = ready_for_search(it)
        print(f"    {label}｜extract_update={upd}｜confidence={conf}｜ready_for_search={ok}")
    print("    （ready_for_search 为假就是「访谈还在问」⇒ topic 有了 ≠ #25 有分母）")

    print(f"\nK4 零网络自证：httpx post 触发={stub.net}（必须 0）｜桩排队={stub.queued}"
          f"｜被消费={stub.consumed}｜剩余={len(stub.left)}"
          + ("｜🔴 植入：计数桩未接线（本场是负控制）" if stub.plant else "")
          + ("｜🔴 植入：主动发了一次网络请求（本场是负控制）" if stub.netplant else ""))
    if stub.net or stub.queued != stub.consumed or stub.left:
        print("判读：K4 不成立 ⇒ 桩与网络对不上账，本场读数不作数（RC=2）")
        return 2
    verdict = (f"轮 146 那句「纯英文 topic 永远进不了检索是代码级必然」**已被轮 147 的修复否证**："
               f"模型返回的 {flipped[0][2]!r} 这类纯拉丁 topic 现在出得来 llm_topic"
               if flipped else
               "轮 146 那句仍然成立：这批记录里没有一例纯拉丁 topic 能过闸门")
    print(f"\n判读：{verdict}；K1 尺子／K3 反向／K5 装载／K4 零网络四道自证全过（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
