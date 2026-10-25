"""轮 113 的供给体检：收紧「可抄词」门槛后，反证闸门还有料可递吗？

轮 112 末尾修了一个机制。旧 `_limitation_hits` 会挑出「整句只与池子里套话重合」的否定句，
模型照抄之后 `survey` 仍然核不回原文 —— `g88` 那次「编号挂上=True、支持 0 相反 0」就是这个
形状。修法是要求候选句里至少留 `evidence.MIN_TOKENS` 个稀有词（`rare_tokens`）。

但同一把尺也在砍料。砍到零是**另一个方向的失败**：闸门不再注入，反证率原地不动，
而测试仍然是绿的。所以本探针把同一批真实池子同时喂给新旧两份 report.py，只问三件事：

① 每池可递料从几条掉到几条（含产品实际递出的 limit=2 口径）；
② 掉掉的料里有多少本来 `survey` 就核不回（砍对了）——以及**留下的料里还有没有核不回的**
   （有的话说明门槛与尺子没对齐，这次修得不完整）；
③ 提示词要求「逐字照抄 4—6 个连续英文词」，而门槛数的是**整句**的稀有词个数。
   连续窗口里凑不满 `MIN_TOKENS` 个稀有词的料，模型照抄了也核不回来 ——
   这一维是这次修法没覆盖的残余缺口，探针把它单独印出来。

判据（跑之前定死，不许看完数再改）：
- 任一池「旧 limit=2 ≥1 而新 =0」→ **断供**，RC=4（要放宽门槛或换料源，不能就这么部署）；
- 新料里存在 `survey(...)["support"] == 0` 的条 → 门槛与尺子没对齐，RC=5；
- 两份 report.py 字节相同 → 仪器不工作，RC=2（否则一定印出「零差异」的假绿）；
- 池子不足 3 篇时 `rare_tokens` 返回 None，新旧同码，该池记「判不了」，不进任何分子分母。

只读探针：不写库、不改产品、不调 GLM（三个主题都是英文，`search_for_intent` 走不到翻译分支）。
容器内跑：python /tmp/probe_counterev_supply.py
本地跑：R_OLD=... R_NEW=... python probe_counterev_supply.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from companion.intent.model import ResearchIntent            # noqa: E402
from companion.literature import engine, evidence            # noqa: E402

TOPICS = ["multi-agent collaboration", "medical image segmentation",
          "photovoltaic power forecasting"]     # 与 g72（probe_counter_supply）同三个主题

OLD_PATH = os.environ.get("R_OLD", "/tmp/report_old.py")
NEW_PATH = os.environ.get("R_NEW", "/tmp/report_new.py")
WIN = (4, 6)      # 提示词里要求照抄的连续词数


def load(path: str, name: str):
    if not os.path.isfile(path):
        print(f"❌ 读不到 {path}（新旧两份 report.py 都要在，缺一份就没有对照）")
        sys.exit(2)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ordered_tokens(text: str) -> list[str]:
    """与 `evidence._tokens` 的英文切词同一条正则，只是保留词序（料是英文原文，用不上中文三元组）。"""
    return [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text or "")]


def best_window(text: str, rare: set[str] | None) -> tuple[int, str]:
    """照抄一个 4—6 词的连续窗口，最多能带几个**不重复**的稀有词。

    `survey` 的判据是 `len(_tokens(sent) & want) >= MIN_TOKENS`，而 want 来自模型抄进中文句里
    的那段原文 —— 所以真正的地板是「连续窗口内的稀有词个数」，不是整句个数。
    """
    if rare is None:
        return (-1, "")
    toks = ordered_tokens(text)
    best_n, best_s = 0, ""
    for w in range(WIN[0], WIN[1] + 1):
        for i in range(len(toks) - w + 1):
            span = toks[i:i + w]
            n = len({t for t in span if t in rare})
            if n > best_n:
                best_n, best_s = n, " ".join(span)
    return best_n, best_s


def material_stats(material: list[dict], papers: list[dict]) -> list[dict]:
    rare = evidence.rare_tokens(papers)
    rows = []
    for m in material:
        sv = evidence.survey(m["text"], papers)
        wn, w = best_window(m["text"], rare)
        rows.append({"idx": m["idx"], "support": sv["support"], "against": sv["against"],
                     "sentence_rare": len(evidence._tokens(m["text"]) & rare) if rare else None,
                     "window_rare": wn, "window": w, "text": m["text"]})
    return rows


def one_pool(old, new, topic: str) -> dict:
    papers = engine.search_for_intent(ResearchIntent(project_id="supply", topic=topic), limit=8)
    src = {}
    for p in papers:
        src[str(p.get("source") or "?")] = src.get(str(p.get("source") or "?"), 0) + 1
    print(f"\n=== {topic}｜池子 {len(papers)} 篇｜带摘要 "
          f"{sum(1 for p in papers if len((p.get('abstract') or '').strip()) >= 80)} 篇"
          f"｜来源 {src}")
    rare = evidence.rare_tokens(papers)
    if rare is None:
        print("  判不了：池子不足 3 篇或无文本，`rare_tokens` 返回 None → 新旧同码，"
              "不进分子分母（这不是「没有料」）")
        return {"judgeable": False}
    only = set(range(1, len(papers) + 1))
    out = {"judgeable": True, "topic": topic}
    for tag, mod in (("旧", old), ("新", new)):
        for kind, lim in (("全量", 99), ("产品", 2)):
            ms = material_stats(mod._limitation_hits(papers, only, limit=lim), papers)
            out[f"{tag}_{kind}"] = ms
            dead = sum(1 for r in ms if r["support"] == 0)
            thin = sum(1 for r in ms if r["window_rare"] < evidence.MIN_TOKENS)
            print(f"  {tag}口径·{kind}：料 {len(ms)} 条｜survey 核不回 {dead} 条"
                  f"｜连续窗口凑不满 {evidence.MIN_TOKENS} 个稀有词 {thin} 条")
            for r in ms:
                print(f"      [{r['idx']}] 支持 {r['support']} 相反 {r['against']}"
                      f"｜整句稀有 {r['sentence_rare']}｜窗口稀有 {r['window_rare']}"
                      f"｜抄「{r['window'][:40]}」｜{r['text'][:70]}")
    for kind in ("全量", "产品"):
        kept = {r["idx"] for r in out[f"新_{kind}"]}
        cut = [r for r in out[f"旧_{kind}"] if r["idx"] not in kept]
        print(f"  断供检查（{kind}）：旧 {len(out[f'旧_{kind}'])} 条 → 新 {len(out[f'新_{kind}'])} 条"
              f"｜被砍 {len(cut)} 条，其中「本来就核不回」{sum(1 for r in cut if r['support'] == 0)} 条"
              f"、「窗口本来抄得动」{sum(1 for r in cut if r['window_rare'] >= evidence.MIN_TOKENS)} 条")
    def as_material(rows):
        return [{"idx": r["idx"], "title": "", "source": "", "text": r["text"]} for r in rows]

    for tag, txt in (("旧", old._counterev_facts(as_material(out["旧_产品"]))),
                     ("新", new._counterev_facts(as_material(out["新_产品"]), papers))):
        tail = txt.split("单句可用的词有：", 1)
        words = tail[1].split("）", 1)[0] if len(tail) > 1 else ""
        n = len([w for w in words.split(",") if w.strip()])
        print(f"  可抄词（{tag}）：{n} 个｜{words[:90]}")
    return out


def selfcheck(old, new) -> int:
    """合成池，证明这台仪器两端都分得开：套话否定句（旧收新砍）与稀有词否定句（两边都收）。"""
    boiler = ("However, the experimental results show limitations of the proposed framework. "
              "In this paper we present a deep learning method to improve performance on the "
              "benchmark dataset and the reported gains are consistent.")
    pool_a = [{"title": f"Study on framework {i}", "abstract": boiler} for i in range(3)]
    pool_a.append({"title": "Study on framework 4",
                   "abstract": "We compare baselines on the same benchmark and report the mean "
                               "accuracy across five folds."})
    uniq = ("However, the quartz telescope cannot resolve the faint aurora during daylight, "
            "so the campaign relies on the magnetometer array instead.")
    pool_b = [{"title": f"Study on framework {i}", "abstract": boiler} for i in range(2)]
    pool_b += [{"title": "Optical campaign of polar aurora", "abstract": uniq},
               {"title": "Quartz calibration for telescope arrays",
                "abstract": "The magnetometer array is calibrated against a quartz reference."}]
    only_all = set(range(1, 5))
    checks = []

    ma_old = old._limitation_hits(pool_a, only_all, limit=99)
    ma_new = new._limitation_hits(pool_a, only_all, limit=99)
    sa = material_stats(ma_old, pool_a)
    checks.append(("套话否定句：旧收得到", len(ma_old) >= 1, len(ma_old)))
    checks.append(("套话否定句：新砍掉（因为抄不动）", len(ma_new) == 0, len(ma_new)))
    checks.append(("被砍的料本来就核不回（support=0）",
                   bool(sa) and all(r["support"] == 0 for r in sa),
                   [r["support"] for r in sa]))

    mb_old = old._limitation_hits(pool_b, only_all, limit=99)
    mb_new = new._limitation_hits(pool_b, only_all, limit=99)
    sb = material_stats(mb_new, pool_b)
    checks.append(("稀有词否定句：新旧都收得到", len(mb_old) >= 1 and len(mb_new) >= 1,
                   (len(mb_old), len(mb_new))))
    checks.append(("留下的料 survey 核得回（support>=1）",
                   bool(sb) and all(r["support"] >= 1 for r in sb),
                   [r["support"] for r in sb]))
    checks.append(("留下的料有反证标记（against>=1）",
                   bool(sb) and all(r["against"] >= 1 for r in sb),
                   [r["against"] for r in sb]))

    def offered(mod, material, *args):
        tail = mod._counterev_facts(material, *args).split("单句可用的词有：", 1)[-1]
        return [w.strip() for w in tail.split("）", 1)[0].split(",") if w.strip()]

    rare_b = evidence.rare_tokens(pool_b)
    new_terms, old_terms = offered(new, mb_new, pool_b), offered(old, ma_old)
    checks.append(("新口径递给模型的可抄词非空且全是稀有词",
                   bool(new_terms) and all(t in rare_b for t in new_terms), new_terms[:8]))
    checks.append(("同一把尺下，旧口径确实递过抄不动的词（差值不是假的）",
                   bool(old_terms) and all(t not in evidence.rare_tokens(pool_a)
                                           for t in old_terms), old_terms[:8]))
    print("=== --selfcheck（合成池）===")
    bad = 0
    for name, ok, got in checks:
        print(f"  {'✅' if ok else '❌'} {name}　实得 {got}")
        bad += 0 if ok else 1
    print(f"自检 {len(checks) - bad}/{len(checks)} 通过")
    return 1 if bad else 0


def main() -> int:
    sha = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
           for p in (OLD_PATH, NEW_PATH) if os.path.isfile(p)}
    old = load(OLD_PATH, "report_old_mod")
    new = load(NEW_PATH, "report_new_mod")
    print(f"旧 {OLD_PATH} {sha.get(OLD_PATH, '读不到')}｜新 {NEW_PATH} {sha.get(NEW_PATH, '读不到')}"
          f"｜evidence.MIN_TOKENS={evidence.MIN_TOKENS}")
    if len(set(sha.values())) == 1:
        print("❌ 新旧两份 report.py 字节相同 —— 没有对照，任何「差异」都是假的。")
        return 2
    if "--selfcheck" in sys.argv:
        return selfcheck(old, new)
    # 命令行里的非数字参数当作**追加**主题（不是替换）：换一批池子才能区分
    # "这道门槛在真实池上普遍空转"与"恰好这三个池子没触发"——同三个池复跑只证明确定性。
    extra = [a for a in sys.argv[1:] if a != "--selfcheck" and not a.isdigit()]
    topics = TOPICS + [t for t in extra if t not in TOPICS]
    if extra:
        print(f"追加主题 {len(extra)} 个（合计 {len(topics)} 个池子）——这些是**另找的题目**，"
              "不在这三个线上情景里，只用来问「这道门槛会不会咬」，不用来改落地率结论")

    pools = [one_pool(old, new, t) for t in topics]
    j = [p for p in pools if p.get("judgeable")]
    print("\n=== 汇总 ===")
    if not j:
        print("三个池全部判不了（拿不到够大的池子）—— 没有数可印，不是 0 断供")
        return 2
    starved = [p["topic"] for p in j if p["旧_产品"] and not p["新_产品"]]
    misaligned = [(p["topic"], r["idx"]) for p in j for r in p["新_产品"] if r["support"] == 0]
    print(f"可判池 {len(j)}/{len(pools)}")
    print(f"**断供池（旧 limit=2 有料、新没有）= {len(starved)}/{len(j)}** {starved}")
    old_cut = sum(len(p["旧_全量"]) - len(p["新_全量"]) for p in j)
    cut_useless = sum(1 for p in j for r in p["旧_全量"]
                      if r["support"] == 0 and r["idx"] not in
                      {x["idx"] for x in p["新_全量"]})
    print(f"被砍料 {old_cut} 条，其中「本来就核不回」{cut_useless} 条"
          f"（={'100%' if old_cut == cut_useless else f'{cut_useless / old_cut:.0%}' if old_cut else '—'}）"
          "　——只有这一维能说明砍的是废柴还是好料")
    thin = [r for p in j for r in p["新_产品"] if r["window_rare"] < evidence.MIN_TOKENS]
    usable = sum(1 for p in j for r in p["新_产品"] if r["window_rare"] >= evidence.MIN_TOKENS)
    print(f"新料（产品口径）共 {sum(len(p['新_产品']) for p in j)} 条："
          f"连续窗口抄得动 {usable} 条｜抄不动 {len(thin)} 条"
          f"｜核不回 {len(misaligned)} 条")
    if misaligned:
        print(f"❌ 门槛与尺子没对齐：新料里有 support=0 的 {misaligned} —— 这次修得不完整，RC=5")
        return 5
    if starved:
        print("❌ 有池断供：闸门在这些池上从此不再注入。放宽门槛或换料源再部署，RC=4")
        return 4
    print("✅ 无断供、无核不回 —— 可以部署；但「窗口抄不动」的条数就是残余缺口，"
          "别把落地率没涨全算到模型头上")
    if usable == 0:
        print("⚠️ 一条抄得动的都没有：门槛过了、连续窗口没过，注入仍然会退回 —— "
              "这说明该改的是选材口径（按窗口选），不是模型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
