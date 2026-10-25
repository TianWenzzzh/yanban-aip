"""线上复量：部署后的那份 `extractor.py` 真按轮 152 的口径抽 topic 吗？（零模型调用、零落库）

为什么必须跑这一场：轮 144 的教训是「部署 RC=0 + 逐文件一致」只证明字节搬上去了，不证明**跑起来的行为**——
当时两份闸门互相印证了一件根本没发生的事。所以这里既比字节，又把**仓里那份句集**喂进线上那份函数。

判据（跑前写死，全过才 RC=0）：
  E 认路：`/app/companion/intent/extractor.py` 的 sha256 == 本机提交的份（否则后面是给旧版做的读数）；
  F 同源：`/tmp/fixtures_topic_corpus.py` 的 sha256 == 仓里那份（句集只许一份，线上不许另抄一套）；
  Z 只读自证：`llm.chat_json/chat` 装雷，模型调用计数必须为 0（`--plant-trip` 那场必须红在这里）；
  V1 覆盖：非天花板正例**全部**抽出含关键词的 topic，条数派生自夹具（`len(POSITIVE)-len(LLM_ONLY)`），不钉字面量；
  V2 纯度：负例全部抽出空串（含轮 152 新增的那 4 条残渣形状）；
  V3 形状：抽出的 topic 无一含连接词壳或句读（夹具自己的 `shape_ok`）；
  V4 天花板：申报给 `llm_topic` 的那几条在线上规则通道里确实为空（非空 ⇒ 版本或句集不同）；
  V5 旧期望：轮 147 的口径原样还在——`GNN`/`CRISPR` 不判壳、`方向` 判壳、
      `我刚入门，不知道做什么方向` 抽不出 topic；
  R 只读自证：`/app/data/yanban.db` 的 mtime+size 在**任何产品函数被调用之前**取，跑完再取，逐位相同。

用法（本机）：MSYS_NO_PATHCONV=1 PYTHONUTF8=1 uv run --no-project --python 3.12 --with paramiko \
  python 输出/scripts/run_cloud_probe.py <本机绝对路径>/probe_deployed_topic_capture.py \
  <本机绝对路径>/../code/yanban/tests2/fixtures_topic_corpus.py --expect-sha … --expect-fixture-sha …
植入开关走 argv（轮 150 实测：本地环境变量不会跟进 docker exec，那次是一次假绿）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

DB = pathlib.Path("/app/data/yanban.db")
SRC = pathlib.Path("/app/companion/intent/extractor.py")
FIX = pathlib.Path("/tmp/fixtures_topic_corpus.py")


def arg(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1].strip() if name in sys.argv else ""


def main() -> int:
    bad: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        print(f"    {'✅' if ok else '❌'} {name}" + (f"｜{detail}" if detail else ""))
        if not ok:
            bad.append(name)
        return ok

    got = hashlib.sha256(SRC.read_bytes()).hexdigest()
    want = arg("--expect-sha")
    check("E 线上 extractor.py 与本机提交份一致", bool(want) and got == want, f"sha={got[:16]}…")
    if not want or got != want:
        print("判读：线上跑的不是本机这份字节 ⇒ 后面的判定不作数（RC=2）")
        return 2

    fx_sha = hashlib.sha256(FIX.read_bytes()).hexdigest()
    want_fx = arg("--expect-fixture-sha")
    check("F 句集与仓内那份同源（未另抄）", bool(want_fx) and fx_sha == want_fx, f"sha={fx_sha[:16]}…")
    if not want_fx or fx_sha != want_fx:
        print("判读：句集不是仓里那份 ⇒ 读数是另一套句子的（RC=2）")
        return 2

    sys.path.insert(0, "/tmp")
    import fixtures_topic_corpus as fx          # noqa: E402
    import llm                                   # noqa: E402
    from companion.intent import extractor       # noqa: E402

    calls = {"n": 0}

    def trip(name):
        def boom(*a, **kw):
            calls["n"] += 1
            raise AssertionError(f"Z 破：调了 llm.{name}")
        return boom

    for fn in ("chat_json", "chat", "chat_text"):
        if hasattr(llm, fn):
            setattr(llm, fn, trip(fn))
    llm.LLM_MOCK = True
    if "--plant-trip" in sys.argv:
        print("    （植入）故意发一次模型调用，期望红在 Z")
        try:
            llm.chat_json("plant")
        except AssertionError as e:
            print(f"    雷开了火：{e}")

    st0 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None

    rule_pos = [(t, w) for t, w in fx.POSITIVE if t not in fx.LLM_ONLY]
    hits = [(t, w, extractor.capture_topic(t)) for t, w in rule_pos]
    got_ok = [(t, g) for t, w, g in hits if w in g and fx.shape_ok(g)]
    check(f"V1 覆盖：非天花板正例全抽出含关键词的形状合格 topic",
          len(got_ok) == len(rule_pos), f"{len(got_ok)}/{len(rule_pos)}")
    for t, w, g in hits:
        if (t, g) not in got_ok:
            print(f"      ❌ 漏：期望含{w!r} 实抽={g!r} ｜ {t[:30]}")

    caps = [(t, extractor.capture_topic(t)) for t in fx.NEGATIVE]
    wrongly = [(t, g) for t, g in caps if g]
    check("V2 纯度：负例全部为空", not wrongly, f"误捕 {len(wrongly)}/{len(fx.NEGATIVE)}")
    for t, g in wrongly:
        print(f"      ❌ 误捕 {g!r} ｜ {t[:30]}")

    shelled = [(t, g) for t, _, g in hits if g and not fx.shape_ok(g)]
    check("V3 形状：抽出的 topic 不带壳/句读", not shelled, f"带壳 {len(shelled)} 条")
    for t, g in shelled:
        print(f"      ❌ 带壳 {g!r} ｜ {t[:30]}")

    ceil = [(t, extractor.capture_topic(t)) for t in fx.LLM_ONLY]
    nonempty = [(t, g) for t, g in ceil if g]
    check(f"V4 天花板：申报的 {len(ceil)} 条在规则通道仍为空", not nonempty,
          f"非空 {len(nonempty)} 条")
    for t, g in nonempty:
        print(f"      ❌ 已申报却仍被规则通道抽走：{g!r} ｜ {t[:30]}")

    boiler_ok = extractor._topic_is_shell("GNN") is False and extractor._topic_is_shell("CRISPR") is False
    check("V5a 轮 147 口径：拉丁术语不判壳", boiler_ok)
    check("V5b 轮 147 口径：『方向』仍判壳", extractor._topic_is_shell("方向") is True)
    check("V5c 轮 147 口径：无主题句仍抽不出 topic",
          extractor.capture_topic("我刚入门，不知道做什么方向") == "")

    st1 = (DB.stat().st_mtime_ns, DB.stat().st_size) if DB.exists() else None
    check("R 线上库只读自证：mtime+size 前后逐位相同", st0 is not None and st0 == st1,
          f"{'取到' if st0 else '库不存在'}")
    check("Z 零模型调用：雷计数为 0", calls["n"] == 0, f"触发 {calls['n']} 次")

    if st0 is not None and st0 != st1:
        print("判读：名义只读的探针碰了线上库 ⇒ 读数作废（RC=2）")
        return 2
    if calls["n"]:
        print("判读：Z 破 ⇒ 这场不是零模型调用（RC=2）")
        return 2
    if bad:
        print(f"判读：线上行为与本机口径不符，{len(bad)} 条不过：{bad}（RC=2）")
        return 2
    print(f"判读：线上那份 capture_topic 与本机同一份句集上口径一致"
          f"（覆盖 {len(got_ok)}/{len(rule_pos)}、误捕 0/{len(fx.NEGATIVE)}、带壳 0、"
          f"天花板 {len(ceil)} 条仍空、零模型调用）⇒ 修复真上线了（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
