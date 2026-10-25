"""线上 llm_topic 对英语提问返回空串，是**哪一道校验**判掉的？（容器内跑，零落库，模型调用 ≤3 次）

轮 146 的 `probe_topic_channel.py` 量到：通道活着（中文正对照 → topic=「钓鱼网站检测」）、
三条英语提问 0/3 出 topic、且**包装层没捕获到任何异常** ⇒ 模型有响应，是产品自己的校验把它判成空。
成因不同，处置完全不同，所以不能停在"英语抽不出 topic"：
  R1 模型把主题**译成中文**回答 ⇒ 撞上 `extractor.py:174-176` 的「逐字校验」（中文汉字必须原话里出现过）
     ⇒ 这是设计意图（防补写术语）与英语输入的正面冲突，产品要支持英文就得改这道校验的口径；
  R2 模型给的词带逗号/问号等标点 ⇒ 撞 `extractor.py:173` 的标点表；
  R3 模型给太长（>20 字）或空/非法 JSON ⇒ 撞长度或解析；
  R4 三道都没碰、topic 本来就非空 ⇒ 那就是 `llm_topic` 之外的环节丢的（回头查 apply_update）。

口径：**判定一律用产品自己的函数**（`extractor.llm_topic` 决定空/非空，这条不改），
本探针只额外问一次模型「原始返回是什么」，再用同一批校验规则解释它为什么被判掉——
分类是我为解释用的，结论（topic 空不空）必须由产品给。为此设 C0 对账：
  C0 中文正对照：产品判定非空 且 我复刻的三道校验也判"能过" ⇒ 解释器与产品同向；不同向则本场作废（RC=2）。
退出码：0=R1/R2/R3 有明确成因｜2=C0 对账不过｜3=模型原始返回本身就是空/非法。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

POOL_ARG = "/tmp/g132_external_questions_pool.json"
MAX_CALLS = 3
POS_ZH = "我想研究钓鱼网站检测，第一次做科研，目标发一篇SCI，看近两年的"

# 与 extractor.py 的校验逐条对齐（长度、标点表、中文壳词/填充词、逐字性）
PUNCT = re.compile(r"[，。,、！!？?；;：:/\\]")


def classify(topic_raw: str, src: str) -> str:
    """把模型的原始 topic 归到互斥的 R1..R4（只做解释，不改判空与否）。"""
    t = (topic_raw or "").strip()
    if not t:
        return "R3-模型给空/非法 JSON"
    if not 2 <= len(t) <= 20:
        return f"R3-长度 {len(t)} 越界"
    if PUNCT.search(t):
        return "R2-标点"
    if [c for c in re.findall(r"[一-鿿]", t) if c not in src]:
        return "R1-模型译成中文，撞逐字校验"
    return "R4-三道都该过，topic 却丢了（查壳词/填充词或 llm_topic 之外）"


def main() -> int:
    import llm
    from companion.intent import extractor

    rows = json.loads(pathlib.Path(POOL_ARG).read_text(encoding="utf-8"))
    en = [r.get("user_text", "").strip() for r in rows if r.get("user_text", "").strip()][:2]
    print(f"样本：英语提问 {len(en)} 条（同一份 g132 池，与 probe_topic_channel 同一取法）")

    asked = {"n": 0}

    def ask(text: str) -> str:
        asked["n"] += 1
        if asked["n"] > MAX_CALLS:
            raise RuntimeError(f"超出模型调用上限 {MAX_CALLS}")
        data = llm.run_async(llm.chat_json(extractor._TOPIC_PROMPT.format(text[:400]),
                                           temperature=0.0, max_tokens=120, timeout=25.0, retries=1))
        return str((data or {}).get("topic") or "") if isinstance(data, dict) else ""

    # C0 对账：产品判"非空"，我的解释器也必须判"该通过"，否则解释器与产品不同向 ⇒ 本场不作数
    prod_zh = extractor.llm_topic(POS_ZH)
    raw_zh = ask(POS_ZH)
    cls_zh = classify(raw_zh, POS_ZH)
    print(f"C0 正对照：产品判 topic={prod_zh!r}｜模型原始返回={raw_zh!r}｜我的分类={cls_zh}")
    if not prod_zh or not cls_zh.startswith("R4"):
        print("判读：C0 对账不过（产品判非空，解释器却没判成「该通过」）⇒ 本场解释不作数（RC=2）")
        return 2

    tally: dict[str, int] = {}
    for t in en:
        prod = extractor.llm_topic(t)          # 权威判定：产品自己给的空/非空
        raw = "" if prod else ask(t)           # 只在产品判空时才多花一次调用去问原始返回
        cls = "R0-产品放行了" if prod else classify(raw, t)
        tally[cls] = tally.get(cls, 0) + 1
        print(f"  产品判 topic={prod!r}｜模型原始={raw!r}｜分类={cls}｜{t[:52]}")

    print(f"计数：{tally}")
    print(f"本场模型调用数：产品侧 REAL_CALLS 增量={llm.REAL_CALLS}（含 llm_topic 内部那些）｜"
          f"探针直接问模型 {asked['n']} 次（上限 {MAX_CALLS}）")
    if llm.REAL_CALLS > MAX_CALLS + 3:
        print(f"判读：调用数 {llm.REAL_CALLS} 超出预登记预算（探针直问 ≤{MAX_CALLS} + llm_topic 内部 ≤3）⇒ 作废（RC=2）")
        return 2
    n_r1 = tally.get("R1-模型译成中文，撞逐字校验", 0)
    if n_r1 == len(en):
        print("判读：**R1 全部命中**——模型对英语提问是用中文作答的，于是撞上「只能用原话里出现过的词」这道"
              "防补写校验（extractor.py:174-176）⇒ 这是产品设计与英语输入的正面冲突，"
              "不是 provider 故障、不是这批提问写得含糊（RC=0）")
    else:
        print(f"判读：R1 只占 {n_r1}/{len(en)}，成因分布见上面的计数行 ⇒ **不能**把「模型翻译」"
              "当成这批提问的统一成因（RC=0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
