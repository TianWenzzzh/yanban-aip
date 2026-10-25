"""parse_gate_yield_lines 的两档还原 + 与探针汇总对账 —— 离线测试（不烧模型、不联网）。

fixture 是**照着探针的打印格式手写**的（不是真跑出来的），所以这里断言的是两件事：
① 行格式改了之后正则还认得；② 还原出来的两档数与探针自印的「代价·」汇总**不一致时必须作废**。
真跑的那份（g98）仍然要现场对账 —— fixture 只能证明"这台仪器会响"，不能证明"量对了"。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pgl", str(HERE / "parse_gate_yield_lines.py"))
P = importlib.util.module_from_spec(spec)
sys.argv = ["parse_gate_yield_lines.py"]
spec.loader.exec_module(P)

# 6 条样本：4 条实质（其中 [1] 被 no_citation 拦下）、1 条过场（也开火）、1 条空池（不进任何档）
GOOD = """探针上限：1 轮 × 6 情景 = 6 条回复｜比对键集 10 道闸门
[1] 长度 480（前置  512）｜卡 8｜闸门前 no_quantity,no_citation｜闸门后 无｜接地 3/3｜计划=有
[2] 长度 455（前置  460）｜卡 8｜闸门前 无｜闸门后 无｜接地 2/2｜计划=有
[3] 长度 600（前置  620）｜卡 8｜闸门前 无｜闸门后 无｜接地 4/4｜计划=有
[4] 长度 500（前置  500）｜卡 8｜闸门前 无｜闸门后 无｜接地 1/1｜计划=有
[5] 长度  90（前置   60）｜卡 8｜闸门前 no_citation｜闸门后 无｜接地 0/0｜计划=有
[6] 长度 300（前置  300）｜卡 0｜闸门前 无｜闸门后 无｜计划=有
代价·实质回复（前置 ≥200 字）：4 条｜no_citation 开火 1/4 = 25%｜接地 10/10
代价·过场句（前置 <200 字）：1 条｜no_citation 开火 1/1 = 100%｜接地 0/0（带角标句 0 条 ⇒ 接地率无从算，这不是 0%）
结论：没有闸门让违规变多
"""
# 同一批行，但实质档汇总少算了一条（模拟"探针改了口径、还原器没跟"）
DRIFT = GOOD.replace("代价·实质回复（前置 ≥200 字）：4 条｜no_citation 开火 1/4 = 25%｜接地 10/10",
                     "代价·实质回复（前置 ≥200 字）：3 条｜no_citation 开火 1/3 = 33%｜接地 10/10")


def run(text: str) -> tuple[int, str]:
    tmp = pathlib.Path(tempfile.gettempdir()) / "parse_tier_fixture.txt"
    tmp.write_text(text, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = P.one(tmp)
    tmp.unlink(missing_ok=True)
    return rc, buf.getvalue()


def main() -> int:
    checks = []
    rc, out = run(GOOD)
    checks.append(("正例：两档都还原出来且与汇总对上",
                   rc == 0 and "还原·实质回复（前置 ≥200 字）：4 条｜开火 1｜接地 10/10" in out
                   and out.count("✅ 对上了") == 2,
                   [l.strip() for l in out.splitlines() if l.startswith("还原·")]))
    checks.append(("正例：空池那条不进任何档", "0 条 ⇒ 无读数" not in out, ["两档都非空 ⇒ 空池被排除了"]))
    rc2, out2 = run(DRIFT)
    bad_line = [l.strip() for l in out2.splitlines() if l.startswith("还原·")]
    checks.append(("反例：汇总少算一条 ⇒ 必须判作废且红在实质档",
                   rc2 == 1 and "❌ 对不上" in out2 and bad_line[0].startswith("还原·实质回复")
                   and "✅ 对上了" in bad_line[1],
                   bad_line))
    old = re.sub(r"（前置\s*\d+）", "", GOOD)     # 整批都是轮 117 之前的行格式
    rc3, out3 = run(old)
    checks.append(("旧格式（整批无前置列）只能降级不能崩",
                   rc3 == 0 and "无从还原" in out3 and "还原出 6 条" in out3
                   and "❌" not in out3,
                   [l.strip() for l in out3.splitlines() if "无从还原" in l]))
    # g98 的真形状：过场句档**真的是空**，探针印的是「这一档一条都没有」（不匹配汇总行正则）。
    # 不认这一行就会把"两边都说 0"当成"没比对过"⇒ 判分器拒绝判一份完好读数（本轮真犯过一次）。
    empty = GOOD.replace("[5] 长度  90（前置   60）｜卡 8｜闸门前 no_citation｜闸门后 无｜接地 0/0｜计划=有\n", "")
    empty = empty.replace(
        "代价·过场句（前置 <200 字）：1 条｜no_citation 开火 1/1 = 100%｜接地 0/0（带角标句 0 条 ⇒ 接地率无从算，这不是 0%）",
        "代价·过场句（前置 <200 字）：这一档一条都没有 ⇒ 无读数的维度，不判")
    rc4, out4 = run(empty)
    empty_line = [l.strip() for l in out4.splitlines() if l.startswith("还原·过场句")]
    checks.append(("空档：探针与还原器都说 0 ⇒ 算比对通过（不是「没比对」）",
                   rc4 == 0 and out4.count("✅ 对上了") == 2 and "两边都空" in out4,
                   empty_line))
    bad = 0
    for name, ok, evid in checks:
        bad += not ok
        print(f"{'✅' if ok else '❌'} {name}")
        for e in evid:
            print(f"   {e}")
    print(f"判读：{len(checks) - bad}/{len(checks)} 条过" + ("，这台还原器认得新行格式也会拒绝假一致。" if not bad else "，有断言不过。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
