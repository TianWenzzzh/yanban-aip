"""零模型调用：把云端记录下来的两个「模型原始 topic」按产品自己的校验链**逐道重放**，看谁判掉的。

为什么要有这一步：`probe_topic_reject_reason.py` 的 R 分类是我为「解释」复刻的三道校验，
它**漏了两道、也没按产品的顺序判**——正对照恰好三道同向，所以 C0 没抓到这个不完整。
产品的实际顺序是 `extractor.py:173-178`：长度/标点 → 壳词 `_topic_is_shell` → 填充词 `_TOPIC_FILLER`
→ 逐字校验 `invented`。这里只调产品自己的函数与常量，不复制它的判断。
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path[:0] = [str(pathlib.Path("E:/AIC智能体迁移包/code/yanban"))]

from companion.intent import extractor  # noqa: E402

# (原话, 模型原始返回的 topic, 云端那次产品给的判定)
CASES = [
    ("How should I deal with becoming discouraged as a graduate student?", "研究生挫败感处理", ""),
    ("I don't want to kill any more mice, but my advisor insists on more experiments.", "mice killing", ""),
    ("我想研究钓鱼网站检测，第一次做科研，目标发一篇SCI，看近两年的", "钓鱼网站检测", "钓鱼网站检测"),
]

PUNCT = re.compile(r"[，。,、！!？?；;：:/\\]")   # 与 extractor.py:173 同一张标点表


def first_guard(topic: str, src: str) -> str:
    """按产品 `llm_topic` 的**同一顺序**走一遍，报第一道开火的闸门。"""
    if not 2 <= len(topic) <= 20 or PUNCT.search(topic):
        return "第一道：长度或标点（extractor.py:173）"
    if extractor._topic_is_shell(topic):
        return "第二道：壳词 _topic_is_shell（extractor.py:119-123，要求 core 里 CJK ≥2 字）"
    if extractor._TOPIC_FILLER.sub("", topic) != topic:
        return "第三道：填充词前缀 _TOPIC_FILLER（extractor.py:103，会吃掉开头的「研究/做/关于…」）"
    if [c for c in re.findall(r"[一-鿿]", topic) if c not in src]:
        return "第四道：逐字校验 invented（模型补写了原话里没有的汉字）"
    return "全通过 ⇒ 产品应放行"


rc = 0
for src, raw, prod in CASES:
    verdict = first_guard(raw, src)
    agree = (prod == "") == (not verdict.endswith("全通过 ⇒ 产品应放行"))
    print(f"topic={raw!r}｜产品实判={prod!r}｜第一道开火={verdict}｜与产品实判一致={agree}")
    if not agree:
        rc = 2

# 顺手量这条闸门对「纯拉丁 topic」的形状：它按 CJK 计数，不看拉丁词数
probe_cases = ["mice killing", "graph neural networks", "machine learning", "钓鱼网站检测"]
print("—— _topic_is_shell 对各形态的判定（这一行是给「英语永远进不了检索」这句话当证据的）：")
for t in probe_cases:
    print(f"    {t!r:28} 壳词判定={extractor._topic_is_shell(t)}")
print("判读：以上全部零模型调用（只重放产品的纯函数），任一 agreement=False 就 RC=2")
sys.exit(rc)
