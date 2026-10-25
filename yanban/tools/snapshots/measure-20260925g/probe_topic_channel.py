"""线上真路径下，主题抽取这条兜底通道到底活着没有？（容器内跑，零落库，模型调用硬上限 4 次）

为什么要量：轮 145 在 mock 口径下量到外部提问池 0/206，而轮 144 的 D1 在线上读到 140/140 条
intent 抽取后 topic 为空串。同一件「topic 空」有两种互斥解释，处置完全相反：
  · 线上这条通道本身就是坏的（没配 key / LLM_MOCK=1 / 请求失败被 `except` 吞）⇒ 产品对所有输入
    都只剩中文规则通道，那是 P0 级的线上问题，与提问池无关；
  · 通道通、但这批英语提问拿不出合规 topic ⇒ #25 的外部单轮池在**真路径**下同样是 0 分母，该换料/换测量层。
本机分不开这两条（本机 `GLM_API_KEY` 为空，v1 连试 206 次全失败），所以只能去容器里量。

判据（跑前写死，互斥）：
  C1 环境自检：`llm.LLM_MOCK` / `bool(llm.GLM_API_KEY)`（**只打印布尔，绝不打印 key**）/ `GLM_MODEL` / base url；
     并用 `llm.REAL_CALLS`（产品自己维护的真实调用计数）前后差算出本场真调用数，>MAX_CALLS 即作废。
  C2 通道活性：`llm_topic(正对照中文句)` 非空 ⇒ alive。
  C3 待测：池子里取 3 条英语提问各调一次 `llm_topic`，数非空条数。
  C4 只读自证：`/app/data/yanban.db` 的 mtime 与 size 跑前跑后必须逐位相同。
  判读四选一：V1 = 不活且捕获到异常（请求层坏了）｜V2 = 不活但无异常（模型返回被校验判掉）｜
  V3 = 活且英语 0/3（通道没问题，是这批料）｜V4 = 活且英语 ≥1/3（那 D1 的空 topic 得另找原因）。
退出码：0=V3/V4（读数有效）｜3=V1/V2（线上通道自己有问题）｜2=自检不过（超限或环境拿不到）。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

MAX_CALLS = 4          # 1 次正对照 + 3 次英语待测；超了当场作废
POOL_ARG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/g132_external_questions_pool.json"
DB = pathlib.Path("/app/data/yanban.db")
SECRETISH = re.compile(r"[A-Za-z0-9_\-]{28,}")

POS_ZH = "我想研究钓鱼网站检测，第一次做科研，目标发一篇SCI，看近两年的"


def clean(s: str) -> str:
    """异常文本里若混进长 token 形状的东西一律替换（宁可少一点信息，也不把凭据带进日志）。"""
    return SECRETISH.sub("[已省略疑似密钥片段]", s)[:200]


def main() -> int:
    import llm
    from companion.intent import extractor

    host = re.sub(r"^https?://", "", (llm.GLM_BASE_URL or "").split("/")[0]) if llm.GLM_BASE_URL else ""
    print(f"C1 环境：LLM_MOCK={llm.LLM_MOCK}｜GLM_API_KEY 非空={bool(llm.GLM_API_KEY)}｜"
          f"GLM_MODEL={llm.GLM_MODEL}｜base={host or '(未配)'}")
    if llm.LLM_MOCK:
        print("判读：容器里 LLM_MOCK=1 ⇒ 这条通道在生产路径上被整体关掉，本身就是要记的事，"
              "但本场量不出英语能不能抽出 topic（RC=3）")
        return 3

    errs: list[str] = []
    real_r = llm.run_async
    calls = {"n": 0}

    def wrapped(coro):
        calls["n"] += 1
        if calls["n"] > MAX_CALLS:
            coro.close()
            raise RuntimeError(f"超出模型调用上限 {MAX_CALLS}")
        try:
            return real_r(coro)
        except Exception as exc:  # noqa: BLE001 — 记录成因后原样抛，让产品走它自己的降级分支
            errs.append(f"{type(exc).__name__}: {clean(str(exc))}")
            raise

    llm.run_async = wrapped
    before_calls = llm.REAL_CALLS
    st0 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None

    alive_topic = extractor.llm_topic(POS_ZH)
    print(f"C2 正对照（中文，线上轮 139 真拿到过卡的那句）⇒ topic={alive_topic!r}"
          f"｜通道活={bool(alive_topic)}")

    rows = json.loads(pathlib.Path(POOL_ARG).read_text(encoding="utf-8"))
    en = [r.get("user_text", "").strip() for r in rows if r.get("user_text", "").strip()][:3]
    got = []
    for t in en:
        topic = extractor.llm_topic(t)
        got.append(bool(topic))
        print(f"C3 英语样本 topic={topic!r}｜{t[:56]}")
    used = llm.REAL_CALLS - before_calls
    print(f"C1b 本场产品自己数到的真实调用数 REAL_CALLS 增量={used}（上限 {MAX_CALLS}）｜"
          f"包装层看到 {calls['n']} 次 run_async｜异常 {len(errs)} 条")
    for e in errs[:4]:
        print(f"    · {e}")

    st1 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    print(f"C4 线上库只读自证：mtime/size 跑前={st0} 跑后={st1} 未变={st0 == st1}")
    if used > MAX_CALLS:
        print("判读：模型调用数超限 ⇒ 本场作废（RC=2）")
        return 2
    if st0 is not None and st0 != st1:
        print("判读：名义只读的探针碰了线上库 ⇒ 读数作废（RC=2）")
        return 2

    n_en = sum(got)
    if not alive_topic:
        if errs:
            print(f"判读：**V1** 线上这条通道请求层就是坏的（捕获到 {len(errs)} 次异常，被产品的 "
                  "`except` 吞成空串）⇒ 与提问池无关，属线上问题（RC=3）")
            return 3
        print("判读：**V2** 通道有响应但连正对照都没产出合规 topic（校验判掉或模型给空）⇒ "
              "线上主题抽取实际不可用（RC=3）")
        return 3
    if n_en == 0:
        print("判读：**V3** 通道活着、但这批英语提问 0/3 抽出 topic ⇒ #25 的外部单轮池在真路径下也是 0 分母，"
              "该换料/换测量层，不是再跑一场（RC=0）")
        return 0
    print(f"判读：**V4** 通道活着且英语 {n_en}/3 抽出 topic ⇒ 轮 144 D1 的 140/140 空 topic 得另找原因"
          "（可能在 prepare_context 的上游），不能沿用池子解释（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
