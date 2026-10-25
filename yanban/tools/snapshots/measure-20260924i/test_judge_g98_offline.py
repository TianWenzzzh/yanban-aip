"""g98 判分器（`parse_gate_yield_lines.judge`）的离线测试 —— 每条分支都要有"它必须这样判"的用例。

为什么单独一台：判据 Q0—Q6 是轮 117 **跑前**写死的，跑完由谁执行都应当是同一套口径。
交给我手算的风险已经发生过两次（部分样本手算 ⇒ 结论写歪）。所以判分逻辑落成代码，
并且判分器自己也得是可证伪的仪器：作废、未测、收窄、回退、先修，每条都得被真的触发一次。
其中"没比对过却印成比对通过"是这次新加的分支 —— 它只能连 `main()` 一起测，
因为那个 `recon` 字符串是 `main()` 算出来的。
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


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(HERE / (name + ".py")))
    m = importlib.util.module_from_spec(spec)
    sys.argv = [name + ".py"]
    spec.loader.exec_module(m)
    return m


P = load("parse_gate_yield_lines")        # 判分器
T = load("test_parse_tier_offline")       # 借它的 GOOD 夹具：一份真能两档对账的小证据

HEAD = """用例池：31 行 → 去重后 19 个提问（追问 0 条）
探针上限：19 情景 × 4 轮 × 各 1.0 轮对话 = 76 条回复｜比对键集 10 道闸门
"""
TAIL = """样本：76 次回复，150 次模型调用（平均 1.97 次/回复）
结论：没有闸门让违规变多
"""
REAL_OK = ("代价·实质回复（前置 ≥200 字）：60 条｜no_citation 开火 12/60 = 20%｜接地 100/120\n"
           "  开火那 12 条才是代价所在：长度中位 812 → 790 字（-3%，线是下降不超 15%）｜这批接地 20/24\n")
TRANS_OK = ("代价·过场句（前置 <200 字）：16 条｜no_citation 开火 3/16 = 19%｜接地 5/9\n"
            "  开火那 3 条才是代价所在：长度中位 120 → 118 字（-2%，线是下降不超 15%）｜这批接地 1/2\n")
# Q3 的那种坏：过场句本来无话可引，闸门却在这档一半以上开火，且整档零角标句
TRANS_MIS = ("代价·过场句（前置 <200 字）：16 条｜no_citation 开火 10/16 = 62%"
             "｜接地 0/0（带角标句 0 条 ⇒ 接地率无从算，这不是 0%）\n"
             "  开火那 10 条才是代价所在：长度中位 120 → 400 字（+233%，线是下降不超 15%）｜这批接地 0/0\n")
REAL_COST = ("代价·实质回复（前置 ≥200 字）：60 条｜no_citation 开火 12/60 = 20%｜接地 100/120\n"
             "  开火那 12 条才是代价所在：长度中位 812 → 600 字（-26%，线是下降不超 15%）｜这批接地 20/24\n")
SMALL = ("代价·实质回复（前置 ≥200 字）：4 条｜no_citation 开火 2/4 = 50%｜接地 3/6\n"
         "  开火那 2 条才是代价所在：长度中位 812 → 790 字（-3%，线是下降不超 15%）｜这批接地 1/2\n"
         "代价·过场句（前置 <200 字）：4 条｜no_citation 开火 1/4 = 25%"
         "｜接地 0/0（带角标句 0 条 ⇒ 接地率无从算，这不是 0%）\n"
         "  开火那 1 条才是代价所在：长度中位 120 → 130 字（+8%，线是下降不超 15%）｜这批接地 0/0\n")
FULL = HEAD + REAL_OK + TRANS_OK + TAIL


def _write(text: str) -> pathlib.Path:
    tmp = pathlib.Path(tempfile.gettempdir()) / "judge_g98_fixture.txt"
    tmp.write_text(text, encoding="utf-8")
    return tmp


def run(text: str, recon: str = "ok") -> tuple[int, str]:
    tmp = _write(text)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = P.judge(tmp, recon)
    tmp.unlink(missing_ok=True)
    return rc, buf.getvalue()


def run_cli(text: str) -> tuple[int, str]:
    """走 `main()`：`recon` 这个字符串是它算的，所以"会不会把没比对说成比对通过"只能这样测。"""
    tmp = _write(text)
    buf = io.StringIO()
    argv = sys.argv
    sys.argv = ["parse_gate_yield_lines.py", "--judge", str(tmp)]
    try:
        with contextlib.redirect_stdout(buf):
            rc = P.main()
    finally:
        sys.argv = argv
        tmp.unlink(missing_ok=True)
    return rc, buf.getvalue()


def tail(out: str) -> list[str]:
    return [out.splitlines()[-1]]


def main() -> int:
    checks = []
    rc, out = run(FULL)
    checks.append(("正例：两档分母都够、代价在线内 ⇒ 留下",
                   rc == 0 and "判读：留下" in out, tail(out)))
    rc, out = run(HEAD + REAL_OK + TRANS_MIS + TAIL)
    checks.append(("Q3：过场句档高开火且整档零角标 ⇒ 收窄",
                   "判读：收窄到 ≥200 字（Q3）" in out,
                   [l for l in out.splitlines() if l.startswith("Q1/Q3")]))
    rc, out = run(HEAD + REAL_COST + TRANS_OK + TAIL)
    checks.append(("Q2：实质档缩了 26% ⇒ 回退",
                   "判读：回退（Q2 代价超线）" in out,
                   [l for l in out.splitlines() if l.startswith("Q1/Q2")]))
    rc, out = run(HEAD + SMALL + TAIL)
    checks.append(("两档分母都 <8 ⇒ 判不了，且不许写成通过",
                   rc == 0 and "判读：Q2/Q3 至少一档停在「未测」或「只作个案」" in out and "判读：留下" not in out,
                   [l for l in out.splitlines() if "未测" in l]))
    rc, out = run(HEAD + REAL_OK + TRANS_OK
                  + "  fake_title      1/76 =   1%  →  3/76 =   4%　✗ 变差\n" + TAIL)
    checks.append(("Q4：任何一行「✗ 变差」压过其它结论",
                   "判读：先修闸门（Q4）" in out,
                   [l for l in out.splitlines() if l.startswith("Q4")]))
    rc, out = run(FULL, recon="mismatch")
    checks.append(("Q5：比对过但对不上 ⇒ 整份作废（RC=2，不去读 Q1—Q3）",
                   rc == 2 and "作废" in out and "Q1/Q2" not in out, tail(out)))
    rc, out = run(FULL, recon="not-comparable")
    checks.append(("Q5：压根没比对 ⇒ 只能说「看不见」，不许印成「对上了」",
                   rc == 2 and "Q5 对账 ⚠️" in out and "Q5 对账 ✅" not in out, tail(out)))
    rc, out = run_cli(T.GOOD)
    checks.append(("main 算对账：两档还原一致 ⇒ Q5 ✅ 并继续判（然后被 Q0 拦下）",
                   "Q5 对账 ✅" in out and "Q0 输入自证 ❌" in out,
                   [l for l in out.splitlines() if l.startswith(("Q5", "Q0"))]))
    rc, out = run_cli(re.sub(r"（前置\s*\d+）", "", T.GOOD))
    checks.append(("main 算对账：旧格式没比过 ⇒ 停在 Q5，不假绿（RC=2）",
                   rc == 2 and "Q5 对账 ⚠️" in out and "Q0 输入自证" not in out, tail(out)))
    rc, out = run(HEAD.replace("去重后 19 个提问", "去重后 18 个提问") + REAL_OK + TRANS_OK + TAIL)
    checks.append(("Q0：提问数与预登记不符 ⇒ 不是这一跑，不判（RC=2）",
                   rc == 2 and "不是预登记的 g98" in out, tail(out)))
    rc, out = run(HEAD + REAL_OK + TRANS_OK)
    checks.append(("缺收尾行 ⇒ Q6 判不了（已有结论不被它顶掉）",
                   "Q6 成本 ❌" in out and "判读：留下" in out,
                   [l for l in out.splitlines() if l.startswith("Q6")]))
    # g98 的真形状（这一条就是本轮我自己踩出来的那个拒判）：过场句档**真的是空**，
    # 探针印「这一档一条都没有」；还原器若不认这一行，main() 就会算成「没比对过」⇒ 一份完好读数被判"不判"。
    rows = "".join(f"[{i}] 长度 500（前置  520）｜卡 8｜闸门前 "
                   f"{'no_citation' if i <= 3 else '无'}｜闸门后 无｜接地 1/1｜计划=有\n"
                   for i in range(1, 11))
    g98_shape = (HEAD + rows
                 + "代价·实质回复（前置 ≥200 字）：10 条｜no_citation 开火 3/10 = 30%｜接地 10/10\n"
                 + "  开火那 3 条才是代价所在：长度中位 520 → 500 字（-4%，线是下降不超 15%）｜这批接地 3/3\n"
                 + "代价·过场句（前置 <200 字）：这一档一条都没有 ⇒ 无读数的维度，不判\n" + TAIL)
    rc, out = run_cli(g98_shape)
    checks.append(("空档不再被当成「没比对」：Q5 ✅、开火 3<8 记「只作个案」、0 条那档报上界",
                   "Q5 对账 ✅" in out and "只作个案描述" in out and "这一档 **0 条**" in out
                   and "Q5 对账 ⚠️" not in out,
                   [l for l in out.splitlines() if l.startswith(("Q5", "Q1/Q2", "Q1/Q3")) or l.startswith("判读")]))
    # 两把尺的口径差：闸门认 `[\d]`/【\d】/《》（mentor.py:802），计数尺只数"≥8 字句子里的 [n] 或《》"。
    # 所以"计数尺读到 0 句"的回复，闸门可以按自己的宽口径认它已经挂了来源 ⇒ 不开火。
    diverge = "[1] 长度 210（前置  150）｜卡 8｜闸门前 无｜闸门后 无｜接地 0/0｜计划=有\n"
    rc, out = run(HEAD + REAL_OK + TRANS_MIS + diverge + TAIL)
    checks.append(("Q3 前提核对：闸门与计数尺口径不一致 ⇒ 不许据「带角标句 0 条」判收窄",
                   "两把尺口径不一致" in out and "Q3 **不判**" in out and "判读：收窄" not in out,
                   [l for l in out.splitlines() if l.startswith("口径核对") or "Q3" in l][-2:]))
    rc, out = run(HEAD + REAL_OK + TRANS_MIS
                  + "[2] 长度 210（前置  150）｜卡 8｜闸门前 no_citation｜闸门后 无｜接地 0/0｜计划=有\n"
                  + TAIL)
    checks.append(("同一批行但闸门确实开过火 ⇒ 口径一致，收窄照常判出（上一条的反证）",
                   "两把尺一致" in out and "判读：收窄到 ≥200 字（Q3）" in out,
                   [l for l in out.splitlines() if l.startswith("口径核对")]))
    bad = 0
    for name, ok, evid in checks:
        bad += not ok
        print(f"{'✅' if ok else '❌'} {name}")
        for e in evid:
            print(f"   {e}")
    print(f"判读：{len(checks) - bad}/{len(checks)} 条过"
          + ("，这台判分器每条分支都会红在该红的那条上。" if not bad else "，判分器自身不可信 ⇒ g98 不许由它出结论。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
