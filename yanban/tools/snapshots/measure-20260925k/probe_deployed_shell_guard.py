"""线上复量：部署后的那份 `extractor.py` 是不是真按新口径判壳词？（零模型调用、零落库）

为什么要单独跑这一场：轮 147 改的是 `companion/**`，而「部署 RC=0 + drift 逐文件一致」只说明
字节搬上去了，不说明**跑起来的行为**。上一轮就是因为部署链不搬仓根模块，两份闸门互相印证了一件
没发生的事（轮 144 的 P0）。所以这里既比字节（`--expect-sha`），也直接调线上那份函数看判定。

判据（跑前写死，全过才 RC=0）：
  E 认路：`/app/companion/intent/extractor.py` 的 sha256 必须等于本机提交的那份（否则线上不是这一版）；
  V1/V2 修复目标：`GNN`、`CRISPR` 不再判壳；
  V3 边界：`_BOILER_LATIN` 里 11 个水词逐条仍判壳（放宽不能放宽到水词）；
  V4 用户可见：`capture_topic(「题目是 CRISPR 脱靶检测的深度学习模型」)` 非空；
  V5 旧期望：`方向` 仍判壳、`我刚入门，不知道做什么方向` 仍抽不出 topic；
  R 只读自证：`/app/data/yanban.db` 的 mtime+size 跑前跑后逐位相同。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

DB = pathlib.Path("/app/data/yanban.db")
SRC = pathlib.Path("/app/companion/intent/extractor.py")


def main() -> int:
    want = ""
    for i, a in enumerate(sys.argv):
        if a == "--expect-sha" and i + 1 < len(sys.argv):
            want = sys.argv[i + 1].strip()
    got = hashlib.sha256(SRC.read_bytes()).hexdigest()
    print(f"E 线上 extractor.py sha256={got[:16]}…｜本机提交份={want[:16] if want else '(未传)'}…｜一致={bool(want) and got == want}")
    if not want or got != want:
        print("判读：线上跑的不是本机这份字节 ⇒ 后面的判定是给旧版做的，不作数（RC=2）")
        return 2

    from companion.intent import extractor

    # mtime 要在**任何产品函数被调用之前**取，否则"前后相同"不能证明这场没写库。
    st0 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    bad: list[str] = []
    checks = [
        ("V1 _topic_is_shell('GNN') 应为 False", extractor._topic_is_shell("GNN") is False),
        ("V2 _topic_is_shell('CRISPR') 应为 False", extractor._topic_is_shell("CRISPR") is False),
        ("V5a _topic_is_shell('方向') 应为 True", extractor._topic_is_shell("方向") is True),
        ("V5b 无主题句仍抽不出 topic",
         extractor.capture_topic("我刚入门，不知道做什么方向") == ""),
        ("V4 「题目是 CRISPR 脱靶检测的深度学习模型」应有 topic",
         bool(extractor.capture_topic("题目是 CRISPR 脱靶检测的深度学习模型"))),
    ]
    boiler = sorted(extractor._BOILER_LATIN)
    offenders = [w for w in boiler if not extractor._topic_is_shell(w)]
    checks.append((f"V3 水词表 {len(boiler)} 个词逐条仍判壳", not offenders))

    topic = extractor.capture_topic("题目是 CRISPR 脱靶检测的深度学习模型")
    for name, ok in checks:
        print(f"    {'✅' if ok else '❌'} {name}")
        if not ok:
            bad.append(name)
    if offenders:
        print(f"    V3 漏放水词：{offenders}")
    print(f"    V4 实际抽出 topic={topic!r}")

    st1 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    print(f"R 线上库只读自证：mtime/size 未变={st0 == st1}")
    if st0 is not None and st0 != st1:
        print("判读：名义只读的探针碰了线上库 ⇒ 读数作废（RC=2）")
        return 2
    if bad:
        print(f"判读：线上行为与本机不一致，{len(bad)} 条不过：{bad}（RC=2）")
        return 2
    print("判读：**线上已按新口径判**——拉丁术语不再当壳词、水词照旧拦、无主题句照旧不硬查（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
